"""
run_pipeline.py — Điểm khởi chạy thống nhất cho toàn bộ Smart Checkout Pipeline.

Kiến trúc luồng xử lý:
──────────────────────────────────────────────────────────────────────────────
  [MongoDB: smart_checkout.products]  +  [MinIO: ảnh gốc]
          │
          │  Extract (DataExtractor)
          ▼
  ┌─────────────────────────────────────────────────┐
  │  BATCH LAYER  (chunk_size = 1_000 rows / batch) │
  └─────────────────────────────────────────────────┘
          │
          ├── Batch 0 ──► STAGE 1: PREPROCESSING ──► STAGE 2: PROCESSING
          ├── Batch 1 ──► STAGE 1: PREPROCESSING ──► STAGE 2: PROCESSING
          └── Batch N ──► ...

  STAGE 1 — PREPROCESSING  (module: preprocessing/data_preprocessing.py)
  ┌──────────────────────────────────────────────────────────────────────┐
  │  cleaning   → MinIO: preprocessing/cleaning/<id>.jpg                 │
  │             → MongoDB: preprocessing.cleaning                        │
  │  integrate  → MinIO: preprocessing/integrate/<id>.jpg                │
  │             → MongoDB: preprocessing.integrated                      │
  │  transform  → MinIO: preprocessing/transform/<id>.jpg                │
  │             → MongoDB: preprocessing.transformed                     │
  └──────────────────────────────────────────────────────────────────────┘
          │
          ▼ (df_transformed — DataFrame đã qua transform)
  STAGE 2 — PROCESSING  (module: processing/data_processing.py)
  ┌──────────────────────────────────────────────────────────────────────┐
  │  segment → object crop → embedding → assign metadata                 │
  │  → MinIO: processing/objects/<sku>/<sub_id>.jpg                      │
  │  → MongoDB: processing.objects                                       │
  │  → Qdrant: collection smart_checkout_objects                         │
  └──────────────────────────────────────────────────────────────────────┘
──────────────────────────────────────────────────────────────────────────────
"""

import os
import sys

# ── FIX OOM: Phải đặt TRƯỚC khi import pyspark để JVM được cấp đủ RAM ──────
# .config("spark.driver.memory") trong code KHÔNG có tác dụng trong local mode
# vì JVM đã khởi động trước. Chỉ có PYSPARK_SUBMIT_ARGS mới set được heap size.
if "PYSPARK_SUBMIT_ARGS" not in os.environ:
    _drv = os.getenv("SPARK_DRIVER_MEMORY", "12g")  # tăng lên 12g mặc định
    os.environ["PYSPARK_SUBMIT_ARGS"] = f"--driver-memory {_drv} pyspark-shell"
    print(f"[OOM-FIX] PYSPARK_SUBMIT_ARGS = {os.environ['PYSPARK_SUBMIT_ARGS']}")

import logging
import importlib
import math
import zipfile
import argparse

from dotenv import load_dotenv
from pyspark.sql import SparkSession

# ─────────────────────────────────────────────────────────────────────────────
# Setup logging
# ─────────────────────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler("logs/run_pipeline.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("run_pipeline")

