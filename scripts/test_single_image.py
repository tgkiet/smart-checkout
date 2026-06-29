from pathlib import Path

import cv2
import typer

from src.core.config import load_config
from src.core.logger import get_logger, setup_logger
from src.pipeline.checkout_pipeline import CheckoutPipeline
from src.utils.visualization import draw_checkout_result, draw_detections

app = typer.Typer()
logger = get_logger(__name__)


@app.command()
def test_image(
    image_path: str = typer.Argument(..., help="Path to the BGR image file to test"),
    config_path: str = typer.Option("config/settings.yaml", "--config", "-c", help="Path to config yaml file"),
    output_dir: str = typer.Option("output", "--output-dir", "-o", help="Directory to save visual output images"),
):
    """
    Test script to run the checkout pipeline on a single image.
    If Milvus connection is not available, falls back to YOLO-seg detection and masked cropping only.
    """
    config = load_config(config_path)
    setup_logger(log_level="INFO")

    img_path = Path(image_path)
    if not img_path.exists():
        logger.error("Target image does not exist", path=str(img_path))
        raise typer.Exit(code=1)

    out_path = Path(output_dir)
    out_path.mkdir(exist_ok=True)

    logger.info("Loading target image...", path=str(img_path))
    frame = cv2.imread(str(img_path))
    if frame is None:
        logger.error("Failed to load image. Invalid format or corrupt file.")
        raise typer.Exit(code=1)

    # Attempt to run full end-to-end checkout pipeline
    try:
        logger.info("Initializing Checkout Pipeline orchestrator...")
        pipeline = CheckoutPipeline(config_path)

        logger.info("Processing frame through pipeline...")
        # If using MockScale, it will zero-scale or try to guess weight.
        # Let's read mock scale weight if available
        result = pipeline.process_frame(frame)

        # Save visualization of checkout result
        detections = pipeline.segmentor.detect(frame)
        vis_checkout = draw_checkout_result(frame, result, detections)
        checkout_out = out_path / "checkout_result.png"
        cv2.imwrite(str(checkout_out), vis_checkout)

        # Save standard bounding box / mask overlay for reference
        vis_det = draw_detections(frame, detections)
        det_out = out_path / "detected_objects.png"
        cv2.imwrite(str(det_out), vis_det)

        print("\n" + "=" * 60)
        print("KẾT QUẢ CHECKOUT (CHẠY THÀNH CÔNG ĐẦU-CUỐI):")
        print(f"Tổng tiền: {result.total_price:,.0f} VND")
        print(f"Trọng lượng đo được: {result.scale_weight:.1f}g")
        print(f"Khớp cân nặng: {result.weight_match}")
        print("Danh sách sản phẩm nhận diện được:")
        for item in result.items:
            print(
                f"  - {item.sku_name} (x{item.quantity}) | Đơn giá: {item.unit_price:,.0f} VND | Độ tin cậy: {item.vision_score:.2f}"
            )
        print("=" * 60 + "\n")
        logger.info("Saved visualizations to output directory", dir=str(out_path))

    except Exception as e:
        logger.warning(
            "Không thể chạy pipeline đầu-cuối (có thể do Milvus DB chưa được khởi chạy). "
            "Chuyển sang chế độ chạy YOLOv11-seg và lọc tách nền...",
            error=str(e),
        )

        from src.detection.mask_processor import MaskProcessor
        from src.detection.yolo_segmentor import YOLOSegmentor

        # Load YOLO-seg only
        segmentor = YOLOSegmentor(config.detection)
        processor = MaskProcessor()

        logger.info("Running YOLOv11-seg detection...")
        detections = segmentor.detect(frame)
        logger.info("Detection complete", count=len(detections))

        # Save standard detection visualization
        vis_det = draw_detections(frame, detections)
        det_out = out_path / "detected_objects.png"
        cv2.imwrite(str(det_out), vis_det)

        # Extract and save crops
        crops = processor.extract_batch(frame, detections)
        for idx, crop in enumerate(crops):
            crop_out = out_path / f"crop_{idx}.png"
            cv2.imwrite(str(crop_out), crop)
            logger.info("Saved cleaned crop image", index=idx, path=str(crop_out))

        print("\n" + "=" * 60)
        print("CHẠY THÀNH CÔNG TẦNG 1 (DETECTION & MASK PROCESSING):")
        print(f"Số lượng vật thể phát hiện được: {len(detections)}")
        print(f"Ảnh kết quả đã lưu tại: {det_out}")
        print(f"Ảnh crop tách nền đã lưu tại thư mục: {out_path}/")
        print("=" * 60 + "\n")


if __name__ == "__main__":
    app()
