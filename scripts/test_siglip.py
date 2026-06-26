import numpy as np
import typer
import cv2
from pathlib import Path

from src.core.config import load_config
from src.core.logger import get_logger, setup_logger
from src.embedding.siglip_encoder import SigLIPEncoder

app = typer.Typer()
logger = get_logger(__name__)

@app.command()
def run_inference(
    image_path: str = typer.Option(None, "--image", "-i", help="Path to image file to encode"),
    config_path: str = typer.Option("config/settings.yaml", "--config", "-c", help="Path to config yaml file"),
):
    """
    Simple inference script for SigLIP 2 encoder.
    If no image is provided, runs on a dummy black image.
    """
    setup_logger(log_level="INFO")
    config = load_config(config_path)

    # Initialize encoder
    logger.info("Initializing SigLIPEncoder...")
    encoder = SigLIPEncoder(config.embedding)

    if image_path:
        img_path = Path(image_path)
        if not img_path.exists():
            logger.error("Target image does not exist", path=str(img_path))
            raise typer.Exit(code=1)
        
        image = cv2.imread(str(img_path))
        if image is None:
            logger.error("Failed to load image", path=str(img_path))
            raise typer.Exit(code=1)
        logger.info("Loaded image successfully", shape=image.shape)
    else:
        logger.info("No image provided. Using a 224x224 dummy image.")
        image = np.zeros((224, 224, 3), dtype=np.uint8)

    # Encode image
    logger.info("Generating embedding vector...")
    embedding = encoder.encode(image)

    print("\n" + "=" * 50)
    print("SIGLIP 2 INFERENCE RESULT:")
    print(f"Embedding shape: {embedding.shape}")
    print(f"Embedding sample (first 5 elements): {embedding[:5]}")
    print(f"L2 Norm: {np.linalg.norm(embedding):.4f}")
    print("=" * 50 + "\n")

if __name__ == "__main__":
    app()
