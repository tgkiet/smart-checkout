import logging
from PIL import Image

logger = logging.getLogger(__name__)

import os

class EmbeddingModel:
    """
    Model tạo Embedding vector cho ảnh đã crop.
    Sử dụng CLIP (offline).
    """
    def __init__(self, model_name=None):
        self.logger = logging.getLogger(self.__class__.__name__)
        # Xử lý đường dẫn model khi chạy trên Spark worker (__file__ bị trỏ vào /tmp)
        if model_name is None or model_name == "openai/clip-vit-base-patch32":
            docker_path = "/app/models/clip-vit-base-patch32"
            local_path = "/home/quockhanh/smart-checkout/models/clip-vit-base-patch32"
            if os.path.exists(docker_path):
                self.model_name = docker_path
            elif os.path.exists(local_path):
                self.model_name = local_path
            else:
                self.model_name = "openai/clip-vit-base-patch32"
        else:
            self.model_name = model_name

        self._model = None
        self._processor = None
        self._backend = None
        self._load_model()

    def _load_model(self):
        try:
            from transformers import CLIPProcessor, CLIPModel
            import torch
            self.logger.info(f"Đang tải CLIP model: {self.model_name}...")
            self._model = CLIPModel.from_pretrained(self.model_name)
            self._processor = CLIPProcessor.from_pretrained(self.model_name)
            self._model.eval()
            self._backend = "clip"
            self.logger.info("Tải CLIP model thành công.")
        except Exception as e:
            self.logger.error(f"Lỗi tải CLIP: {e}")
            self._backend = None
            raise e

    def embed(self, image: Image.Image) -> list:
        """
        Tạo embedding vector (list[float]) từ ảnh PIL.
        Trả về list rỗng nếu có lỗi.
        """
        if self._model is None or self._backend is None:
            return []
        try:
            import torch
            inputs = self._processor(images=image, return_tensors="pt", padding=True)
            with torch.no_grad():
                features = self._model.get_image_features(**inputs)
            
            # Transformers >= 5.x có thể trả về BaseModelOutputWithPooling thay vì Tensor
            if not isinstance(features, torch.Tensor):
                if hasattr(features, 'pooler_output'):
                    features = features.pooler_output
                elif hasattr(features, 'image_embeds'):
                    features = features.image_embeds
                elif isinstance(features, tuple):
                    features = features[0]

            # L2 normalize để dùng cosine similarity trong Qdrant
            features = features / features.norm(dim=-1, keepdim=True)
            return features[0].tolist()
        except Exception as e:
            self.logger.error(f"Lỗi tạo embedding: {e}")
            return []
