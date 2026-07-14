import logging
from pyspark.sql import DataFrame
from pyspark.sql.functions import col, lit, coalesce

logger = logging.getLogger(__name__)

class DataIntegrator:
    def __init__(self, config: dict):
        self.config = config

    def process_integrate_data(self, df: DataFrame) -> DataFrame:
        """
        Chuẩn hóa schema dữ liệu về định dạng duy nhất.
        Schema: product_id, platform, sku, name, price
        """
        logger.info("Bắt đầu chuẩn hóa schema dữ liệu (Integrate)...")
        
        # Xử lý các cột name hoặc title
        logger.info("  > Mapping cột 'name' và 'title'...")
        if "title" in df.columns and "name" in df.columns:
            name_col = coalesce(col("name"), col("title"))
        elif "title" in df.columns:
            name_col = col("title")
        elif "name" in df.columns:
            name_col = col("name")
        else:
            name_col = lit(None).cast("string")
            
        # Xác định cột platform
        if "platform" in df.columns:
            platform_col = col("platform")
        elif "source" in df.columns:
            platform_col = col("source")
        else:
            platform_col = lit("unknown").cast("string")
            
        # Xác định product_id (thường là _id trong MongoDB, có thể là kiểu ObjectId -> string)
        if "product_id" in df.columns:
            product_id_col = col("product_id").cast("string")
        elif "_id" in df.columns:
            product_id_col = col("_id").cast("string")
        else:
            product_id_col = lit(None).cast("string")
            
        # Khai báo các cột state
        if "cleaning_state" in df.columns:
            cleaning_state_col = col("cleaning_state")
        else:
            cleaning_state_col = lit("UNKNOWN").cast("string")

        # Giữ lại minio_image_path nếu có, để query được ảnh từ MinIO sau này
        if "minio_image_path" in df.columns:
            minio_path_col = col("minio_image_path").cast("string")
        else:
            minio_path_col = lit(None).cast("string")

        # Select và alias các cột theo schema chuẩn (Giữ lại image_data để Transform)
        integrated_df = df.select(
            product_id_col.alias("product_id"),
            platform_col.alias("platform"),
            col("sku").cast("string").alias("sku"),
            name_col.cast("string").alias("name"),
            col("price").cast("double").alias("price"),
            col("image_data"),
            minio_path_col.alias("minio_image_path"),
            cleaning_state_col.alias("cleaning_state"),
            lit("SUCCESS").cast("string").alias("integrate_state")
        )
        
        logger.info("Hoàn thành quá trình định nghĩa chuẩn hóa schema.")
        return integrated_df

    def save_to_mongodb(self, df: DataFrame, database: str, collection: str):
        """
        Lưu dữ liệu vào collection mới trong MongoDB.
        Giữ lại minio_image_path để có thể query ảnh từ MinIO sau này.
        """
        uri = self.config.get("mongo_uri")
        logger.info(f"----- Lưu dữ liệu đã integrate vào MongoDB: {database}.{collection} -----")
        
        # Bỏ binary image_data nhưng GIỮ LẠI minio_image_path để query
        df_to_save = df.drop("image_data")
        
        df_to_save.write.format("mongodb") \
            .mode("append") \
            .option("spark.mongodb.write.connection.uri", uri) \
            .option("spark.mongodb.write.database", database) \
            .option("spark.mongodb.write.collection", collection) \
            .save()
        logger.info(f"Lưu thành công vào {database}.{collection} (giữ minio_image_path để query)")
