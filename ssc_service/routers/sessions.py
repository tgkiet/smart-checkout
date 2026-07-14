"""
routers/sessions.py — CRUD phiên tính toán (Session management).

Endpoints:
  POST   /sessions/                         → Tạo phiên mới
  GET    /sessions/{session_id}             → Lấy trạng thái phiên
  DELETE /sessions/{session_id}             → Xóa phiên
  GET    /sessions/                         → Liệt kê tất cả phiên (admin/debug)

  POST   /sessions/{session_id}/images      → Upload ảnh (multi-file)
  DELETE /sessions/{session_id}/images/{image_id}  → Xóa 1 ảnh khỏi phiên

  POST   /sessions/{session_id}/checkout    → Checkout tất cả ảnh PENDING song song
  POST   /sessions/{session_id}/checkout/{image_id}  → Checkout 1 ảnh cụ thể

  GET    /sessions/{session_id}/cart        → Lấy giỏ hàng + tổng tiền
  POST   /sessions/{session_id}/confirm     → Xác nhận thanh toán (đổi status → CHECKED_OUT)
"""
import asyncio
import logging
from typing import List

from fastapi import APIRouter, File, HTTPException, Path, UploadFile

from config import SESSION_MAX_IMAGES, SESSION_MAX_WORKERS
from models.session import ImageItem, ImageStatus, SessionStatus, session_store
from services.checkout_service import checkout_one

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["sessions"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _session_response(session):
    """Serialize session để trả về client."""
    return {
        "session_id": session.session_id,
        "status": session.status,
        "created_at": session.created_at,
        "summary": session.summary(),
        "images": [
            {
                "image_id": img.image_id,
                "filename": img.filename,
                "status": img.status,
                "uploaded_at": img.uploaded_at,
                "processed_at": img.processed_at,
                "product": img.product.dict() if img.product else None,
                "error": img.error,
            }
            for img in session.images.values()
        ],
    }


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------
@router.post("/", summary="Tạo phiên tính toán mới")
async def create_session():
    session = session_store.create()
    return {"session_id": session.session_id, "status": session.status, "created_at": session.created_at}


@router.get("/", summary="Liệt kê tất cả phiên (debug)")
async def list_sessions():
    return session_store.list_all()


@router.get("/{session_id}", summary="Lấy trạng thái phiên")
async def get_session(session_id: str = Path(...)):
    return _session_response(session_store.get_or_404(session_id))


@router.delete("/{session_id}", summary="Xóa phiên")
async def delete_session(session_id: str = Path(...)):
    session_store.get_or_404(session_id)  # 404 nếu không tồn tại
    session_store.delete(session_id)
    return {"message": f"Session '{session_id}' deleted"}


# ---------------------------------------------------------------------------
# Image management
# ---------------------------------------------------------------------------
@router.post("/{session_id}/images", summary="Upload ảnh vào phiên (multi-file)")
async def upload_images(
    session_id: str = Path(...),
    files: List[UploadFile] = File(...),
):
    session = session_store.get_or_404(session_id)

    if session.status == SessionStatus.CHECKED_OUT:
        raise HTTPException(status_code=400, detail="Session đã thanh toán, không thể upload thêm ảnh")

    if len(session.images) + len(files) > SESSION_MAX_IMAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Vượt quá giới hạn {SESSION_MAX_IMAGES} ảnh mỗi phiên",
        )

    added = []
    for f in files:
        contents = await f.read()
        item = ImageItem(filename=f.filename or "unknown.jpg")
        # Lưu raw bytes vào item (sẽ dùng khi checkout)
        # NOTE: với production scale, lưu vào MinIO/Redis thay vì in-memory
        item._raw_bytes = contents  # type: ignore[attr-defined]
        session.images[item.image_id] = item
        added.append({"image_id": item.image_id, "filename": item.filename})

    return {"added": added, "total_images": len(session.images)}


