import io
import requests
from config import API_ENDPOINT

class InferenceService:
    @staticmethod
    def segment(image_bytes: bytes, conf_threshold: float = 0.5):
        try:
            response = requests.post(
                f"{API_ENDPOINT}/segment",
                files={"image": ("image.jpg", image_bytes, "image/jpeg")},
                data={"conf_threshold": conf_threshold},
                timeout=30
            )
            response.raise_for_status()
            return response.json().get("detections", [])
        except Exception as e:
            raise Exception(f"Segmentation API error: {str(e)}")

    @staticmethod
    def embed(cropped_pil_image):
        buf = io.BytesIO()
        cropped_pil_image.save(buf, format="JPEG")
        img_bytes = buf.getvalue()
        
        try:
            response = requests.post(
                f"{API_ENDPOINT}/embed",
                files={"image": ("crop.jpg", img_bytes, "image/jpeg")},
                timeout=30
            )
            response.raise_for_status()
            return response.json().get("embedding", [])
        except Exception as e:
            print(f"Embedding API error: {str(e)}")
            return []
