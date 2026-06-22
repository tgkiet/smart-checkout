import os
import json
import logging
from dotenv import load_dotenv
from utils.gdrive_handler import GDriveHandler
from cleaning import DataCleaner
from storage import MongoStorage, MinioStorage

# Load biến môi trường từ file .env
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s')

# Cấu hình ID thư mục Google Drive
PRODUCTS_FOLDER_ID = os.environ.get("PRODUCTS_FOLDER_ID", "")
IMAGES_FOLDER_ID = os.environ.get("IMAGES_FOLDER_ID", "")

# Cấu hình database và storage
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
MONGO_DB = os.environ.get("MONGO_DB", "smart_checkout")
MONGO_COLLECTION = "products"

MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS = os.environ.get("MINIO_ACCESS", "minioadmin")
MINIO_SECRET = os.environ.get("MINIO_SECRET", "minioadmin")
MINIO_BUCKET = "products-images"

def collection():
    # 1. Khởi tạo các handler
    logging.info("Khởi tạo các kết nối (GDrive, Mongo, MinIO)...")
    try:
        gdrive = GDriveHandler()
        cleaner = DataCleaner()
        mongo = MongoStorage(uri=MONGO_URI, db_name=MONGO_DB)
        minio = MinioStorage(endpoint=MINIO_ENDPOINT, access_key=MINIO_ACCESS, secret_key=MINIO_SECRET, secure=False)
    except Exception as e:
        logging.error(f"Khởi tạo thất bại: {e}")
        return
        
    logging.info("Bắt đầu lấy dữ liệu sản phẩm từ Google Drive...")
    
    # 2. Duyệt qua các file trong thư mục Products (JSON)
    for file in gdrive.list_files(PRODUCTS_FOLDER_ID):
        file_name = file['name']
        file_id = file['id']
        
        if not file_name.endswith('.json'):
            continue
            
        logging.info(f"Đang xử lý metadata file: {file_name}")
        
        # Kiểm tra file đã được lưu trong MongoDB chưa
        if mongo.find_document(MONGO_COLLECTION, {"source_file": file_name}):
            logging.info(f"Bỏ qua {file_name} do đã tồn tại trong MongoDB.")
            continue
            
        try:
            # 3. Tải nội dung file JSON vào bộ nhớ
            json_bytes = gdrive.download_file_content(file_id)
            json_data = json.loads(json_bytes.decode('utf-8'))
        except Exception as e:
            logging.error(f"Lỗi khi tải hoặc parse JSON {file_name}: {e}")
            continue
            
        # 4. Làm sạch dữ liệu
        cleaned_data = cleaner.clean_metadata(json_data)
        if not cleaned_data:
            # Bỏ qua vì bị trùng lặp hoặc không hợp lệ
            continue
            
        cleaned_data['source_file'] = file_name
        
        # 5. Tìm ảnh tương ứng trong thư mục IMAGES (cùng tên, khác đuôi)
        base_name = os.path.splitext(file_name)[0]
        img_extensions = ['.jpg', '.jpeg', '.png']
        image_file = None
        
        for ext in img_extensions:
            image_name = base_name + ext
            image_file = gdrive.find_file_by_name(IMAGES_FOLDER_ID, image_name)
            if image_file:
                break
                
        # 6. Tải ảnh, làm sạch ảnh và lưu vào MinIO
        if image_file:
            try:
                image_bytes = gdrive.download_file_content(image_file['id'])
                
                # Làm sạch và tối ưu ảnh
                cleaned_image_bytes = cleaner.clean_image(image_bytes)
                if not cleaned_image_bytes:
                    logging.warning(f"Lỗi làm sạch ảnh {image_file['name']}, dùng ảnh gốc.")
                    cleaned_image_bytes = image_bytes
                
                content_type = "image/jpeg"
                minio_file_name = os.path.splitext(image_file['name'])[0] + ".jpg"
                
                minio_path = minio.upload_file(MINIO_BUCKET, minio_file_name, cleaned_image_bytes, content_type)
                logging.info(f"Đã lưu ảnh {minio_file_name} vào MinIO tại {minio_path}")
                
                # Cập nhật đường dẫn lưu ảnh vào metadata
                cleaned_data['minio_image_path'] = minio_path
            except Exception as e:
                logging.error(f"Lỗi khi xử lý ảnh {image_file['name']}: {e}")
                cleaned_data['minio_image_path'] = None
        else:
            logging.warning(f"Không tìm thấy ảnh tương ứng cho {file_name}")
            cleaned_data['minio_image_path'] = None
            
        # 7. Lưu metadata vào MongoDB
        try:
            mongo.insert_document(MONGO_COLLECTION, cleaned_data)
            logging.info(f"Đã lưu siêu dữ liệu {file_name} vào MongoDB.")
        except Exception as e:
            logging.error(f"Lỗi khi lưu metadata vào MongoDB: {e}")
            
    # Đóng kết nối
    mongo.close()
    logging.info("Hoàn tất luồng thu thập và lưu trữ!")

if __name__ == "__main__":
    main()
