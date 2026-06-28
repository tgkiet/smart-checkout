import cv2
import numpy as np
import logging
from pyspark.sql import DataFrame
from pyspark.sql.functions import col, udf
from pyspark.sql.types import BinaryType

logger = logging.getLogger(__name__)

class DataTransformer:
    def __init__(self, config: dict):
        self.config = config

    @staticmethod
    def remove_background_to_white(img_bytes: bytearray) -> tuple:
        """
        Đổi nền ảnh sang màu trắng.
        Trả về tuple (bytearray_anh_moi, trang_thai_chuoi)
        """
        if not img_bytes:
            return (None, "FAILED_EMPTY")
            
        try:
            # --- CÁCH 2: Dùng OpenCV GrabCut (Mặc định) ---
            nparr = np.frombuffer(img_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return (img_bytes, "FAILED_DECODE")
                
            # Tạo mask
            mask = np.zeros(img.shape[:2], np.uint8)
            
            # Khởi tạo mô hình nền/foreground cho GrabCut
            bgdModel = np.zeros((1, 65), np.float64)
            fgdModel = np.zeros((1, 65), np.float64)
            
            # Định nghĩa vùng ROI giả định object nằm trong khoảng 90% diện tích ảnh
            h, w = img.shape[:2]
            rect = (int(w * 0.05), int(h * 0.05), int(w * 0.9), int(h * 0.9))
            
            # Chạy GrabCut để tìm background (cơ bản - 5 iterations)
            cv2.grabCut(img, mask, rect, bgdModel, fgdModel, 5, cv2.GC_INIT_WITH_RECT)
            
            # Tạo mask nhị phân: 2 (PR_BGD) hoặc 0 (BGD) thì set 0, ngược lại 1
            mask2 = np.where((mask == 2) | (mask == 0), 0, 1).astype('uint8')
            
            # Lấy vùng object
            img_fg = img * mask2[:, :, np.newaxis]
            
            # Tạo nền trắng
            white_bg = np.ones_like(img, np.uint8) * 255
            
            # Nghịch đảo mask để lấy nền
            mask_inv = np.where((mask == 2) | (mask == 0), 1, 0).astype('uint8')
            white_bg = white_bg * mask_inv[:, :, np.newaxis]
            
            # Hợp nhất: Object (Foreground) + Nền trắng (Background)
            final_img = img_fg + white_bg
            
            success, encoded_img = cv2.imencode('.jpg', final_img)
            if success:
                return (bytearray(encoded_img), "SUCCESS")
            return (img_bytes, "FAILED_ENCODE")
            
        except Exception as e:
            return (img_bytes, "FAILED_EXCEPTION")

    def get_bg_removal_udf(self):
        from pyspark.sql.types import StructType, StructField, BinaryType, StringType
        schema = StructType([
            StructField("image_data", BinaryType(), True),
            StructField("state", StringType(), True)
        ])
        return udf(DataTransformer.remove_background_to_white, schema)

    def process_transform_data(self, df: DataFrame) -> DataFrame:
        """
        Đóng gói pipeline transform dữ liệu.
        """
        logger.info("Bắt đầu transform dữ liệu (Đổi nền sang trắng)...")
        
        if "image_data" not in df.columns:
            logger.warning("Không tìm thấy cột `image_data` để transform.")
            return df
            
        bg_removal_udf = self.get_bg_removal_udf()
        
        # Thêm/Cập nhật cột ảnh với ảnh nền trắng và sinh ra state
        df_transformed = df.withColumn("transform_result", bg_removal_udf(col("image_data")))
        
        # Giải nén kết quả
        df_final = df_transformed.withColumn("image_data", col("transform_result.image_data")) \
                                 .withColumn("transform_state", col("transform_result.state")) \
                                 .drop("transform_result")
                                 
        # Gom các biến state thành một cột cấu trúc (struct) metadata
        from pyspark.sql.functions import struct
        df_final = df_final.withColumn("pipeline_state", struct(
            col("cleaning_state"),
            col("integrate_state"),
            col("transform_state")
        )).drop("cleaning_state", "integrate_state", "transform_state")
        
        # Giữ lại minio_image_path của ảnh gốc (sau khi transform) để query
        # Đồng thời lưu riêng minio_transform_path để có thể phân biệt với ảnh gốc
        logger.info("Hoàn tất chuyển nền ảnh sang trắng!")
        return df_final

    def save_to_mongodb(self, df: DataFrame, database: str, collection: str):
        """
        Lưu dữ liệu vào collection mới trong MongoDB.
        Giữ lại minio_image_path và minio_transform_path để có thể query ảnh từ MinIO sau này.
        """
        uri = self.config.get("mongo_uri")
        logger.info(f"----- Lưu dữ liệu đã transform vào MongoDB: {database}.{collection} -----")
        
        # Bỏ binary image_data nhưng GIỮ LẠI các path để query ảnh sau này
        df_to_save = df.drop("image_data")
        
        df_to_save.write.format("mongodb") \
            .mode("append") \
            .option("spark.mongodb.write.connection.uri", uri) \
            .option("spark.mongodb.write.database", database) \
            .option("spark.mongodb.write.collection", collection) \
            .save()
        logger.info(f"Lưu thành công vào {database}.{collection} (giữ đường dẫn MinIO để query)")
