import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from transformers import AutoModel, AutoProcessor

from src.core.config import EmbeddingConfig
from src.core.logger import get_logger

logger = get_logger(__name__)


class SigLIPEncoder:
    def __init__(self, config: EmbeddingConfig):
        self.config = config
        self.device = config.device

        if self.device == "cuda" and not torch.cuda.is_available():
            logger.warning("CUDA requested but not available. Falling back to CPU for SigLIP.")
            self.device = "cpu"

        logger.info("Loading SigLIP2 model and processor", model=self.config.model_name_or_path, device=self.device)

        self.processor = AutoProcessor.from_pretrained(self.config.model_name_or_path)

        # Load custom fine-tuned checkpoint if it exists, otherwise load base model
        import os

        checkpoint_dir = self.config.model_checkpoint_path
        if os.path.exists(checkpoint_dir) and any(os.listdir(checkpoint_dir)):
            logger.info("Found fine-tuned checkpoint, loading from local path", path=checkpoint_dir)
            self.model = AutoModel.from_pretrained(checkpoint_dir)
        else:
            logger.info(
                "No fine-tuned checkpoint found. Loading base pre-trained model.", model=self.config.model_name_or_path
            )
            self.model = AutoModel.from_pretrained(self.config.model_name_or_path)

        self.model.to(self.device)

        # Convert to half precision if running on CUDA for speed & VRAM savings
        if self.device == "cuda":
            self.model.half()

        self.model.eval()

    def encode(self, image: np.ndarray) -> np.ndarray:
        """
        Extracts L2-normalized embedding vector for a single image.

        Args:
            image: BGR or RGB image as numpy array (HxWxC)

        Returns:
            np.ndarray: 768-dimensional float32 vector, L2 normalized
        """
        embeddings = self.encode_batch([image])
        return embeddings[0]

    def encode_batch(self, images: list[np.ndarray]) -> np.ndarray:
        """
        Extracts L2-normalized embedding vectors for a batch of images.

        Args:
            images: list of images as numpy arrays

        Returns:
            np.ndarray: Matrix of shape (N, 768) containing L2 normalized embeddings
        """
        if not images:
            return np.empty((0, self.config.embedding_dim), dtype=np.float32)

        # Convert images (usually BGR opencv format) to RGB PIL Images
        pil_images = []
        for img in images:
            # OpenCV BGR to RGB
            img_rgb = img[:, :, ::-1]
            pil_images.append(Image.fromarray(img_rgb))

        batch_size = self.config.batch_size
        all_embeddings = []

        # Process in chunks of batch_size
        for i in range(0, len(pil_images), batch_size):
            chunk = pil_images[i : i + batch_size]

            # Preprocess
            inputs = self.processor(images=chunk, return_tensors="pt")
            pixel_values = inputs["pixel_values"].to(self.device)

            if self.device == "cuda":
                pixel_values = pixel_values.half()

            with torch.no_grad():
                # Get image features from SiglipModel
                # Depending on transformers version, model might return a Dict or a specific Output class.
                # get_image_features uses the projection head to produce 768-dim features.
                if hasattr(self.model, "get_image_features"):
                    features = self.model.get_image_features(pixel_values=pixel_values)
                    if hasattr(features, "pooler_output"):
                        features = features.pooler_output
                else:
                    # Fallback to vision_model pooler output if not full model
                    vision_outputs = self.model.vision_model(pixel_values=pixel_values)
                    features = vision_outputs.pooler_output
                    if hasattr(self.model, "visual_projection"):
                        features = self.model.visual_projection(features)

                # Perform L2 normalization
                features_norm = F.normalize(features, p=2, dim=-1)

                # Move to CPU and convert to float32
                embeddings_np = features_norm.cpu().float().numpy()
                all_embeddings.append(embeddings_np)

        return np.vstack(all_embeddings)
