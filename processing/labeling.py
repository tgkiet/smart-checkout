import io
import logging
import requests
from PIL import Image
from utils.segmentation_model import InferenceModel

class DataLabeler:
    """
    Module Đánh Nhãn (Labeling).
    Nhiệm vụ: Gọi Model để trích xuất vật thể từ ảnh (Inference), sau đó 
    cắt (crop) vùng vật thể và gán nhãn SKU/Product_ID dựa trên logic.
    """
    def __init__(self, config=None):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.config = config or {}
        
        self.use_api = self.config.get("use_api", False)
        self.api_endpoint = self.config.get("api_endpoint", "http://localhost:8000/api/v1")

        if self.use_api:
            self.logger.info(f"Sử dụng API Inference tại: {self.api_endpoint}")
            self.model = None
        else:
            self.logger.info("Sử dụng Local Inference Model")
            self.model = InferenceModel(model_path="yolov8x.pt")

    def process_and_label(self, row_dict):
        """
        Hàm xử lý cho từng dòng dữ liệu lấy từ DB preprocessing.
        Đầu ra là danh sách các sản phẩm con (đã cắt) kèm nhãn.
        """
        image_bytes = row_dict.get("image_data")
        if not image_bytes:
            return []

        try:
            # Chuyển đổi Binary từ DB thành dạng PIL Image để model đọc
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            
            # 1. Gọi model tốt nhất để inference (chỉ lấy vật thể rõ ràng, conf >= 0.5)
            if self.use_api:
                try:
                    response = requests.post(
                        f"{self.api_endpoint}/predict",
                        files={"image": ("image.jpg", image_bytes, "image/jpeg")},
                        data={"conf_threshold": 0.5},
                        timeout=30
                    )
                    response.raise_for_status()
                    detections = response.json().get("detections", [])
                except Exception as e:
                    self.logger.error(f"Lỗi khi gọi API dự đoán: {e}")
                    detections = []
            else:
                detections = self.model.predict(image, conf_threshold=0.5)
            
            labeled_results = []
            
            # Gán nhãn - Với Single-Product, nhãn là SKU hoặc Product ID của dòng dữ liệu gốc
            original_sku = row_dict.get("sku") or row_dict.get("product_id", "unknown_sku")
            original_id = str(row_dict.get("_id", "unknown_id"))
            
            for idx, det in enumerate(detections):
                bbox = det["bbox"]
                
                # 2. Cắt ảnh dựa vào tọa độ (Crop)
                # Bbox là mảng [x1, y1, x2, y2]
                cropped_img = image.crop((bbox[0], bbox[1], bbox[2], bbox[3]))
                
                # Chuyển ảnh đã cắt thành bytes để truyền đi hoặc lưu lại
                img_byte_arr = io.BytesIO()
                cropped_img.save(img_byte_arr, format='JPEG')
                cropped_bytes = img_byte_arr.getvalue()
                
                # 3. Tổng hợp thành Record mới
                labeled_results.append({
                    "original_id": original_id,          # Truy vết về metadata gốc
                    "sub_id": f"{original_id}_obj{idx}", # ID định danh của sản phẩm được cắt ra
                    "sku_label": original_sku,           # ĐÁNH NHÃN CHÍNH THỨC
                    "bbox": bbox,
                    "confidence": det["confidence"],
                    "cropped_image_data": cropped_bytes  # Dữ liệu ảnh nhị phân đã cắt
                })
            
            return labeled_results
            
        except Exception as e:
            self.logger.error(f"Lỗi khi đánh nhãn tại bản ghi {row_dict.get('_id', 'unknown')}: {e}")
            return []
