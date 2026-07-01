from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import JSONResponse
import io
import base64
import numpy as np
from PIL import Image
import cv2

# Import trực tiếp các model hiện có từ utils
from utils.segmentation_model import SegmentationModel, InferenceModel
from utils.embedding_model import EmbeddingModel

app = FastAPI(title="Smart Checkout Inference API", description="API phục vụ model cho Spark Data Pipeline")

print("Đang khởi tạo các mô hình Deep Learning vào GPU/RAM...")
seg_model = SegmentationModel(model_path="yolov8x-seg.pt")
emb_model = EmbeddingModel(model_name="openai/clip-vit-base-patch32")
inf_model = InferenceModel(model_path="yolov8x.pt") # Dùng cho labeling

@app.post("/api/v1/segment")
async def segment_image(
    image: UploadFile = File(...),
    conf_threshold: float = Form(0.5)
):
    """API dùng cho object_processor.py để lấy bbox và mask."""
    try:
        contents = await image.read()
        img = Image.open(io.BytesIO(contents)).convert("RGB")
        
        detections = seg_model.predict(img, conf_threshold)
        
        processed_dets = []
        for det in detections:
            det_dict = {
                "bbox": det["bbox"],
                "confidence": det["confidence"],
                "class_id": det["class_id"]
            }
            # Nén và encode mask sang Base64 để tiết kiệm băng thông khi gửi qua REST JSON
            if det.get("mask") is not None:
                mask_uint8 = (det["mask"] * 255).astype(np.uint8)
                success, buffer = cv2.imencode('.png', mask_uint8)
                if success:
                    det_dict["mask_base64"] = base64.b64encode(buffer).decode("utf-8")
                
            processed_dets.append(det_dict)
            
        return {"detections": processed_dets}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/predict")
async def predict_image(
    image: UploadFile = File(...),
    conf_threshold: float = Form(0.5)
):
    """API dùng cho labeling.py để lấy bbox (không lấy mask)."""
    try:
        contents = await image.read()
        img = Image.open(io.BytesIO(contents)).convert("RGB")
        
        detections = inf_model.predict(img, conf_threshold)
        
        processed_dets = []
        for det in detections:
            processed_dets.append({
                "bbox": det["bbox"],
                "confidence": det["confidence"],
                "class_id": det["class_id"]
            })
            
        return {"detections": processed_dets}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/embed")
async def embed_image(
    image: UploadFile = File(...)
):
    """API tạo Vector Embedding cho từng Object đã được crop."""
    try:
        contents = await image.read()
        img = Image.open(io.BytesIO(contents)).convert("RGB")
        
        embedding = emb_model.embed(img)
        return {"embedding": embedding}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {"status": "healthy"}
