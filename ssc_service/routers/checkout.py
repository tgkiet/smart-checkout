import io
import numpy as np
from PIL import Image
from fastapi import APIRouter, File, UploadFile, HTTPException
from services.inference import InferenceService
from services.vector_db import vector_db
from utils.image import process_detection

router = APIRouter(prefix="/checkout", tags=["checkout"])

@router.post("/")
async def process_checkout(image: UploadFile = File(...)):
    if vector_db.client is None:
        raise HTTPException(status_code=500, detail="Qdrant client is not initialized")
        
    try:
        contents = await image.read()
        pil_img = Image.open(io.BytesIO(contents)).convert("RGB")
        img_np = np.array(pil_img)
        
        # 1. Gọi API Segment
        try:
            detections = InferenceService.segment(contents)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
            
        if not detections:
            print("DEBUG: Không tìm thấy object nào qua Segmentation. Kích hoạt chế độ Fallback: Dùng toàn bộ ảnh để quét.")
            # Tạo một bounding box giả (bao trọn toàn bộ ảnh)
            height, width, _ = img_np.shape
            detections = [{
                "box": [0, 0, width, height],
                "confidence": 1.0,
                "class_name": "fallback_object"
            }]
            
        items = []
        total_price = 0.0
        
        # 2. Xử lý từng detection
        for i, det in enumerate(detections):
            # Crop/Mask
            cropped = process_detection(pil_img, img_np, det)
                
            # 3. Gọi API Embedding
            embedding = InferenceService.embed(cropped)
            if not embedding:
                continue
                
            # 4. Search Qdrant với threshold thấp (lỏng) để dễ nhận diện hơn
            matches = vector_db.search(embedding, limit=1, threshold=0.1)
            
            if matches:
                best_match = matches[0]
                items.append(best_match)
                total_price += best_match["price"]
                
        # Gom nhóm sản phẩm giống nhau
        grouped_items = {}
        for item in items:
            sku = item.get("sku") or item.get("name")
            if sku in grouped_items:
                grouped_items[sku]["quantity"] += 1
                grouped_items[sku]["subtotal"] += item["price"]
            else:
                grouped_items[sku] = {
                    "sku": item.get("sku", ""),
                    "name": item.get("name", "Unknown Product"),
                    "price": item["price"],
                    "quantity": 1,
                    "subtotal": item["price"],
                    "platform": item.get("platform", "")
                }
                    
        return {
            "items": list(grouped_items.values()),
            "total_price": total_price,
            "message": "Thành công"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