# ─────────────────────────────────────────────────────────────────────────────
# Load .env (nếu có)
# ─────────────────────────────────────────────────────────────────────────────
load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — đọc từ env, fallback về giá trị mặc định Docker Compose
# ─────────────────────────────────────────────────────────────────────────────
CONFIG = {
    # MongoDB
    "mongo_uri": os.getenv("MONGO_URI", "mongodb://root:rootpass@ssc-mongo:27017/?authSource=admin"),
    "mongo_db": os.getenv("MONGO_DB", "smart_checkout"),
    "mongo_collection": os.getenv("MONGO_COLLECTION", "products"),
    "path_column": os.getenv("MONGO_PATH_COLUMN", "minio_image_path"),

    # MinIO
    "minio_endpoint": os.getenv("MINIO_ENDPOINT", "ssc-minio:9000"),
    "minio_access_key": os.getenv("MINIO_ACCESS", "admin"),
    "minio_secret_key": os.getenv("MINIO_SECRET", "adminpass"),

    # Qdrant
    "qdrant_host": os.getenv("QDRANT_HOST", "ssc-qdrant"),
    "qdrant_port": int(os.getenv("QDRANT_PORT", "6333")),
    "qdrant_collection": os.getenv("QDRANT_COLLECTION", "smart_checkout_objects"),

    # Inference API (cho processing stage)
    "use_api": os.getenv("USE_API", "true").lower() == "true",
    "api_endpoint": os.getenv("API_ENDPOINT", "http://inference-api:8800/api/v1"),

    # Pipeline tuning
    "batch_size":      int(os.getenv("PIPELINE_BATCH_SIZE",    "1000")),
    # Giảm xuống 4 để giảm peak memory (mỗi partition ốc riêng 1 luồng JVM)
    "num_partitions":  int(os.getenv("SPARK_NUM_PARTITIONS",   "4")),
    "extract_limit":   int(os.getenv("EXTRACT_LIMIT",          "0")),
}

BATCH_SIZE = CONFIG["batch_size"]