@router.delete("/{session_id}/images/{image_id}", summary="Xóa ảnh khỏi phiên")
async def delete_image(
    session_id: str = Path(...),
    image_id: str = Path(...),
):
    session = session_store.get_or_404(session_id)
    img = session.images.get(image_id)
    if img is None:
        raise HTTPException(status_code=404, detail=f"Image '{image_id}' not found")
    if img.status == ImageStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="Ảnh đang được xử lý, không thể xóa")

    del session.images[image_id]
    return {"message": f"Image '{image_id}' removed", "total_images": len(session.images)}


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------
@router.post("/{session_id}/checkout", summary="Checkout tất cả ảnh PENDING (song song)")
async def checkout_session(session_id: str = Path(...)):
    """
    Checkout song song tối đa SESSION_MAX_WORKERS ảnh cùng lúc.
    Mở rộng: tăng SESSION_MAX_WORKERS env var nếu hardware mạnh hơn.
    """
    session = session_store.get_or_404(session_id)

    pending_items = [
        (img_id, img)
        for img_id, img in session.images.items()
        if img.status == ImageStatus.PENDING
    ]

    if not pending_items:
        raise HTTPException(status_code=400, detail="Không có ảnh nào ở trạng thái PENDING")

    session.status = SessionStatus.PROCESSING

    # Semaphore để giới hạn concurrency
    sem = asyncio.Semaphore(SESSION_MAX_WORKERS)

    async def _run_one(img_id: str, img: ImageItem):
        raw = getattr(img, "_raw_bytes", None)
        if raw is None:
            img.status = ImageStatus.FAILED
            img.error = "Image data not found in session"
            return img
        async with sem:
            return await checkout_one(session, img_id, raw)

    tasks = [_run_one(img_id, img) for img_id, img in pending_items]
    await asyncio.gather(*tasks, return_exceptions=True)

    # Cập nhật session status
    summary = session.summary()
    if summary["processing"] == 0 and summary["pending"] == 0:
        session.status = SessionStatus.COMPLETED
    else:
        session.status = SessionStatus.OPEN

    return {
        "message": f"Hoàn tất checkout {len(pending_items)} ảnh",
        "summary": summary,
        "total_price": session.total_price(),
    }


@router.post("/{session_id}/checkout/{image_id}", summary="Checkout 1 ảnh cụ thể")
async def checkout_single_image(
    session_id: str = Path(...),
    image_id: str = Path(...),
):
    session = session_store.get_or_404(session_id)
    img = session.images.get(image_id)
    if img is None:
        raise HTTPException(status_code=404, detail=f"Image '{image_id}' not found")
    if img.status == ImageStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="Ảnh đang được xử lý")

    raw = getattr(img, "_raw_bytes", None)
    if raw is None:
        raise HTTPException(status_code=500, detail="Image data not available")

    result = await checkout_one(session, image_id, raw)
    return {
        "image_id": image_id,
        "status": result.status,
        "product": result.product.dict() if result.product else None,
        "error": result.error,
    }


# ---------------------------------------------------------------------------
# Cart & Confirm
# ---------------------------------------------------------------------------
@router.get("/{session_id}/cart", summary="Lấy giỏ hàng và tổng tiền")
async def get_cart(session_id: str = Path(...)):
    session = session_store.get_or_404(session_id)
    return {
        "session_id": session_id,
        "status": session.status,
        "items": session.cart_items(),
        "total_price": session.total_price(),
        "summary": session.summary(),
    }


@router.post("/{session_id}/confirm", summary="Xác nhận thanh toán")
async def confirm_checkout(session_id: str = Path(...)):
    session = session_store.get_or_404(session_id)
    if session.status not in (SessionStatus.COMPLETED, SessionStatus.OPEN):
        raise HTTPException(
            status_code=400,
            detail=f"Không thể confirm ở trạng thái '{session.status}'. Hãy checkout ảnh trước.",
        )
    if not session.cart_items():
        raise HTTPException(status_code=400, detail="Giỏ hàng trống, không thể thanh toán")

    session.status = SessionStatus.CHECKED_OUT
    return {
        "message": "Thanh toán thành công!",
        "session_id": session_id,
        "items": session.cart_items(),
        "total_price": session.total_price(),
    }
