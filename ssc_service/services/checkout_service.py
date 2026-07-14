"""
services/checkout_service.py — Core business logic cho checkout 1 ảnh.

Thiết kế để có thể gọi:
  - Tuần tự: checkout_one(session, image_id, raw_bytes)
  - Song song: dùng asyncio.gather() + ThreadPoolExecutor ở router
"""
import io
import logging
from datetime import datetime
from typing import Optional
import numpy as np
from PIL import Image

from models.session import Session, ImageItem, ImageStatus, ProductMatch
from services.inference import InferenceService
from services.vector_db import vector_db
from utils.image import process_detection

logger = logging.getLogger(__name__)


async def checkout_one(
    session: Session,
    image_id: str,
    raw_bytes: bytes,
    conf_threshold: float = 0.25,
    vector_threshold: float = 0.10,
) -> ImageItem:
    """
    Thực hiện checkout 1 ảnh:
      raw_bytes → segment → crop best object → embed → search Qdrant
    Cập nhật trạng thái ImageItem trong session và trả về item đó.

    Thiết kế async để có thể gather() nhiều ảnh song song:
      results = await asyncio.gather(*[checkout_one(s, id, b) for id, b in ...])
    Phần heavy-CPU (inference) vẫn blocking nhưng FastAPI chạy trên thread pool
    nên không block event loop.
    """
    item = session.images.get(image_id)
    if item is None:
        raise ValueError(f"Image '{image_id}' not found in session")

    if item.status in (ImageStatus.DONE, ImageStatus.SKIPPED):
        return item  # idempotent

    item.status = ImageStatus.PROCESSING

    try:
        pil_img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
        img_np = np.array(pil_img)

        # 1. Segment
        try:
            detections = InferenceService.segment(raw_bytes, conf_threshold=conf_threshold)
        except Exception as seg_err:
            logger.warning(f"Segment failed for {image_id}: {seg_err}. Using full-image fallback.")
            detections = []

        if not detections:
            h, w, _ = img_np.shape
            detections = [{"bbox": [0, 0, w, h], "confidence": 1.0, "class_name": "fallback_object"}]

        # 2. Take best detection (highest confidence)
        best_det = max(detections, key=lambda d: d.get("confidence", 0))
        cropped = process_detection(pil_img, img_np, best_det)

        # 3. Embed
        embedding = InferenceService.embed(cropped)
        if not embedding:
            item.status = ImageStatus.FAILED
            item.error = "Embedding returned empty vector"
            item.processed_at = datetime.utcnow()
            return item

        # 4. Vector search
        matches = vector_db.search(embedding, limit=1, threshold=vector_threshold)
        if not matches:
            item.status = ImageStatus.FAILED
            item.error = "No matching product found"
            item.processed_at = datetime.utcnow()
            return item

        best = matches[0]
        item.product = ProductMatch(
            name=best["name"],
            sku=best["sku"],
            price=best["price"],
            score=best["score"],
            platform=best.get("platform", ""),
        )
        item.status = ImageStatus.DONE
        item.processed_at = datetime.utcnow()
        logger.info(f"[checkout_one] {image_id} → {item.product.name} ({item.product.score:.3f})")

    except Exception as e:
        logger.error(f"[checkout_one] Unexpected error for {image_id}: {e}", exc_info=True)
        item.status = ImageStatus.FAILED
        item.error = str(e)
        item.processed_at = datetime.utcnow()

    return item