# ─────────────────────────────────────────────────────────────────────────────
# SPARK SESSION
# ─────────────────────────────────────────────────────────────────────────────
def build_spark_session() -> SparkSession:
    """
    Khởi tạo SparkSession duy nhất cho toàn bộ pipeline.
    Cấu hình memory được tối ưu để xử lý dữ liệu ảnh binary nặng.
    """
    # Đọc tuỳ chỉnh memory từ env (override nếu máy có nhiều RAM hơn)
    driver_mem  = os.getenv("SPARK_DRIVER_MEMORY",  "4g")
    executor_mem = os.getenv("SPARK_EXECUTOR_MEMORY", "4g")
    offheap_mem  = os.getenv("SPARK_OFFHEAP_MEMORY",  "2g")

    spark = (
        SparkSession.builder
        .appName("SmartCheckout_UnifiedPipeline")
        .config(
            "spark.jars.packages",
            "org.mongodb.spark:mongo-spark-connector_2.13:10.4.0",
        )
        # ── Memory ────────────────────────────────────────────────────────
        .config("spark.driver.memory",          driver_mem)
        .config("spark.executor.memory",        executor_mem)
        # Off-heap giúp giảm GC pressure khi xử lý binary data lớn
        .config("spark.memory.offHeap.enabled", "true")
        .config("spark.memory.offHeap.size",    offheap_mem)
        # Tỷ lệ RAM dành cho execution (shuffle/sort) vs storage (cache)
        # Giảm storage fraction để ưu tiên execution, tránh OOM khi cache ảnh
        .config("spark.memory.fraction",        "0.8")
        .config("spark.memory.storageFraction", "0.2")
        # ── Serialization (Kryo nhanh + nhẹ hơn Java default) ─────────────
        .config("spark.serializer",             "org.apache.spark.serializer.KryoSerializer")
        .config("spark.kryoserializer.buffer.max", "512m")
        # ── Shuffle ───────────────────────────────────────────────────────
        .config("spark.sql.shuffle.partitions", str(CONFIG["num_partitions"]))
        # Nén dữ liệu shuffle để giảm I/O
        .config("spark.shuffle.compress",       "true")
        .config("spark.shuffle.spill.compress", "true")
        # ── Misc ──────────────────────────────────────────────────────────
        .config("spark.ui.enabled",             "false")
        # Tăng timeout heartbeat để tránh executor bị kill khi xử lý ảnh nặng
        .config("spark.network.timeout",        "600s")
        .config("spark.executor.heartbeatInterval", "60s")
        # Giới hạn số field in plan log (tránh truncation warning)
        .config("spark.sql.debug.maxToStringFields", "50")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    logger.info(f"[SPARK] Driver memory: {driver_mem} | Executor memory: {executor_mem} | Off-heap: {offheap_mem}")
    logger.info("[SPARK] Khởi tạo SparkSession thành công.")
    return spark


# ─────────────────────────────────────────────────────────────────────────────
# DISTRIBUTE SOURCE FILES TO SPARK WORKERS
# ─────────────────────────────────────────────────────────────────────────────
def _register_preprocessing_files(spark: SparkSession):
    """Đăng ký các file Python của preprocessing module lên Spark workers."""
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preprocessing")
    for fname in ["extract_data.py", "cleaning.py", "integrate.py", "transform.py"]:
        fpath = os.path.join(base, fname)
        if os.path.exists(fpath):
            spark.sparkContext.addPyFile(fpath)
            logger.debug(f"  [PyFile] {fpath}")


def _register_processing_files(spark: SparkSession):
    """Đăng ký các file Python của processing module lên Spark workers."""
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "processing")

    # object_processor.py
    opp = os.path.join(base, "object_processor.py")
    if os.path.exists(opp):
        spark.sparkContext.addPyFile(opp)
        logger.debug(f"  [PyFile] {opp}")

    # utils/ → đóng gói thành zip để worker import được
    utils_dir = os.path.join(base, "utils")
    if os.path.exists(utils_dir):
        zip_path = f"/tmp/processing_utils_{os.getpid()}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(utils_dir):
                for f in files:
                    fp = os.path.join(root, f)
                    arcname = os.path.relpath(fp, base)
                    zf.write(fp, arcname)
        spark.sparkContext.addPyFile(zip_path)
        logger.debug(f"  [PyFile zip] {zip_path}")


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 0: EXTRACT — lấy toàn bộ dữ liệu từ MongoDB + MinIO
# ─────────────────────────────────────────────────────────────────────────────
def stage_extract(spark: SparkSession, config: dict):
    """
    Chỉ trích xuất METADATA từ MongoDB (KHÔNG tải ảnh từ MinIO).
    Ảnh sẽ được tải lazily trong từng batch bởi fetch_images_for_batch().

    Lý do tách biệt:
      - UDF tải ảnh MinIO chạy trên ALL rows cùng lúc → OOM
      - Metadata (text/numbers) rất nhẹ, có thể load toàn bộ để đếm và chia batch
    """
    logger.info("━━━ [EXTRACT] Đọc metadata từ MongoDB (chưa tải ảnh) ━━━")

    prep_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preprocessing")
    if prep_dir not in sys.path:
        sys.path.insert(0, prep_dir)

    from extract_data import DataExtractor

    extractor = DataExtractor(spark, config)
    limit = config.get("extract_limit", 0) or None

    # Chỉ lấy metadata (get_mongo_source), KHÔNG gọi UDF tải ảnh
    logger.info("[EXTRACT] Đang gọi extractor.get_mongo_source()...")
    df_raw = extractor.get_mongo_source()
    logger.info("[EXTRACT] Lấy dữ liệu raw thành công, chuẩn bị apply limit (nếu có).")
    if limit:
        df_raw = df_raw.limit(limit)

    # ── Chỉ giữ lại các cột thực sự cần ──────────────────────────────────────
    # Các field khác (ld_json, raw_html, v.v.) thường rất nặng, bỏ qua ngay tại đây
    path_col  = config.get("path_column", "minio_image_path")
    keep_cols = ["sku", "_id", "product_id", "name", "title",
                 "price", "platform", "source", path_col]
    existing  = [c for c in keep_cols if c in df_raw.columns]
    if existing:
        df_meta = df_raw.select(*existing)
        logger.info(f"[EXTRACT] Project xuống {len(existing)} cột: {existing}")
    else:
        df_meta = df_raw  # fallback: giữ nguyên nếu không match

    # Repartition sau limit để tránh 1-partition bottleneck
    n_parts = config.get("num_partitions", 4)
    df_meta = df_meta.repartition(n_parts)

    df_meta.cache()  # metadata nhẹ, cache OK
    total = df_meta.count()
    logger.info(f"[EXTRACT] Tổng số bản ghi metadata: {total:,} | partitions: {n_parts}")
    df_meta.printSchema()
    return df_meta, total


