import numpy as np
import cv2
import typer
from pathlib import Path

from src.core.config import load_config
from src.core.logger import get_logger, setup_logger
from src.detection.yolo_segmentor import YOLOSegmentor
from src.detection.mask_processor import MaskProcessor
from src.embedding.siglip_encoder import SigLIPEncoder
from src.utils.visualization import draw_detections

app = typer.Typer()
logger = get_logger(__name__)

@app.command()
def run_pipeline(
    image_path: str = typer.Argument(..., help="Path to input image file to process"),
    config_path: str = typer.Option("config/settings.yaml", "--config", "-c", help="Path to config yaml file"),
    output_dir: str = typer.Option("output", "--output", "-o", help="Directory to save output files"),
):
    """
    Test script to run both YOLOv11-seg detection and SigLIP 2 embedding inference.
    1. Detects objects and segmentations using YOLOv11-seg.
    2. Extracts masked crops (224x224) using MaskProcessor.
    3. Generates 768-dim embedding vectors using SigLIPEncoder.
    """
    setup_logger(log_level="INFO")
    config = load_config(config_path)

    img_path = Path(image_path)
    if not img_path.exists():
        logger.error("Target image does not exist", path=str(img_path))
        raise typer.Exit(code=1)

    out_dir = Path(output_dir)
    out_dir.mkdir(exist_ok=True)

    # 1. Load image
    logger.info("Loading image...", path=str(img_path))
    frame = cv2.imread(str(img_path))
    if frame is None:
        logger.error("Failed to load image", path=str(img_path))
        raise typer.Exit(code=1)

    # 2. Initialize Models
    logger.info("Initializing YOLOv11-seg segmentor...")
    try:
        segmentor = YOLOSegmentor(config.detection)
    except Exception as e:
        logger.error("Failed to initialize YOLO model. Make sure weight file exists at model_path in settings.yaml", error=str(e))
        raise typer.Exit(code=1)

    logger.info("Initializing MaskProcessor...")
    mask_processor = MaskProcessor()

    logger.info("Initializing SigLIPEncoder...")
    encoder = SigLIPEncoder(config.embedding)

    # 3. Step 1: Detect objects
    logger.info("Running YOLOv11-seg detection...")
    detections = segmentor.detect(frame)
    logger.info("Detection complete", count=len(detections))

    if not detections:
        logger.warning("No objects detected in the image.")
        return

    # Draw and save detection overlay
    vis_detection = draw_detections(frame, detections)
    det_out_path = out_dir / "detections_overlay.png"
    cv2.imwrite(str(det_out_path), vis_detection)
    logger.info("Saved detection visualization overlay", path=str(det_out_path))

    # 4. Step 2: Extract clean, masked crops
    logger.info("Extracting masked object crops...")
    crops = mask_processor.extract_batch(frame, detections)

    # 5. Step 3: Run embedding inference
    logger.info("Generating SigLIP 2 embeddings for crops...")
    embeddings = encoder.encode_batch(crops)
    logger.info("Embedding inference complete", shape=embeddings.shape)

    # Save crops and print results
    print("\n" + "=" * 60)
    print("DETECTION & EMBEDDING INFERENCE RESULTS:")
    print(f"Total Objects Detected: {len(detections)}")
    print(f"Embeddings Shape: {embeddings.shape}")
    print("=" * 60)
    
    for idx, (det, crop) in enumerate(zip(detections, crops)):
        crop_name = f"crop_{idx}_class_{det.class_id}.png"
        crop_path = out_dir / crop_name
        cv2.imwrite(str(crop_path), crop)
        
        emb_sample = embeddings[idx][:4] # First 4 elements
        print(f"Object {idx}:")
        print(f"  - Class ID: {det.class_id} | Confidence: {det.confidence:.2f}")
        print(f"  - BBox: {[int(coord) for coord in det.bbox]}")
        print(f"  - Embedding sample (first 4 dims): {emb_sample}")
        print(f"  - Crop saved to: {crop_path}")
        print("-" * 60)
    print()

if __name__ == "__main__":
    app()
