import logging
from PIL import Image

logger = logging.getLogger(__name__)

class EmbeddingModel:
    """
    Model tạo Embedding vector cho ảnh đã crop.
    Sử dụng CLIP (openai/clip-vit-base-patch32) nếu có, 
    fallback sang torchvision ResNet50 nếu không có transformers.
    """
    def __init__(self, model_name="openai/clip-vit-base-patch32"):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.model_name = model_name
        self._model = None
        self._processor = None
        self._backend = None
        self._load_model()

    def _load_model(self):
        # Thử CLIP trước (độ chính xác cao hơn cho ảnh sản phẩm)
        try:
            from transformers import CLIPProcessor, CLIPModel
            import torch
            self.logger.info(f"Đang tải CLIP model: {self.model_name}...")
            self._model = CLIPModel.from_pretrained(self.model_name)
            self._processor = CLIPProcessor.from_pretrained(self.model_name)
            self._model.eval()
            self._backend = "clip"
            self.logger.info("Tải CLIP model thành công.")
        except ImportError:
            self.logger.warning("transformers chưa cài, fallback sang torchvision ResNet50...")
            self._load_resnet()
        except Exception as e:
            self.logger.error(f"Lỗi tải CLIP: {e}. Fallback sang ResNet50...")
            self._load_resnet()

    def _load_resnet(self):
        try:
            import torch
            import torchvision.models as models
            import torchvision.transforms as T
            self._model = models.resnet50(pretrained=True)
            # Bỏ lớp FC cuối để lấy feature vector 2048-dim
            self._model = torch.nn.Sequential(*list(self._model.children())[:-1])
            self._model.eval()
            self._transform = T.Compose([
                T.Resize((224, 224)),
                T.ToTensor(),
                T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ])
            self._backend = "resnet"
            self.logger.info("Tải ResNet50 thành công.")
        except Exception as e:
            self.logger.error(f"Không thể tải EmbeddingModel: {e}")
            self._backend = None

    def embed(self, image: Image.Image) -> list:
        """
        Tạo embedding vector (list[float]) từ ảnh PIL.
        Trả về list rỗng nếu có lỗi.
        """
        if self._model is None or self._backend is None:
            return []
        try:
            import torch
            if self._backend == "clip":
                inputs = self._processor(images=image, return_tensors="pt", padding=True)
                with torch.no_grad():
                    features = self._model.get_image_features(**inputs)
                # L2 normalize để dùng cosine similarity trong Qdrant
                features = features / features.norm(dim=-1, keepdim=True)
                return features[0].tolist()
            elif self._backend == "resnet":
                tensor = self._transform(image).unsqueeze(0)
                with torch.no_grad():
                    features = self._model(tensor)
                return features.squeeze().tolist()
        except Exception as e:
            self.logger.error(f"Lỗi tạo embedding: {e}")
            return []