def fetch_images_for_batch(df_batch, config: dict):
    """
    Áp dụng UDF tải ảnh MinIO CHỈ cho batch hiện tại.
    Được gọi ở đầu stage_preprocessing cho từng batch 1.000 rows.
    """
    from pyspark.sql.functions import col
    from extract_data import DataExtractor

    path_col = config.get("path_column", "minio_image_path")
    minio_ep  = config.get("minio_endpoint",  "ssc-minio:9000")
    minio_ak  = config.get("minio_access_key", "admin")
    minio_sk  = config.get("minio_secret_key", "adminpass")

    logger.info(f"[FETCH-IMAGES] Khởi tạo UDF với endpoint {minio_ep} và đính kèm vào DataFrame.")
    fetch_udf = DataExtractor.get_image_from_minio_udf(minio_ep, minio_ak, minio_sk)
    return df_batch.withColumn("image_data", fetch_udf(col(path_col)))


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 1: PREPROCESSING — cleaning → integrate → transform (theo batch)
# ─────────────────────────────────────────────────────────────────────────────
def stage_preprocessing(spark: SparkSession, df_batch, batch_idx: int, config: dict):
    """
    Chạy toàn bộ preprocessing pipeline trên 1 batch DataFrame.
    Bước đầu tiên: fetch ảnh từ MinIO CHỈ cho batch này (lazy, tiết kiệm RAM).
    Lưu kết quả mỗi sub-stage vào:
      - MinIO  : preprocessing/<stage>/<id>.jpg
      - MongoDB: preprocessing.<stage>

    Trả về df_transformed để chuyển tiếp sang stage Processing.
    """
    batch_count = df_batch.count()
    logger.info(f"  ┌── [PREPROCESSING] Batch #{batch_idx} — {batch_count:,} rows")

    # ── Bước 0: Tải ảnh từ MinIO chỉ cho batch này ───────────────────────
    logger.info(f"  │  [FETCH-IMAGES] Tải ảnh MinIO cho {batch_count:,} rows...")
    prep_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preprocessing")
    if prep_dir not in sys.path:
        sys.path.insert(0, prep_dir)
    df_batch = fetch_images_for_batch(df_batch, config)

    # Import lazy để tránh conflict namespace giữa preprocessing & processing
    prep_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preprocessing")
    if prep_dir not in sys.path:
        sys.path.insert(0, prep_dir)

    # Dùng importlib để nạp độc lập, tránh module cache clash
    spec_pp = importlib.util.spec_from_file_location(
        f"data_preprocessing_b{batch_idx}",
        os.path.join(prep_dir, "data_preprocessing.py"),
    )
    pp_module = importlib.util.module_from_spec(spec_pp)
    spec_pp.loader.exec_module(pp_module)

    # DataFrame chứa binary image_data → KHÔNG cache vào memory (OOM)
    # Dùng DISK_ONLY để Spark spill xuống disk thay vì OOM
    from pyspark import StorageLevel

    # ── Sub-stage: CLEANING ───────────────────────────────────────────────
    logger.info(f"  │  [CLEANING] Batch #{batch_idx} - Bắt đầu gọi process_clean")
    df_cleaned, cleaner = pp_module.process_clean(df_batch, config)
    # DISK_ONLY: ảnh binary nặng → spill ra disk, không giữ trên heap
    df_cleaned.persist(StorageLevel.DISK_ONLY)
    clean_count = df_cleaned.count()
    logger.info(f"  │    → Sau cleaning: {clean_count:,} rows")

    if clean_count > 0:
        logger.info(f"  │    [CLEANING] Uploading ảnh cleaning lên MinIO...")
        df_clean_enriched = pp_module.upload_images_to_minio(df_cleaned, "cleaning", config)
        # Sau upload_images_to_minio, df_clean_enriched có thêm cột path (string) nhẹ hơn
        # Lưu MongoDB ngay rồi unpersist để giải phóng
        logger.info(f"  │    [CLEANING] Lưu metadata cleaning vào MongoDB...")
        cleaner.save_to_mongodb(df_clean_enriched, "preprocessing", "cleaning")
        df_cleaned.unpersist()
        logger.info(f"  │    [CLEANING] Hoàn tất cleaning.")
    else:
        df_clean_enriched = df_cleaned
        logger.warning(f"  │    [CLEANING] Batch #{batch_idx}: không có row nào sau cleaning!")

    # ── Sub-stage: INTEGRATE ─────────────────────────────────────────────
    logger.info(f"  │  [INTEGRATE] Batch #{batch_idx} - Bắt đầu gọi process_integrate")
    df_integrated, integrator = pp_module.process_integrate(df_clean_enriched, config)
    df_integrated.persist(StorageLevel.DISK_ONLY)
    integ_count = df_integrated.count()
    logger.info(f"  │    → Sau integrate: {integ_count:,} rows")

    if integ_count > 0:
        logger.info(f"  │    [INTEGRATE] Uploading ảnh integrate lên MinIO...")
        df_integ_enriched = pp_module.upload_images_to_minio(df_integrated, "integrate", config)
        logger.info(f"  │    [INTEGRATE] Lưu metadata integrate vào MongoDB...")
        integrator.save_to_mongodb(df_integ_enriched, "preprocessing", "integrated")
        df_integrated.unpersist()
        logger.info(f"  │    [INTEGRATE] Hoàn tất integrate.")
    else:
        df_integ_enriched = df_integrated
        logger.warning(f"  │    [INTEGRATE] Batch #{batch_idx}: không có row nào sau integrate!")

    # ── Sub-stage: TRANSFORM ─────────────────────────────────────────────
    logger.info(f"  │  [TRANSFORM] Batch #{batch_idx} - Bắt đầu gọi process_transform")
    df_transformed, transformer = pp_module.process_transform(df_integ_enriched, config)
    df_transformed.persist(StorageLevel.DISK_ONLY)
    trans_count = df_transformed.count()
    logger.info(f"  │    → Sau transform: {trans_count:,} rows")

    if trans_count > 0:
        logger.info(f"  │    [TRANSFORM] Uploading ảnh transform lên MinIO...")
        df_trans_enriched = pp_module.upload_images_to_minio(df_transformed, "transform", config)
        logger.info(f"  │    [TRANSFORM] Lưu metadata transform vào MongoDB...")
        transformer.save_to_mongodb(df_trans_enriched, "preprocessing", "transformed")
        df_transformed.unpersist()
        logger.info(f"  │    [TRANSFORM] Hoàn tất transform.")
    else:
        df_trans_enriched = df_transformed
        logger.warning(f"  │    [TRANSFORM] Batch #{batch_idx}: không có row nào sau transform!")

    # Giải phóng RAM các DataFrame trung gian không cần nữa
    df_clean_enriched.unpersist()
    df_integ_enriched.unpersist()

    logger.info(f"  └── [PREPROCESSING] Batch #{batch_idx} hoàn tất. Output: {trans_count:,} rows → Processing")
    return df_trans_enriched


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 2: PROCESSING — segment → object → embed → metadata (theo batch)
# ─────────────────────────────────────────────────────────────────────────────
def stage_processing(spark: SparkSession, df_transformed, batch_idx: int, config: dict):
    """
    Chạy toàn bộ processing pipeline trên df_transformed của 1 batch.
    Bên trong chia nhỏ theo Spark partitions để xử lý song song.

    Lưu kết quả vào:
      - MinIO  : processing/objects/<sku>/<sub_id>.jpg
      - MongoDB: processing.objects
      - Qdrant : collection smart_checkout_objects
    """
    from pyspark.sql.types import StructType, StructField, StringType, BinaryType
    from pyspark.sql.types import ArrayType, FloatType, IntegerType, BooleanType, DoubleType

    logger.info(f"  ┌── [PROCESSING] Batch #{batch_idx}")

    proc_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "processing")
    if proc_dir not in sys.path:
        sys.path.insert(0, proc_dir)

    # Import data_processing module độc lập
    spec_proc = importlib.util.spec_from_file_location(
        f"data_processing_b{batch_idx}",
        os.path.join(proc_dir, "data_processing.py"),
    )
    proc_module = importlib.util.module_from_spec(spec_proc)
    spec_proc.loader.exec_module(proc_module)

    # Repartition để các Spark worker nhận phần đều nhau
    num_parts = config.get("num_partitions", 8)
    df_input = df_transformed.repartition(num_parts)
    total_in = df_input.count()
    logger.info(f"  │  Input: {total_in:,} rows — {num_parts} partitions")

    if total_in == 0:
        logger.warning(f"  └── [PROCESSING] Batch #{batch_idx}: không có dữ liệu đầu vào, bỏ qua.")
        return

    # ── Sub-stage: SEGMENT + OBJECT CROP + EMBEDDING + METADATA ─────────
    logger.info(f"  │  [SEG→OBJ→EMBED] Batch #{batch_idx} - Bắt đầu mapPartitions process_objects_partition")
    rdd_objects = df_input.rdd.mapPartitions(
        lambda it: proc_module.process_objects_partition(it, config)
    )
    OBJECT_SCHEMA_WITH_IMAGE = proc_module.OBJECT_SCHEMA_WITH_IMAGE
    df_objects = spark.createDataFrame(rdd_objects, schema=OBJECT_SCHEMA_WITH_IMAGE)
    logger.info(f"  │  [SEG→OBJ→EMBED] Đang đếm số lượng objects...")
    df_objects.cache()
    obj_count = df_objects.count()
    logger.info(f"  │    → Objects phát hiện: {obj_count:,}")

    if obj_count == 0:
        logger.warning(f"  └── [PROCESSING] Batch #{batch_idx}: không phát hiện object nào.")
        df_objects.unpersist()
        return

    # Lấy embedding_dim từ record đầu tiên để khởi tạo Qdrant collection
    first_row = df_objects.select("embedding_dim").first()
    embedding_dim = 512
    if first_row and first_row["embedding_dim"] and int(first_row["embedding_dim"]) > 0:
        embedding_dim = int(first_row["embedding_dim"])
    logger.info(f"  │    embedding_dim = {embedding_dim}")

    # ── Sub-stage: UPLOAD CROP IMAGES → MinIO ────────────────────────────
    logger.info(f"  │  [MINIO-UPLOAD] Batch #{batch_idx}: upload ảnh object crop - Bắt đầu mapPartitions upload_objects_to_minio_partition")
    OBJECT_SCHEMA_META = proc_module.OBJECT_SCHEMA_META
    rdd_meta = df_objects.rdd.mapPartitions(
        lambda it: proc_module.upload_objects_to_minio_partition(it, config)
    )
    df_meta = spark.createDataFrame(rdd_meta, schema=OBJECT_SCHEMA_META)
    logger.info(f"  │  [MINIO-UPLOAD] Đang đếm metadata records...")
    df_meta.cache()
    meta_count = df_meta.count()
    logger.info(f"  │    → Metadata records sau upload MinIO: {meta_count:,}")
    df_objects.unpersist()

    # ── Sub-stage: SAVE METADATA → MongoDB processing.objects ────────────
    logger.info(f"  │  [MONGODB] Batch #{batch_idx}: lưu metadata vào processing.objects")
    (
        df_meta.write.format("mongodb")
        .mode("append")
        .option("spark.mongodb.write.connection.uri", config["mongo_uri"])
        .option("spark.mongodb.write.database", "processing")
        .option("spark.mongodb.write.collection", "objects")
        .save()
    )
    logger.info(f"  │    → MongoDB lưu thành công.")

    # ── Sub-stage: ENSURE QDRANT COLLECTION ─────────────────────────────
    logger.info(f"  │  [QDRANT] Batch #{batch_idx}: kiểm tra / tạo collection")
    try:
        proc_module.ensure_qdrant_collection(config, embedding_dim=embedding_dim)
    except Exception as e:
        logger.warning(f"  │    [QDRANT] Không thể khởi tạo collection: {e}")

    # ── Sub-stage: PUSH VECTORS → Qdrant ────────────────────────────────
    logger.info(f"  │  [QDRANT] Batch #{batch_idx}: push embedding vectors")
    try:
        df_meta.rdd.foreachPartition(
            lambda it: proc_module.push_to_qdrant_partition(it, config)
        )
        logger.info(f"  │    → Qdrant push thành công.")
    except Exception as e:
        logger.error(f"  │    [QDRANT] Lỗi push vectors: {e}")

    df_meta.unpersist()
    logger.info(f"  └── [PROCESSING] Batch #{batch_idx} hoàn tất.")


