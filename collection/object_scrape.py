import os
import json
import logging
import concurrent.futures
import threading
from pymongo import UpdateOne
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

# Thread-local storage to keep non-thread-safe clients (like googleapiclient) safe
thread_local = threading.local()

def get_handlers():
    if not hasattr(thread_local, "gdrive"):
        thread_local.gdrive = GDriveHandler()
    if not hasattr(thread_local, "cleaner"):
        thread_local.cleaner = DataCleaner()
    if not hasattr(thread_local, "minio"):
        thread_local.minio = MinioStorage(endpoint=MINIO_ENDPOINT, access_key=MINIO_ACCESS, secret_key=MINIO_SECRET, secure=False)
    return thread_local.gdrive, thread_local.cleaner, thread_local.minio

def process_file(file, processed_files, image_dict):
    gdrive, cleaner, minio = get_handlers()
    file_name = file['name']
    file_id = file['id']
    
    if not file_name.endswith('.json'):
        return None
        
    base_name = os.path.splitext(file_name)[0]
    img_extensions = ['.jpg', '.jpeg', '.png']
    
    # Kiểm tra Checkpoint (Tránh miss dữ liệu)
    if file_name in processed_files:
        has_minio_image = processed_files[file_name] is not None
        
        # Nếu đã có cả metadata và đường dẫn ảnh -> Xử lý hoàn thiện, bỏ qua
        if has_minio_image:
            logging.debug(f"Bỏ qua {file_name} do đã xử lý hoàn tất (có ảnh).")
            return None
            
        # Nếu đã có metadata nhưng CHƯA có ảnh -> Kiểm tra GDrive xem có ảnh không
        has_image_in_gdrive = any(base_name + ext in image_dict for ext in img_extensions)
        
        if not has_image_in_gdrive:
            # GDrive vẫn chưa có ảnh -> Bỏ qua
            logging.debug(f"Bỏ qua {file_name} do đã lưu metadata và GDrive vẫn chưa có ảnh.")
            return None
            
        # GDrive CÓ ảnh mới -> Tiếp tục xử lý để bổ sung ảnh!
        logging.info(f"Phát hiện ảnh mới cho {file_name}. Tiến hành cập nhật...")
    else:
        logging.info(f"Đang xử lý metadata file mới: {file_name}")
        
    try:
        # Tải nội dung file JSON vào bộ nhớ
        json_bytes = gdrive.download_file_content(file_id)
        json_data = json.loads(json_bytes.decode('utf-8'))
    except Exception as e:
        logging.error(f"Lỗi khi tải hoặc parse JSON {file_name}: {e}")
        return None
        
    # Làm sạch dữ liệu
    cleaned_data = cleaner.clean_metadata(json_data)
    if not cleaned_data:
        return None
        
    cleaned_data['source_file'] = file_name
    
    # Tìm ảnh tương ứng O(1) in-memory
    image_file = None
    for ext in img_extensions:
        image_name = base_name + ext
        if image_name in image_dict:
            image_file = {'name': image_name, 'id': image_dict[image_name]}
            break
            
    # Tải ảnh, làm sạch ảnh và lưu vào MinIO
    if image_file:
        try:
            image_bytes = gdrive.download_file_content(image_file['id'])
            
            cleaned_image_bytes = cleaner.clean_image(image_bytes)
            if not cleaned_image_bytes:
                logging.warning(f"Lỗi làm sạch ảnh {image_file['name']}, dùng ảnh gốc.")
                cleaned_image_bytes = image_bytes
            
            content_type = "image/jpeg"
            minio_file_name = os.path.splitext(image_file['name'])[0] + ".jpg"
            
            minio_path = minio.upload_file(MINIO_BUCKET, minio_file_name, cleaned_image_bytes, content_type)
            logging.info(f"Đã upload ảnh {minio_file_name} vào MinIO: {minio_path}")
            
            cleaned_data['minio_image_path'] = minio_path
        except Exception as e:
            logging.error(f"Lỗi khi xử lý ảnh {image_file['name']}: {e}")
            cleaned_data['minio_image_path'] = None
    else:
        logging.warning(f"Không tìm thấy ảnh tương ứng cho {file_name}")
        cleaned_data['minio_image_path'] = None
        
    # Trả về data để main thread batch insert
    return cleaned_data

def collection():
    logging.info("Khởi tạo kết nối tạm để tải cache...")
    try:
        gdrive = GDriveHandler()
        mongo = MongoStorage(uri=MONGO_URI, db_name=MONGO_DB)
    except Exception as e:
        logging.error(f"Khởi tạo thất bại: {e}")
        return
        
    logging.info("Tải danh sách file đã xử lý từ MongoDB...")
    # Lấy metadata gồm tên file VÀ đường dẫn ảnh MinIO để làm Checkpoint
    cursor = mongo.db[MONGO_COLLECTION].find({}, {"source_file": 1, "minio_image_path": 1})
    processed_files = {doc['source_file']: doc.get('minio_image_path') for doc in cursor if 'source_file' in doc}
    logging.info(f"Đã tìm thấy {len(processed_files)} file đã xử lý trước đó.")

    logging.info("Tải danh sách ID toàn bộ hình ảnh từ Google Drive...")
    image_dict = {f['name']: f['id'] for f in gdrive.list_files(IMAGES_FOLDER_ID)}
    logging.info(f"Đã load danh sách {len(image_dict)} hình ảnh.")

    logging.info("Tải danh sách file Products metadata...")
    files = list(gdrive.list_files(PRODUCTS_FOLDER_ID))
    logging.info(f"Tìm thấy tổng cộng {len(files)} file metadata.")

    logging.info("Bắt đầu xử lý song song và Bulk Insert vào MongoDB...")
    
    batch_size = 50
    operations = []
    
    # Process files concurrently with 10 workers
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        # Dùng executor.map thay vì submit/as_completed để dễ quản lý dữ liệu trả về hơn,
        # tuy nhiên submit/as_completed tối ưu hơn nếu các task thời gian chạy chênh lệch.
        futures = [executor.submit(process_file, file, processed_files, image_dict) for file in files]
        
        for future in concurrent.futures.as_completed(futures):
            try:
                result_data = future.result()
                if result_data:
                    # Tạo truy vấn Update hoặc Insert (Upsert) cho MongoDB
                    op = UpdateOne(
                        {"source_file": result_data["source_file"]},
                        {"$set": result_data},
                        upsert=True
                    )
                    operations.append(op)
                    
                    # Nếu đạt đủ Batch Size thì thực hiện Bulk Write
                    if len(operations) >= batch_size:
                        mongo.db[MONGO_COLLECTION].bulk_write(operations)
                        logging.info(f"Đã lưu/cập nhật {len(operations)} records vào MongoDB theo batch.")
                        operations.clear()
            except Exception as e:
                logging.error(f"Lỗi trong quá trình chạy thread: {e}")

    # Ghi nốt phần còn lại của batch
    if operations:
        try:
            mongo.db[MONGO_COLLECTION].bulk_write(operations)
            logging.info(f"Đã lưu {len(operations)} records cuối cùng vào MongoDB.")
        except Exception as e:
            logging.error(f"Lỗi khi lưu batch cuối cùng: {e}")

    mongo.close()
    logging.info("Hoàn tất luồng thu thập và lưu trữ!")

if __name__ == "__main__":
    collection()
