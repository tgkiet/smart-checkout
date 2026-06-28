import os
import logging
from pyspark.sql import SparkSession
from extract_data import DataExtractor
from cleaning import DataCleaner

# Thiết lập thư mục logs
os.makedirs("logs", exist_ok=True)

# Cấu hình logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/data_preprocessing.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def process_extract_data(spark: SparkSession, config: dict):
    """
    Hàm đóng gói bước Extract dữ liệu từ MongoDB và MinIO.
    Trả về DataFrame chứa dữ liệu gốc kèm ảnh binary.
    """
    logger.info("Bắt đầu trích xuất dữ liệu từ MongoDB và MinIO...")
    extractor = DataExtractor(spark, config)
    
    # Truyền trực tiếp limit=1000 vào pipeline để tránh bị nghẽn (bottleneck) tại 1 partition
    df = extractor.extract_data_pipeline(limit_rows=1000)
    
    logger.info("----- SCHEMA DỮ LIỆU ĐÃ TRÍCH XUẤT -----")
    df.printSchema()
    
    logger.info(f"Tổng số lượng dữ liệu thu thập được: {df.count()}")
    return df

def process_clean(df, config):
    """Bước Cleaning Data (Metadata + Images)"""
    logger.info("Bắt đầu thực thi module Cleaning...")
    from cleaning import DataCleaner
    cleaner = DataCleaner(config)
    df_cleaned = cleaner.process_clean_data(df)
    return df_cleaned, cleaner

def process_integrate(df_cleaned, config):
    """Bước Integate: Chuẩn hóa schema"""
    logger.info("Bắt đầu thực thi module Integrate...")
    from integrate import DataIntegrator
    integrator = DataIntegrator(config)
    df_integrated = integrator.process_integrate_data(df_cleaned)
    return df_integrated, integrator

def process_transform(df_integrated, config):
    """Bước Transform: Đổi nền ảnh trắng"""
    logger.info("Bắt đầu thực thi module Transform...")
    from transform import DataTransformer
    transformer = DataTransformer(config)
    df_transformed = transformer.process_transform_data(df_integrated)
    return df_transformed, transformer

def run_pipeline(spark: SparkSession, config: dict):
    """
    Generator pipeline: yield dataframe sau mỗi bước.
    Trong PySpark, DataFrame là lazy-evaluation (tính toán lười).
    Việc yield từng DataFrame giúp tổ chức code dạng streaming/pipeline sạch hơn
    mặc dù bản chất Spark không nạp toàn bộ data vào RAM ở bước return.
    """
    # 1. Extract
    df = process_extract_data(spark, config)
    yield "extract", df, None
    
    # 2. Clean
    df_cleaned, cleaner = process_clean(df, config)
    yield "clean", df_cleaned, cleaner
    
    # 3. Integrate
    df_integrated, integrator = process_integrate(df_cleaned, config)
    yield "integrate", df_integrated, integrator
    
    # 4. Transform
    df_transformed, transformer = process_transform(df_integrated, config)
    yield "transform", df_transformed, transformer

def upload_images_to_minio(df, step_name, config):
    """
    Sử dụng mapPartitions để upload ảnh lên MinIO song song.
    Trả về DataFrame đã được bổ sung cột `minio_{step_name}_path` chứa path MinIO mới.
    Cho phép query ảnh đã qua từng bước xử lý từ MinIO sau này.
    """
    from pyspark.sql import SparkSession
    from pyspark.sql.types import StructType, StructField, StringType
    
    minio_endpoint = config.get("minio_endpoint", "ssc-minio:9000")
    access_key = config.get("minio_access_key", "admin")
    secret_key = config.get("minio_secret_key", "adminpass")
    
    def process_partition(iterator):
        from minio import Minio
        import io
        import uuid
        
        client = Minio(minio_endpoint, access_key=access_key, secret_key=secret_key, secure=False)
        bucket_name = "smart-checkout"
        
        # Đảm bảo bucket tồn tại
        try:
            if not client.bucket_exists(bucket_name):
                client.make_bucket(bucket_name)
        except:
            pass
            
        for row in iterator:
            try:
                # Kiểm tra xem có cột image_data không
                if "image_data" not in row or not row.image_data:
                    continue
                img_bytes = row.image_data
                
                # Tìm ID định danh cho file ảnh
                prod_id = None
                if "product_id" in row and row.product_id:
                    prod_id = str(row.product_id)
                elif "_id" in row and row["_id"]:
                    prod_id = str(row["_id"])
                elif "sku" in row and row.sku:
                    prod_id = str(row.sku)
                else:
                    prod_id = str(uuid.uuid4())
                
                # Đường dẫn lưu trên MinIO: preprocessing/<step>/<id>.jpg
                object_name = f"preprocessing/{step_name}/{prod_id}.jpg"
                data_stream = io.BytesIO(img_bytes)
                
                client.put_object(
                    bucket_name=bucket_name,
                    object_name=object_name,
                    data=data_stream,
                    length=len(img_bytes),
                    content_type="image/jpeg"
                )
                # Trả về (prod_id, path) để join lại vào DataFrame
                minio_path = f"s3://{bucket_name}/{object_name}"
                yield (prod_id, minio_path)
            except Exception as e:
                # Bỏ qua lỗi cục bộ của 1 tấm ảnh
                pass

    spark = SparkSession.getActiveSession()
    path_col_name = f"minio_{step_name}_path"
    
    logger.info(f"Đang đẩy luồng ảnh nhị phân của bước [{step_name}] lên MinIO...")
    
    # Thực hiện upload và thu thập các path đã upload thành công
    path_schema = StructType([
        StructField("product_id", StringType(), True),
        StructField(path_col_name, StringType(), True)
    ])
    rdd_paths = df.rdd.mapPartitions(process_partition)
    df_paths = spark.createDataFrame(rdd_paths, schema=path_schema)
    
    # Join path mới vào DataFrame gốc theo product_id
    if "product_id" in df.columns:
        df_enriched = df.join(df_paths, on="product_id", how="left")
    else:
        # Nếu không có product_id, thêm cột path rỗng
        from pyspark.sql.functions import lit
        df_enriched = df.withColumn(path_col_name, lit(None).cast("string"))
    
    logger.info(f"Hoàn tất đẩy ảnh bước [{step_name}] lên MinIO!")
    return df_enriched

