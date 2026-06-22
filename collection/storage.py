import logging
import io
from pymongo import MongoClient
from minio import Minio
from minio.error import S3Error

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s')

class MongoStorage:
    def __init__(self, uri="mongodb://localhost:27017/", db_name="smart_checkout"):
        try:
            self.client = MongoClient(uri)
            self.db = self.client[db_name]
        except Exception as e:
            logging.error(f"Không thể kết nối MongoDB: {e}")
            raise

    def insert_document(self, collection_name, document):
        try:
            collection = self.db[collection_name]
            return collection.insert_one(document).inserted_id
        except Exception as e:
            logging.error(f"Lỗi khi insert vào MongoDB: {e}")
            raise
            
    def find_document(self, collection_name, query):
        try:
            collection = self.db[collection_name]
            return collection.find_one(query)
        except Exception as e:
            logging.error(f"Lỗi khi tìm kiếm trong MongoDB: {e}")
            raise

    def close(self):
        self.client.close()

class MinioStorage:
    def __init__(self, endpoint="localhost:9000", access_key="minioadmin", secret_key="minioadmin", secure=False):
        try:
            self.client = Minio(
                endpoint,
                access_key=access_key,
                secret_key=secret_key,
                secure=secure
            )
        except Exception as e:
            logging.error(f"Không thể khởi tạo MinIO client: {e}")
            raise

    def upload_file(self, bucket_name, object_name, data_bytes, content_type="application/octet-stream"):
        try:
            if not self.client.bucket_exists(bucket_name):
                self.client.make_bucket(bucket_name)
                
            data_stream = io.BytesIO(data_bytes)
            length = len(data_bytes)
            
            self.client.put_object(
                bucket_name,
                object_name,
                data_stream,
                length,
                content_type=content_type
            )
            # Trả về đường dẫn lưu trên MinIO
            return f"{bucket_name}/{object_name}"
        except S3Error as e:
            logging.error(f"Lỗi S3 khi thao tác với MinIO: {e}")
            raise
        except Exception as e:
            logging.error(f"Lỗi khi upload file lên MinIO: {e}")
            raise
