from pyspark.sql import SparkSession, DataFrame
from pyspark.sql.functions import col, udf
from pyspark.sql.types import BinaryType

class DataExtractor:
    def __init__(self, spark: SparkSession, config: dict):
        self.spark = spark
        self.config = config

    def get_mongo_source(self) -> DataFrame:
        """
        Sử dụng toán tử source (Data Source API) của Spark để lấy metadata 
        từ MongoDB làm điểm đầu vào của Spark Pipeline (Batch Mode).
        """
        mongo_uri = self.config.get("mongo_uri", "mongodb://root:rootpass@ssc-mongo:27017/?authSource=admin")
        database = self.config.get("mongo_db", "smart_checkout")
        collection = self.config.get("mongo_collection", "products")
        
        # Bỏ qua trường 'ld_json' do dữ liệu không đồng nhất gây lỗi parse schema
        pipeline = "[{'$project': {'ld_json': 0}}]"
        
        df = self.spark.read.format("mongodb") \
            .option("spark.mongodb.read.connection.uri", mongo_uri) \
            .option("spark.mongodb.read.database", database) \
            .option("spark.mongodb.read.collection", collection) \
            .option("spark.mongodb.read.aggregation.pipeline", pipeline) \
            .option("spark.mongodb.read.partitioner", "com.mongodb.spark.sql.connector.read.partitioner.PaginateBySizePartitioner") \
            .option("spark.mongodb.read.partitionerOptions.partitionSizeMB", "32") \
            .load()
        
        return df

    @staticmethod
    def get_image_from_minio_udf(minio_endpoint: str, access_key: str, secret_key: str):
        """
        Tạo một User-Defined Function (UDF) để fetch trực tiếp bytes hình ảnh 
        từ MinIO thông qua path được lấy ra từ metadata MongoDB.
        """
        def _fetch_image(path: str):
            if not path:
                return None
            
            try:
                # Khởi tạo MinIO client tại mỗi executor thay vì ở driver
                from minio import Minio
                client = Minio(
                    minio_endpoint,
                    access_key=access_key,
                    secret_key=secret_key,
                    secure=False
                )
                
                # Format path thường lưu theo dạng "bucket_name/object_name"
                clean_path = path.replace("s3://", "").replace("s3a://", "").strip()
                parts = clean_path.split('/', 1)
                if len(parts) != 2:
                    return None
                
                bucket_name, object_name = parts
                
                # Tải object từ MinIO
                response = client.get_object(bucket_name, object_name)
                data = response.read()
                
                # Đóng response connection pool
                response.close()
                response.release_conn()
                
                return bytearray(data)
            except Exception as e:
                # Có thể ghi log lỗi ở đây nếu cần, hiện tại trả về None nếu file bị lỗi
                return None

        return udf(_fetch_image, BinaryType())

    def extract_data_pipeline(self, limit_rows: int = None) -> DataFrame:
        """
        Pipeline trích xuất dữ liệu, bao gồm:
        1. Đọc metadata từ MongoDB bằng source operator.
        2. Chạy UDF để lấy ảnh từ MinIO thành binary column.
        """
        # 1. Đọc Dataframe từ MongoDB
        mongo_df = self.get_mongo_source()
        
        # --- THÊM LOGIC RESUME (ANTI-JOIN) ĐỂ CHECKPOINT PIPELINE ---
        try:
            df_processed = (
                self.spark.read.format("mongodb")
                .option("spark.mongodb.read.connection.uri", self.config.get("mongo_uri", "mongodb://root:rootpass@ssc-mongo:27017/?authSource=admin"))
                .option("spark.mongodb.read.database", "preprocessing")
                .option("spark.mongodb.read.collection", "transformed")
                .load()
            )
            if "_id" in df_processed.columns:
                df_processed_ids = df_processed.select("_id").withColumnRenamed("_id", "processed_id").distinct()
                mongo_df = mongo_df.withColumn("_id_str", col("_id").cast("string"))
                df_processed_ids = df_processed_ids.withColumn("processed_id_str", col("processed_id").cast("string"))
                mongo_df = mongo_df.join(
                    df_processed_ids,
                    mongo_df["_id_str"] == df_processed_ids["processed_id_str"],
                    "left_anti"
                ).drop("_id_str")
                import logging
                logging.getLogger(__name__).info("  [Resume] Đã lọc bỏ các dữ liệu đã tồn tại trong preprocessing.transformed.")
        except Exception as e:
            import logging
            logging.getLogger(__name__).info(f"  [Resume] Không lọc dữ liệu cũ (có thể là lần chạy đầu tiên): {e}")
        # -------------------------------------------------------------

        # Nếu có yêu cầu limit (dùng cho test), thực hiện limit NGAY TẠI ĐÂY
        if limit_rows:
            mongo_df = mongo_df.limit(limit_rows)
            
        # [QUAN TRỌNG] Repartition: Hàm limit() của Spark sẽ gom toàn bộ dữ liệu về 1 partition duy nhất.
        # Nếu để nguyên 1 partition này mà chạy OpenCV (heavy task) thì 1 luồng sẽ phải gánh hết toàn bộ dữ liệu gây treo rất lâu.
        # Cần chia nhỏ lại ra nhiều partition bằng số core (defaultParallelism) để xử lý song song.
        mongo_df = mongo_df.repartition(self.spark.sparkContext.defaultParallelism or 10)
        
        # 2. Sử dụng UDF map path sang image bytes (Được chạy song song sau repartition)
        path_column = self.config.get("path_column", "minio_path") # Cột chứa thông tin đường dẫn minio
        
        minio_endpoint = self.config.get("minio_endpoint", "ssc-minio:9000")
        access_key = self.config.get("minio_access_key", "admin")
        secret_key = self.config.get("minio_secret_key", "adminpass")
        
        fetch_image = self.get_image_from_minio_udf(minio_endpoint, access_key, secret_key)
        
        # 3. Tạo DataFrame trả về có chứa cột `image_data`
        enriched_df = mongo_df.withColumn("image_data", fetch_image(col(path_column)))
        
        return enriched_df