# ─────────────────────────────────────────────────────────────────────────────
# BATCH SPLITTER — chia metadata DataFrame (nhẹ, không có ảnh) theo SKU
# ─────────────────────────────────────────────────────────────────────────────
def split_into_batches(df, batch_size: int, total: int):
    """
    Generator: yield từng batch metadata DataFrame theo batch_id gán ngẫu nhiên bằng rand().

    Lưu ý: df ở đây CHỈ chứa metadata (không có image_data),
    nên rand() + cache rất nhẹ và an toàn — không gây OOM.
    Ảnh sẽ được tải lazily trong fetch_images_for_batch() bên trong stage_preprocessing.
    """
    from pyspark.sql.functions import rand

    num_batches = math.ceil(total / batch_size)
    logger.info(f"[BATCH] Tổng {total:,} rows → {num_batches} batches (≤{batch_size:,} rows/batch)")

    if num_batches <= 1:
        yield 0, df
        return

    # Ưu tiên dùng SKU làm khóa chính để join, fallback về _id / product_id
    pk_col = "sku" if "sku" in df.columns \
        else "_id" if "_id" in df.columns \
        else "product_id" if "product_id" in df.columns \
        else None

    if pk_col is None:
        logger.warning("[BATCH] Không tìm thấy cột khóa (sku/_id/product_id). Xử lý 1 batch duy nhất.")
        yield 0, df
        return

    logger.info(f"[BATCH] Dùng '{pk_col}' làm khóa phân batch")

    # Gán batch_id ngẫu nhiên cho từng SKU → cache (chỉ cột ID, rất nhẹ)
    df_keys = (
        df.select(pk_col)
          .withColumn("__batch_id__", (rand() * num_batches).cast("int"))
    )
    df_keys.cache()
    df_keys.count()  # ép Spark thực thi để cố định giá trị rand()

    for batch_idx in range(num_batches):
        logger.info(f"[BATCH] Đang chuẩn bị filter và join dữ liệu cho batch #{batch_idx + 1}/{num_batches}...")
        # Filter key-only DF → join lại với metadata DF gốc (vẫn không có ảnh)
        batch_keys = df_keys.filter(df_keys["__batch_id__"] == batch_idx).drop("__batch_id__")
        df_batch   = df.join(batch_keys, on=pk_col, how="inner")
        logger.info(f"[BATCH] Yield batch #{batch_idx + 1}/{num_batches}")
        yield batch_idx, df_batch

    df_keys.unpersist()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PIPELINE ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────
