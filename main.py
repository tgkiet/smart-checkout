import argparse
import logging
import os
import sys

# Cấu hình đường dẫn sys.path để các module con (như extract_data, object_processor)
# có thể được import đúng cách khi chạy từ thư mục gốc.
base_dir = os.path.dirname(os.path.abspath(__file__))
preprocessing_dir = os.path.join(base_dir, "preprocessing")
processing_dir = os.path.join(base_dir, "processing")

sys.path.insert(0, preprocessing_dir)
sys.path.insert(0, processing_dir)

# Thiết lập thư mục logs
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/smart_checkout_pipeline.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="Smart Checkout Data Pipeline Orchestrator")
    parser.add_argument(
        "--stage", 
        type=str, 
        choices=["all", "preprocessing", "processing", "unified"], 
        default="unified",
        help="Chọn giai đoạn pipeline muốn chạy: 'preprocessing', 'processing', 'all' hoặc 'unified' (mặc định - chạy luồng chia nhỏ tối ưu RAM)"
    )
    
    args = parser.parse_args()
    
    logger.info("=" * 60)
    logger.info(f" BẮT ĐẦU SMART CHECKOUT PIPELINE - CHẾ ĐỘ: {args.stage.upper()}")
    logger.info("=" * 60)
    
    # Import muộn để tránh load thư viện dư thừa nếu chỉ chạy 1 stage
    if args.stage in ["all", "preprocessing"]:
        logger.info("\n" + "*" * 50)
        logger.info(" BẮT ĐẦU GIAI ĐOẠN 1: PREPROCESSING")
        logger.info("*" * 50)
        try:
            import data_preprocessing
            data_preprocessing.main()
            logger.info("=> Giai đoạn PREPROCESSING hoàn tất thành công.")
        except Exception as e:
            logger.error(f"Lỗi nghiêm trọng trong giai đoạn PREPROCESSING: {e}", exc_info=True)
            sys.exit(1)
            
    if args.stage in ["all", "processing"]:
        logger.info("\n" + "*" * 50)
        logger.info(" BẮT ĐẦU GIAI ĐOẠN 2: PROCESSING (AI & Vector DB)")
        logger.info("*" * 50)
        try:
            import data_processing
            data_processing.main()
            logger.info("=> Giai đoạn PROCESSING hoàn tất thành công.")
        except Exception as e:
            logger.error(f"Lỗi nghiêm trọng trong giai đoạn PROCESSING: {e}", exc_info=True)
            sys.exit(1)

    if args.stage == "unified":
        logger.info("\n" + "*" * 50)
        logger.info(" BẮT ĐẦU UNIFIED CHUNKED PIPELINE (TỐI ƯU RAM)")
        logger.info("*" * 50)
        try:
            import unified_pipeline
            unified_pipeline.run_chunked_pipeline(chunk_size=1000)
            logger.info("=> Giai đoạn UNIFIED hoàn tất thành công.")
        except Exception as e:
            logger.error(f"Lỗi nghiêm trọng trong giai đoạn UNIFIED: {e}", exc_info=True)
            sys.exit(1)

    logger.info("=" * 60)
    logger.info(" TOÀN BỘ PIPELINE ĐÃ HOÀN TẤT!")
    logger.info("=" * 60)

if __name__ == "__main__":
    main()
