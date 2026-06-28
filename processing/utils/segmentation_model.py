import logging
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

try:
    from ultralytics import YOLO
except ImportError:
    logger.warning("Thư viện ultralytics chưa được cài đặt. Vui lòng chạy: pip install ultralytics")
    YOLO = None


class SegmentationModel:
    """
    YOLOv8x-seg: Mô hình Segmentation tốt nhất hiện tại (SOTA).
    Trả về cả bbox + segmentation mask cho từng object được phát hiện.
    """
    def __init__(self, model_path="yolov8x-seg.pt"):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.info(f"Đang tải mô hình Segmentation {model_path}...")
        self.model = None
        if YOLO is None:
            self.logger.error("ultralytics chưa được cài đặt.")
            return
        try:
            self.model = YOLO(model_path)
            self.logger.info("Tải mô hình Segmentation thành công.")
        except Exception as e:
            self.logger.error(f"Không thể tải mô hình: {e}")

    def predict(self, image: Image.Image, conf_threshold=0.5):
        """
        Thực hiện Segmentation trên ảnh PIL.
        Trả về danh sách dict chứa:
          - bbox       : [x1, y1, x2, y2]
          - confidence : float
          - class_id   : int
          - mask       : np.ndarray (H, W) binary mask hoặc None
        """
        if self.model is None:
            self.logger.warning("Mô hình chưa được tải, trả về kết quả rỗng.")
            return []

        results = self.model(image, conf=conf_threshold, verbose=False)
        detections = []

        for result in results:
            boxes = result.boxes
            masks = result.masks  # None nếu model không phải seg

            for idx, box in enumerate(boxes):
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                conf = box.conf[0].item()
                cls_id = int(box.cls[0].item())

                # Trích xuất mask nhị phân nếu có
                mask_arr = None
                if masks is not None and idx < len(masks.data):
                    mask_arr = masks.data[idx].cpu().numpy().astype(np.uint8)

                detections.append({
                    "bbox": [x1, y1, x2, y2],
                    "confidence": conf,
                    "class_id": cls_id,
                    "mask": mask_arr,
                })

        return detections


# Backward-compat alias (labeling.py cũ dùng InferenceModel)
class InferenceModel(SegmentationModel):
    """Alias của SegmentationModel để tương thích ngược với labeling.py."""
    def __init__(self, model_path="yolov8x-seg.pt"):
        super().__init__(model_path=model_path)

    def predict(self, image: Image.Image, conf_threshold=0.5):
        return super().predict(image, conf_threshold)