def main():
    """
    Điểm khởi chạy Spark Pipeline cho tiền xử lý dữ liệu.
    """
    spark = SparkSession.builder \
        .appName("SmartCheckout_DataPreprocessing") \
        .config("spark.jars.packages", "org.mongodb.spark:mongo-spark-connector_2.13:10.4.0") \
        .getOrCreate()
        
    import os
    base_dir = os.path.dirname(os.path.abspath(__file__))
    spark.sparkContext.addPyFile(os.path.join(base_dir, "cleaning.py"))
    spark.sparkContext.addPyFile(os.path.join(base_dir, "extract_data.py"))
    spark.sparkContext.addPyFile(os.path.join(base_dir, "integrate.py"))
    spark.sparkContext.addPyFile(os.path.join(base_dir, "transform.py"))
        
    config = {
        "mongo_uri": "mongodb://root:rootpass@ssc-mongo:27017/?authSource=admin",
        "mongo_db": "smart_checkout",
        "mongo_collection": "products", # Thay bằng tên collection thực tế
        "path_column": "minio_image_path", # Thay bằng tên field lưu path minio trong MongoDB
        "minio_endpoint": "ssc-minio:9000",
        "minio_access_key": "admin",
        "minio_secret_key": "adminpass"
    }
    
    # Duyệt qua từng bước trong pipeline generator
    for step_name, current_df, module_instance in run_pipeline(spark, config):
        logger.info(f"===== ĐANG XỬ LÝ BƯỚC: {step_name.upper()} =====")
        
        # [QUAN TRỌNG ĐỂ TỐI ƯU TỐC ĐỘ]
        # Cache Dataframe ngay lập tức để ngắt chuỗi (DAG) của Spark. 
        # Nếu không có hàm này, ở mỗi lệnh Save (Action) tiếp theo, 
        # Spark sẽ chạy lại toàn bộ quy trình từ tải ảnh đến xử lý ảnh từ con số 0.
        current_df.cache()
        record_count = current_df.count() # Hàm Action ép Spark thực thi tính toán và giữ kết quả vào RAM
        
        logger.info(f"Số lượng record tại bước {step_name}: {record_count}")
        current_df.printSchema()
        
        # Ở các bước có instance lưu trữ (clean, integrate, transform), tiến hành lưu ở cả 2 nơi
        if step_name in ["clean", "integrate", "transform"]:
            # 1. Đẩy ảnh binary lên MinIO → nhận về DataFrame đã có thêm cột minio_<step>_path
            df_with_path = upload_images_to_minio(current_df, step_name, config)
            df_with_path.cache()
            
            # 2. Lưu metadata (kèm minio path, không bao gồm ảnh binary) xuống MongoDB
            # => Document trong MongoDB sẽ có trường minio_<step>_path để query ảnh sau này
            if module_instance and hasattr(module_instance, 'save_to_mongodb'):
                collection_name = "cleaning" if step_name == "clean" else ("integrated" if step_name == "integrate" else "transformed")
                module_instance.save_to_mongodb(df_with_path, "preprocessing", collection_name)
            
            df_with_path.unpersist()
                
        if step_name == "transform":
            logger.info("HOÀN TẤT TOÀN BỘ PIPELINE THỬ NGHIỆM!")

if __name__ == "__main__":
    main()