def run_unified_pipeline():
    """
    Điểm khởi chạy chính:
      1. Khởi động SparkSession
      2. Đăng ký PyFile cho workers
      3. Extract toàn bộ dữ liệu từ MongoDB + MinIO
      4. Chia thành batches (mỗi batch BATCH_SIZE rows)
      5. Mỗi batch chạy tuần tự: PREPROCESSING → PROCESSING
    """
    logger.info("=" * 70)
    logger.info("  SMART CHECKOUT — UNIFIED DATA PIPELINE")
    logger.info(f"  batch_size      = {BATCH_SIZE:,}")
    logger.info(f"  num_partitions  = {CONFIG['num_partitions']}")
    logger.info(f"  extract_limit   = {CONFIG['extract_limit'] or 'không giới hạn'}")
    logger.info(f"  use_api         = {CONFIG['use_api']}")
    logger.info("=" * 70)

    # ── 1. Khởi động Spark ───────────────────────────────────────────────
    logger.info("[STARTUP] Bắt đầu khởi tạo SparkSession...")
    spark = build_spark_session()
    logger.info("[STARTUP] Hoàn tất khởi tạo SparkSession.")

    # ── 2. Đăng ký PyFile cho workers ────────────────────────────────────
    logger.info("[SETUP] Đăng ký PyFile cho Spark workers...")
    _register_preprocessing_files(spark)
    _register_processing_files(spark)
    logger.info("[SETUP] Đăng ký PyFile hoàn tất.")

    # ── 3. Extract ────────────────────────────────────────────────────────
    logger.info("[PIPELINE] Bắt đầu Stage 0: Extract metadata...")
    df_full, total = stage_extract(spark, CONFIG)

    if total == 0:
        logger.warning("[PIPELINE] Không có dữ liệu nào được trích xuất. Kết thúc.")
        spark.stop()
        return

    # ── 4 + 5. Batch → Preprocessing → Processing ────────────────────────
    batch_stats = {"success": 0, "failed": 0}

    for batch_idx, df_batch in split_into_batches(df_full, BATCH_SIZE, total):
        logger.info("")
        logger.info(f"{'─' * 70}")
        logger.info(f"  BATCH #{batch_idx + 1} / {math.ceil(total / BATCH_SIZE)}")
        logger.info(f"{'─' * 70}")

        try:
            # ─── Stage 1: Preprocessing ───────────────────────────────────
            df_transformed = stage_preprocessing(spark, df_batch, batch_idx, CONFIG)

            # ─── Stage 2: Processing ──────────────────────────────────────
            stage_processing(spark, df_transformed, batch_idx, CONFIG)

            # Giải phóng DataFrame của batch này
            try:
                df_transformed.unpersist()
            except Exception:
                pass

            batch_stats["success"] += 1
            logger.info(f"  ✓ Batch #{batch_idx + 1} hoàn tất thành công.")

        except Exception as e:
            batch_stats["failed"] += 1
            logger.error(f"  ✗ Batch #{batch_idx + 1} thất bại: {e}", exc_info=True)
            # Tiếp tục batch tiếp theo thay vì dừng toàn bộ pipeline
            continue

    # ── 6. Tổng kết ───────────────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 70)
    logger.info("  PIPELINE KẾT THÚC")
    logger.info(f"  Tổng batches thành công : {batch_stats['success']}")
    logger.info(f"  Tổng batches thất bại   : {batch_stats['failed']}")
    logger.info("=" * 70)

    spark.stop()


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chạy Smart Checkout Pipeline")
    parser.add_argument("--limit", type=int, default=None, help="Giới hạn số lượng records trích xuất để test nhanh (ghi đè EXTRACT_LIMIT trong .env)")
    args = parser.parse_args()

    if args.limit is not None:
        CONFIG["extract_limit"] = args.limit
        
    run_unified_pipeline()
