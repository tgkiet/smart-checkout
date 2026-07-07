"""
data_processing.py — Spark Pipeline cho bước Processing.

Luồng xử lý:
  [MongoDB preprocessing.transformed]
        ↓  (Spark read, lấy ảnh đã qua transform)
  SEGMENT  → YOLOv8x-seg: phát hiện & tạo mask từng object
        ↓
  OBJECT   → Crop ảnh object (dùng mask nếu có, fallback bbox)
        ↓
  EMBEDDING → CLIP / ResNet50: tạo vector đặc trưng
        ↓
  ASSIGN METADATA → Gán SKU/name/price/platform từ document MongoDB gốc
        ↓
  ┌──────────────────────────────────┐
  │  Upload ảnh crop → MinIO         │  (preprocessing/objects/<sku>/<sub_id>.jpg)
  │  Lưu metadata    → MongoDB       │  (processing.objects)
  │  Push vector     → Qdrant        │  (collection: smart_checkout_objects)
  └──────────────────────────────────┘
"""

import os
import io
import logging
import zipfile

from pyspark.sql import SparkSession
from pyspark.sql.types import (
    StructType, StructField,
    StringType, BinaryType, ArrayType, FloatType,
    IntegerType, BooleanType, DoubleType,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/processing_pipeline.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema định nghĩa kết quả mỗi object (còn chứa binary ảnh crop)
# ---------------------------------------------------------------------------
OBJECT_SCHEMA_WITH_IMAGE = StructType([
    StructField("original_id", StringType(), True),
    StructField("sub_id", StringType(), True),
    StructField("sku", StringType(), True),
    StructField("name", StringType(), True),
    StructField("price", DoubleType(), True),
    StructField("platform", StringType(), True),
    StructField("minio_image_path", StringType(), True),   # đường dẫn ảnh gốc đã qua transform
    StructField("bbox", ArrayType(FloatType()), True),
    StructField("confidence", FloatType(), True),
    StructField("class_id", IntegerType(), True),
    StructField("mask_applied", BooleanType(), True),
    StructField("embedding", ArrayType(FloatType()), True),
    StructField("embedding_dim", IntegerType(), True),
    StructField("cropped_image_data", BinaryType(), True),
])

# Schema sau khi bóc tách binary (lưu MongoDB + Qdrant)
OBJECT_SCHEMA_META = StructType([
    StructField("original_id", StringType(), True),
    StructField("sub_id", StringType(), True),
    StructField("sku", StringType(), True),
    StructField("name", StringType(), True),
    StructField("price", DoubleType(), True),
    StructField("platform", StringType(), True),
    StructField("minio_image_path", StringType(), True),
    StructField("minio_object_path", StringType(), True),  # đường dẫn ảnh crop đã upload
    StructField("bbox", ArrayType(FloatType()), True),
    StructField("confidence", FloatType(), True),
    StructField("class_id", IntegerType(), True),
    StructField("mask_applied", BooleanType(), True),
    StructField("embedding", ArrayType(FloatType()), True),
    StructField("embedding_dim", IntegerType(), True),
])


# ---------------------------------------------------------------------------
# mapPartitions: Segment → Object → Embedding → Metadata
# ---------------------------------------------------------------------------
def process_objects_partition(iterator, config):
    """
    Chạy trên mỗi Spark partition (worker core).
    Khởi tạo model 1 lần duy nhất, xử lý các row bằng ThreadPool (nếu call API) 
    để tối ưu I/O mạng.
    """
    from object_processor import ObjectProcessor
    import concurrent.futures
    import uuid

    batch_id = str(uuid.uuid4())[:8]
    print(f"[DEBUG-BATCH] ==> Bắt đầu xử lý batch (partition) {batch_id}")

    processor = ObjectProcessor(config)

    def process_row(row):
        row_dict = row.asDict()
        return processor.process(row_dict, conf_threshold=0.5)

    item_count = 0
    # Chạy song song tối đa 2 luồng / partition để không làm chết ngộp API Server (nếu không có GPU mạnh)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        for records in executor.map(process_row, iterator):
            for rec in records:
                item_count += 1
                # Chuyển embedding list[float] sang tuple để Spark nhận được
                rec["embedding"] = list(rec["embedding"])
                yield rec

    print(f"[DEBUG-BATCH] ==> Hoàn thành batch {batch_id}. Tổng số items trong batch này: {item_count}")


# ---------------------------------------------------------------------------
# mapPartitions: Upload ảnh crop lên MinIO, trả về metadata nhẹ
# ---------------------------------------------------------------------------
def upload_objects_to_minio_partition(iterator, config):
    """
    Upload ảnh object đã crop lên MinIO.
    Đường dẫn: processing/objects/<sku>/<sub_id>.jpg
    Trả về dict metadata (không còn binary) kèm minio_object_path.
    """
    from minio import Minio

    client = Minio(
        config.get("minio_endpoint", "ssc-minio:9000"),
        access_key=config.get("minio_access_key", "admin"),
        secret_key=config.get("minio_secret_key", "adminpass"),
        secure=False,
    )
    bucket_name = "smart-checkout"

    try:
        if not client.bucket_exists(bucket_name):
            client.make_bucket(bucket_name)
    except Exception:
        pass

    for row in iterator:
        row_dict = row.asDict()
        cropped_bytes = row_dict.pop("cropped_image_data", None)
        sub_id = row_dict.get("sub_id", "unknown")
        sku = row_dict.get("sku", "unknown")

        minio_object_path = ""
        if cropped_bytes:
            object_name = f"processing/objects/{sku}/{sub_id}.jpg"
            try:
                client.put_object(
                    bucket_name=bucket_name,
                    object_name=object_name,
                    data=io.BytesIO(cropped_bytes),
                    length=len(cropped_bytes),
                    content_type="image/jpeg",
                )
                minio_object_path = f"s3://{bucket_name}/{object_name}"
            except Exception as e:
                pass  # giữ path rỗng nếu lỗi upload

        row_dict["minio_object_path"] = minio_object_path
        yield row_dict


# ---------------------------------------------------------------------------
# foreachPartition: Push vector lên Qdrant
# ---------------------------------------------------------------------------
def push_to_qdrant_partition(iterator, config):
    """
    Đẩy từng object record lên Qdrant với:
      - id       : hash của sub_id
      - vector   : embedding
      - payload  : toàn bộ metadata (sku, name, price, paths, bbox…)
    """
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointStruct

    host = config.get("qdrant_host", "ssc-qdrant")
    port = config.get("qdrant_port", 6333)
    collection = config.get("qdrant_collection", "smart_checkout_objects")

    client = QdrantClient(host=host, port=port)
    batch = []

    for row in iterator:
        row_dict = row.asDict()
        embedding = row_dict.get("embedding")
        if not embedding:
            continue

        # Qdrant cần id dạng số nguyên hoặc UUID
        sub_id_str = row_dict.get("sub_id", "")
        point_id = abs(hash(sub_id_str)) % (2**63)

        payload = {k: v for k, v in row_dict.items() if k != "embedding"}
        batch.append(PointStruct(id=point_id, vector=embedding, payload=payload))

        # Upload theo lô (batch) để giảm số lần gọi API
        if len(batch) >= 64:
            try:
                client.upsert(collection_name=collection, points=batch)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Lỗi khi push Qdrant batch 64: {e}")
            batch = []

    if batch:
        try:
            client.upsert(collection_name=collection, points=batch)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Lỗi khi push Qdrant batch cuối: {e}")


# ---------------------------------------------------------------------------
# Tiện ích: tạo Qdrant collection nếu chưa tồn tại
# ---------------------------------------------------------------------------
def ensure_qdrant_collection(config, embedding_dim=512):
    """Tạo collection Qdrant nếu chưa có. Gọi từ Driver (1 lần)."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams

    host = config.get("qdrant_host", "ssc-qdrant")
    port = config.get("qdrant_port", 6333)
    collection = config.get("qdrant_collection", "smart_checkout_objects")

    client = QdrantClient(host=host, port=port)
    existing = [c.name for c in client.get_collections().collections]
    if collection not in existing:
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=embedding_dim, distance=Distance.COSINE),
        )
        logger.info(f"Đã tạo Qdrant collection '{collection}' (dim={embedding_dim})")
    else:
        logger.info(f"Qdrant collection '{collection}' đã tồn tại.")


# ---------------------------------------------------------------------------
# Tiện ích: zip thư mục utils để Spark worker import được
# ---------------------------------------------------------------------------
def create_utils_zip():
    import uuid
    base_dir = os.path.dirname(os.path.abspath(__file__))
    zip_path = f"/tmp/utils_{uuid.uuid4().hex}.zip"
    utils_dir = os.path.join(base_dir, "utils")
    if os.path.exists(utils_dir):
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(utils_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, base_dir)
                    zipf.write(file_path, arcname)
    return zip_path


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main(external_spark=None, external_config=None, input_df=None):
    logger.info("=" * 60)
    logger.info("  BẮT ĐẦU PROCESSING PIPELINE")
    logger.info("  Luồng: img → segment → object → embedding → metadata → Qdrant")
    logger.info("=" * 60)

    spark = external_spark or (
        SparkSession.builder.appName("SmartCheckout_Processing")
        .config(
            "spark.jars.packages",
            "org.mongodb.spark:mongo-spark-connector_2.13:10.4.0",
        )
        .getOrCreate()
    )

    # --- Phân phối code cho Spark workers ---
    base_dir = os.path.dirname(os.path.abspath(__file__))
    spark.sparkContext.addPyFile(os.path.join(base_dir, "object_processor.py"))

    utils_zip = create_utils_zip()
    if os.path.exists(utils_zip):
        spark.sparkContext.addPyFile(utils_zip)

    # --- Config ---
    config = external_config or {
        "mongo_uri": "mongodb://root:rootpass@ssc-mongo:27017/?authSource=admin",
        "minio_endpoint": "ssc-minio:9000",
        "minio_access_key": "admin",
        "minio_secret_key": "adminpass",
        "qdrant_host": "ssc-qdrant",
        "qdrant_port": 6333,
        "qdrant_collection": "smart_checkout_objects",
        "use_api": True,
        "api_endpoint": "http://inference-api:8800/api/v1",
    }

    # -----------------------------------------------------------------------
    # BƯỚC 1: Đọc dữ liệu đã qua bước Transform từ MongoDB preprocessing (hoặc từ input_df)
    # -----------------------------------------------------------------------
    if input_df is not None:
        logger.info("[1/5] Nhận dữ liệu chunk từ unified pipeline...")
        df_transformed = input_df
    else:
        logger.info("[1/5] Đọc dữ liệu từ preprocessing.transformed...")
        df_transformed = (
            spark.read.format("mongodb")
            .option("spark.mongodb.read.connection.uri", config["mongo_uri"])
            .option("spark.mongodb.read.database", "preprocessing")
            .option("spark.mongodb.read.collection", "transformed")
            .option("spark.mongodb.read.partitioner", "com.mongodb.spark.sql.connector.read.partitioner.PaginateBySizePartitioner")
            .option("spark.mongodb.read.partitionerOptions.partitionSizeMB", "32")
            .load()
        )

        # Giới hạn để test (tăng lên hoặc bỏ .limit() khi production)
        df_transformed = df_transformed.repartition(8)
    
    num_partitions = df_transformed.rdd.getNumPartitions()
    total_count = df_transformed.count()
    logger.info(f"  Số lượng ảnh đầu vào: {total_count}")
    logger.info(f"  [DEBUG-BATCH] Dữ liệu đã được chia thành {num_partitions} batches (partitions) để chạy song song!")

    # -----------------------------------------------------------------------
    # BƯỚC 2+3+4: Segment → Object → Embedding → Assign Metadata
    # -----------------------------------------------------------------------
    logger.info("[2-4/5] Segment → Object Crop → Embedding → Assign Metadata...")
    rdd_objects = df_transformed.rdd.mapPartitions(
        lambda it: process_objects_partition(it, config)
    )
    df_objects = spark.createDataFrame(rdd_objects, schema=OBJECT_SCHEMA_WITH_IMAGE)
    df_objects.cache()
    obj_count = df_objects.count()
    logger.info(f"  Tổng số objects phát hiện được: {obj_count}")

    if obj_count == 0:
        logger.warning("Không có object nào được phát hiện. Kết thúc pipeline.")
        spark.stop()
        return

    # Lấy embedding_dim từ record đầu tiên để tạo Qdrant collection
    first_row = df_objects.select("embedding_dim").first()
    embedding_dim = 512
    if first_row and first_row["embedding_dim"]:
        val = int(first_row["embedding_dim"])
        if val > 0:
            embedding_dim = val
    logger.info(f"  Embedding dimension: {embedding_dim}")

    # -----------------------------------------------------------------------
    # BƯỚC 5a: Upload ảnh crop lên MinIO → nhận metadata kèm minio_object_path
    # -----------------------------------------------------------------------
    logger.info("[5a/5] Upload ảnh object lên MinIO...")
    rdd_meta = df_objects.rdd.mapPartitions(
        lambda it: upload_objects_to_minio_partition(it, config)
    )
    df_meta = spark.createDataFrame(rdd_meta, schema=OBJECT_SCHEMA_META)
    df_meta.cache()
    logger.info(f"  Upload hoàn tất. Số records metadata: {df_meta.count()}")

    # -----------------------------------------------------------------------
    # BƯỚC 5b: Lưu metadata vào MongoDB (processing.objects)
    # Document sẽ có:
    #   - minio_image_path  : đường dẫn ảnh gốc (đã qua transform)
    #   - minio_object_path : đường dẫn ảnh crop của object này
    #   - embedding         : vector để tra cứu trong Qdrant
    #   - sku, name, price… : metadata sản phẩm
    # -----------------------------------------------------------------------
    logger.info("[5b/5] Lưu metadata vào MongoDB processing.objects...")
    df_meta.write.format("mongodb") \
        .mode("append") \
        .option("spark.mongodb.write.connection.uri", config["mongo_uri"]) \
        .option("spark.mongodb.write.database", "processing") \
        .option("spark.mongodb.write.collection", "objects") \
        .save()
    logger.info("  Lưu MongoDB hoàn tất.")

    # -----------------------------------------------------------------------
    # BƯỚC 5c: Đảm bảo Qdrant collection tồn tại (từ Driver)
    # -----------------------------------------------------------------------
    logger.info("[5c/5] Kiểm tra / tạo Qdrant collection...")
    ensure_qdrant_collection(config, embedding_dim=embedding_dim)

    # -----------------------------------------------------------------------
    # BƯỚC 5d: Push vector lên Qdrant (song song qua foreachPartition)
    # -----------------------------------------------------------------------
    logger.info("[5d/5] Push embedding vectors lên Qdrant...")
    df_meta.rdd.foreachPartition(
        lambda it: push_to_qdrant_partition(it, config)
    )
    logger.info("  Push Qdrant hoàn tất.")

    # -----------------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("  HOÀN TẤT PROCESSING PIPELINE")
    logger.info("=" * 60)
    if external_spark is None:
        spark.stop()


if __name__ == "__main__":
    main()
