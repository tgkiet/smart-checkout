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
        print(f"DEBUG: Found {len(detections)} detections from segmentation")
        for i, det in enumerate(detections):
            # Crop/Mask
            cropped = process_detection(pil_img, img_np, det)
                
            # 3. Gọi API Embedding
            embedding = InferenceService.embed(cropped)
            if not embedding:
                print(f"DEBUG: Detection {i} failed to get embedding")
                continue
                
            # 4. Search Qdrant
            # Temporary lower threshold to 0.1 to debug similarity scores
            matches = vector_db.search(embedding, limit=3, threshold=0.1)
            print(f"DEBUG: Detection {i} returned {len(matches)} matches from Qdrant")
            
            if matches:
                # Print out scores of top matches to see how far off it is
                for j, match in enumerate(matches):
                    print(f"  -> Match {j}: score={match['score']:.4f}, name={match['name']}")
                
                best_match = matches[0]
                if best_match['score'] > 0.6:
                    items.append(best_match)
                    total_price += best_match["price"]
                else:
                    print(f"DEBUG: Best match score {best_match['score']:.4f} is lower than 0.6 threshold")
                    
        return {
            "items": items,
            "total_price": total_price,
            "message": "Thành công"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
