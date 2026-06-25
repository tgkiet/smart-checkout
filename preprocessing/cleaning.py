import cv2
import numpy as np
from pyspark.sql import DataFrame
from pyspark.sql.functions import col, udf
from pyspark.sql.types import BinaryType

class DataCleaner:
    def __init__(self, config: dict):
        self.config = config

    @staticmethod
    def clean_metadata(df: DataFrame, path_column: str) -> DataFrame:
        """
        Làm sạch metadata: Bỏ qua các dòng có minio_path bị null hoặc thiếu thông tin sku, price, title hoặc name.
        """
        if "title" in df.columns and "name" in df.columns:
            has_name_col = col("title").isNotNull() | col("name").isNotNull()
        elif "title" in df.columns:
            has_name_col = col("title").isNotNull()
        elif "name" in df.columns:
            has_name_col = col("name").isNotNull()
        else:
            has_name_col = col("sku").isNotNull()

        cleaned_df = df.filter(
            col(path_column).isNotNull() &
            col("sku").isNotNull() &
            col("price").isNotNull() &
            has_name_col
        )
        return cleaned_df

    @staticmethod
    def detect_and_handle_blur(img: np.ndarray, gray: np.ndarray) -> np.ndarray:
        """Phát hiện và xử lý ảnh mờ (Blur)"""
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        if laplacian_var < 100:  # Threshold tùy chỉnh cho ảnh mờ
            # Tăng độ nét ảnh
            kernel_sharpening = np.array([[-1,-1,-1], 
                                          [-1, 9,-1],
                                          [-1,-1,-1]])
            img = cv2.filter2D(img, -1, kernel_sharpening)
        return img

    @staticmethod
    def handle_noise(img: np.ndarray) -> np.ndarray:
        """Xử lý nhiễu hạt (Noise/Grain)"""
        # Sử dụng Median Blur để giảm nhiễu mà vẫn giữ được chi tiết cạnh (tốt hơn Gaussian trong trường hợp nhiễu hạt)
        return cv2.medianBlur(img, 3)

    @staticmethod
    def handle_overexposure(img: np.ndarray, gray: np.ndarray) -> np.ndarray:
        """Xử lý ảnh chói sáng (Glare/Overexposure)"""
        mean_brightness = np.mean(gray)
        if mean_brightness > 200:  # Ảnh quá sáng
            # Dùng kỹ thuật CLAHE trên không gian màu LAB (chỉ tác động độ sáng, giữ nguyên màu)
            lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            cl = clahe.apply(l)
            limg = cv2.merge((cl, a, b))
            img = cv2.cvtColor(limg, cv2.COLOR_LAB2BGR)
        return img

    @staticmethod
    def process_image(img_bytes: bytearray) -> tuple:
        """
        Hàm xử lý chất lượng ảnh: xử lý nhiễu hạt, mờ, và chói sáng.
        Trả về tuple (bytearray_anh, trang_thai_chuoi)
        """
        if not img_bytes:
            return (None, "FAILED_EMPTY")
            
        try:
            # Decode ảnh từ chuỗi binary
            nparr = np.frombuffer(img_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if img is None:
                return (img_bytes, "FAILED_DECODE")
                
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                
            # 1. Phát hiện và xử lý ảnh mờ (Blur)
            img = DataCleaner.detect_and_handle_blur(img, gray)
                
            # 2. Xử lý nhiễu hạt (Noise/Grain)
            img = DataCleaner.handle_noise(img)
            
            # 3. Xử lý ảnh chói sáng (Glare/Overexposure)
            img = DataCleaner.handle_overexposure(img, gray)
                
            # Trả lại dạng binary
            success, encoded_img = cv2.imencode('.jpg', img)
            if success:
                return (bytearray(encoded_img), "SUCCESS")
            return (img_bytes, "FAILED_ENCODE")
            
        except Exception as e:
            # Trả về ảnh gốc nếu có lỗi xảy ra
            return (img_bytes, "FAILED_EXCEPTION")

    @staticmethod
    def get_image_cleaning_udf():
        from pyspark.sql.types import StructType, StructField, BinaryType, StringType
        schema = StructType([
            StructField("image_data", BinaryType(), True),
            StructField("state", StringType(), True)
        ])
        return udf(DataCleaner.process_image, schema)

    def process_clean_data(self, df: DataFrame) -> DataFrame:
        """
        Đóng gói tiến trình làm sạch dữ liệu.
        """
        path_column = self.config.get("path_column", "minio_image_path")
        
        # 1. Làm sạch Metadata
        df_cleaned_metadata = self.clean_metadata(df, path_column)
        
        # Lọc bỏ những dòng không lấy được ảnh ở bước extract
        df_cleaned_metadata = df_cleaned_metadata.filter(col("image_data").isNotNull())
        
        # 2. Làm sạch Ảnh
        clean_img_udf = self.get_image_cleaning_udf()
        df_cleaned_images = df_cleaned_metadata.withColumn("cleaning_result", clean_img_udf(col("image_data")))
        
        # Cập nhật lại cột ảnh bằng ảnh đã xử lý và thêm state
        df_final = df_cleaned_images.withColumn("image_data", col("cleaning_result.image_data")) \
                                    .withColumn("cleaning_state", col("cleaning_result.state")) \
                                    .drop("cleaning_result")
        
        return df_final

    def save_to_mongodb(self, df: DataFrame, database: str, collection: str):
        """
        Lưu dữ liệu vào collection mới trong MongoDB.
        Chỉ lưu metadata, bỏ cột image_data binary.
        """
        uri = self.config.get("mongo_uri")
        print(f"----- Lưu dữ liệu đã làm sạch vào MongoDB: {database}.{collection} -----")
        
        df_to_save = df.drop("image_data")
        
        df_to_save.write.format("mongodb") \
            .mode("append") \
            .option("spark.mongodb.write.connection.uri", uri) \
            .option("spark.mongodb.write.database", database) \
            .option("spark.mongodb.write.collection", collection) \
            .save()
