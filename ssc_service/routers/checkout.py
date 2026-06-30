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
            return {"items": [], "total_price": 0.0, "message": "Không tìm thấy sản phẩm nào trong ảnh"}
            
        items = []
        total_price = 0.0
        
        # 2. Xử lý từng detection
        for det in detections:
            # Crop/Mask
            cropped = process_detection(pil_img, img_np, det)
                
            # 3. Gọi API Embedding
            embedding = InferenceService.embed(cropped)
            if not embedding:
                continue
                
            # 4. Search Qdrant
            matches = vector_db.search(embedding, limit=1, threshold=0.6)
            
            if matches:
                best_match = matches[0]
                items.append(best_match)
                total_price += best_match["price"]
                    
        return {
            "items": items,
            "total_price": total_price,
            "message": "Thành công"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
