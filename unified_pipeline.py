import os
import sys
import logging
from pyspark.sql import SparkSession
from pyspark.sql.functions import monotonically_increasing_id, col

base_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(base_dir, "preprocessing"))
sys.path.insert(0, os.path.join(base_dir, "processing"))

logger = logging.getLogger(__name__)

def run_chunked_pipeline(chunk_size=1000):
    spark = SparkSession.builder \
        .appName("SmartCheckout_Unified_Pipeline") \
        .config("spark.jars.packages", "org.mongodb.spark:mongo-spark-connector_2.13:10.4.0") \
        .getOrCreate()

    # Đóng gói module thành zip để giải quyết các import dạng absolute (vd: processing.data_processing)
    import uuid, zipfile
    zip_path = f"/tmp/smart_checkout_modules_{uuid.uuid4().hex}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for folder in ["preprocessing", "processing"]:
            folder_path = os.path.join(base_dir, folder)
            if os.path.exists(folder_path):
                for root, dirs, files in os.walk(folder_path):
                    if "__pycache__" in root: continue
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, base_dir)
                        zipf.write(file_path, arcname)
    spark.sparkContext.addPyFile(zip_path)
    
    # Thêm các module lẻ để giải quyết các import dạng implicit (vd: from cleaning import DataCleaner)
    spark.sparkContext.addPyFile(os.path.join(base_dir, "preprocessing", "cleaning.py"))
    spark.sparkContext.addPyFile(os.path.join(base_dir, "preprocessing", "extract_data.py"))
    spark.sparkContext.addPyFile(os.path.join(base_dir, "preprocessing", "integrate.py"))
    spark.sparkContext.addPyFile(os.path.join(base_dir, "preprocessing", "transform.py"))
    spark.sparkContext.addPyFile(os.path.join(base_dir, "processing", "object_processor.py"))

    # Config chung
    config = {
        "mongo_uri": "mongodb://root:rootpass@ssc-mongo:27017/?authSource=admin",
        "mongo_db": "smart_checkout",
        "mongo_collection": "products",
        "path_column": "minio_image_path",
        "minio_endpoint": "ssc-minio:9000",
        "minio_access_key": "admin",
        "minio_secret_key": "adminpass",
        "qdrant_host": "ssc-qdrant",
        "qdrant_port": 6333,
        "qdrant_collection": "smart_checkout_objects",
        "use_api": True,
        "api_endpoint": "http://inference-api:8800/api/v1",
    }

    from preprocessing.extract_data import DataExtractor
    from preprocessing.data_preprocessing import process_clean, process_integrate, process_transform, upload_images_to_minio
    from processing.data_processing import (
        process_objects_partition, upload_objects_to_minio_partition, 
        push_to_qdrant_partition, ensure_qdrant_collection,
        OBJECT_SCHEMA_WITH_IMAGE, OBJECT_SCHEMA_META
    )

    # 1. Chỉ lấy METADATA từ MongoDB
    extractor = DataExtractor(spark, config)
    mongo_df = extractor.get_mongo_source()
    
    # Để tránh tràn RAM (OOM) khi cache toàn bộ DataFrame,
    # ta sẽ lấy danh sách ID về driver và chia chunk bằng Python.
    logger.info("Đang đếm tổng số records và trích xuất danh sách SKU...")
    id_rows = mongo_df.select("sku").collect()
    all_ids = [row["sku"] for row in id_rows if row["sku"]]
    total_docs = len(all_ids)
    
    if total_docs == 0:
        logger.info("Không có dữ liệu trong MongoDB. Dừng pipeline.")
        return

    num_chunks = max(1, (total_docs + chunk_size - 1) // chunk_size)
    logger.info(f"Tổng số records: {total_docs}. Sẽ chia làm {num_chunks} chunks (mỗi chunk ~{chunk_size} records).")

    # Đảm bảo Qdrant collection tồn tại trước khi chạy processing
    ensure_qdrant_collection(config, embedding_dim=512)

    fetch_image = extractor.get_image_from_minio_udf(
        config["minio_endpoint"], config["minio_access_key"], config["minio_secret_key"]
    )

    # 2. Xử lý từng luồng (chunk)
    for i in range(num_chunks):
        logger.info(f"\n{'='*50}\nBẮT ĐẦU XỬ LÝ CHUNK {i+1}/{num_chunks}\n{'='*50}")
        
        # Lấy danh sách ID cho chunk hiện tại
        current_chunk_ids = all_ids[i * chunk_size : (i + 1) * chunk_size]
        
        # Lọc DataFrame gốc theo các SKU này
        chunk_meta_df = mongo_df.filter(col("sku").isin(current_chunk_ids))
        
        if chunk_meta_df.count() == 0:
            continue
            
        chunk_meta_df = chunk_meta_df.repartition(8)

        # ---------------- BƯỚC A: FETCH ẢNH (Bắt đầu tốn RAM) ----------------
        chunk_raw = chunk_meta_df.withColumn("image_data", fetch_image(col(config["path_column"])))

        # ---------------- BƯỚC B & C: DELEGATE TỚI HÀM MAIN ----------------
        from data_preprocessing import main as prep_main
        from data_processing import main as proc_main
        
        # 1. Gọi về main của data_preprocessing để xử lý (Clean -> Integrate -> Transform) và tự động lưu
        df_trans = prep_main(external_spark=spark, external_config=config, input_df=chunk_raw)
        
        # 2. Gọi về main của data_processing để xử lý tiếp (Segment -> Object -> Embedding) và tự động push
        if df_trans is not None and df_trans.count() > 0:
            proc_main(external_spark=spark, external_config=config, input_df=df_trans)
        else:
            logger.warning(f"[Chunk {i+1}] Không có dữ liệu hợp lệ sau bước preprocessing, bỏ qua processing.")
        
        logger.info(f"Hoàn thành Chunk {i+1}/{num_chunks} và đã giải phóng RAM thành công!")

    spark.stop()
    logger.info("HOÀN TẤT TOÀN BỘ UNIFIED PIPELINE (TỪNG CHUNK)")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run_chunked_pipeline()
