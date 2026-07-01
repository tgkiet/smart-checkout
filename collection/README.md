# Data Collection Service (Smart Checkout)

Thư mục `collection` chứa toàn bộ luồng xử lý (Data Pipeline - ETL) để thu thập, làm sạch và lưu trữ dữ liệu sản phẩm từ nguồn (Google Drive) vào các hệ thống cơ sở dữ liệu (MongoDB và MinIO).

## 🗂️ Cấu trúc thư mục và Nhiệm vụ

- `object_scrape.py`: Kịch bản điều phối chính (Orchestrator). Nơi gắn kết tất cả các module lại với nhau để tạo thành một luồng chạy hoàn chỉnh.
- `utils/gdrive_handler.py`: Module giao tiếp với Google Drive API (để đọc danh sách file, tìm kiếm và tải file).
- `cleaning.py`: Module làm sạch và tối ưu hoá dữ liệu (Data Cleaner).
- `storage.py`: Module kết nối và lưu trữ dữ liệu vào MongoDB và hệ thống lưu trữ Object (MinIO).
- `gdrive_collector.py`: (Legacy) Script thu thập dữ liệu nguyên khối cũ (đã được refactor tách ra các module trên).

---

## 🔄 Luồng hoạt động chi tiết (Flow)

Luồng hoạt động chính được thực thi thông qua lệnh: `python collection/object_scrape.py`

### Bước 1: Khởi tạo các kết nối (Initialization)
Script sẽ thiết lập các kết nối tới các dịch vụ phụ thuộc, bao gồm:
1. **Google Drive API**: Thông qua `GDriveHandler` với xác thực OAuth2 (sử dụng `credentials.json` và `token.json`).
2. **MongoDB**: Khởi tạo `MongoStorage` thông qua URI được cấu hình trong file `.env` để lưu trữ văn bản (metadata).
3. **MinIO**: Khởi tạo `MinioStorage` để lưu trữ đối tượng (hình ảnh) với tài khoản/mật khẩu trong file `.env`.

### Bước 2: Quét thư mục Google Drive (Extraction)
- `gdrive_handler` duyệt qua `PRODUCTS_FOLDER_ID` trên Google Drive.
- Nó chỉ lấy ra các tệp tin có định dạng JSON (Metadata của từng sản phẩm).
- Trước khi xử lý tiếp, script sẽ query thử vào MongoDB để kiểm tra xem file này đã từng được đưa vào cơ sở dữ liệu chưa (thông qua `source_file`). Nếu có, nó sẽ **Bỏ qua** để tránh trùng lặp dư thừa.

### Bước 3: Tải và Làm sạch Dữ liệu Text (Transformation - Text)
- Kéo nội dung file JSON vào bộ nhớ (Memory).
- Chuyển dữ liệu qua `DataCleaner.clean_metadata`:
  - **Khử trùng lặp (Deduplication)**: Kiểm tra ID/Barcode để đảm bảo không bị trùng.
  - **Chuẩn hóa (Normalization)**: Cắt bỏ khoảng trắng thừa (strip) trong các chuỗi string.
  - **Xác thực (Validation)**: Đưa các trường về đúng định dạng (VD: Ép giá tiền `price` về kiểu `float`).

### Bước 4: Tải và Tối ưu Hình ảnh (Transformation - Image)
- Dựa trên tên của file JSON (VD: `product_A.json`), script đi tìm ảnh tương ứng (`product_A.jpg`, `.png`, v.v.) trong thư mục `IMAGES_FOLDER_ID`.
- Nếu tìm thấy, tải byte ảnh về và đẩy qua `DataCleaner.clean_image`:
  - **Chuyển đổi hệ màu**: Nếu ảnh là RGBA/P, convert qua RGB (chuẩn hóa về JPEG).
  - **Resize**: Thu nhỏ (thumbnail) ảnh nếu ảnh vượt quá `800x800` để tiết kiệm băng thông mạng và dung lượng lưu trữ nhưng vẫn giữ đúng tỷ lệ hình học.
  - **Nén chất lượng**: Đưa về format JPEG với quality tối ưu.

### Bước 5: Lưu trữ Hệ thống (Load)
- **Hình ảnh**: Đẩy bytes ảnh đã được tối ưu hoá lên bucket `products-images` trên **MinIO**. Kết quả trả về là đường dẫn (MinIO path).
- **Metadata**: Đường dẫn ảnh MinIO ở trên được gán ngược lại vào object JSON (`minio_image_path`). Sau đó toàn bộ document JSON hoàn chỉnh này sẽ được `insert` vào collection `products` của **MongoDB**.

---

## ⚙️ Yêu cầu cấu hình (Environment Variables)

Các biến cấu hình này cần được khai báo trong file `.env` tại thư mục gốc:

```env
# Google Drive Folder IDs
PRODUCTS_FOLDER_ID=your_products_folder_id
IMAGES_FOLDER_ID=your_images_folder_id

# MongoDB Connections
MONGO_URI=mongodb://root:rootpass@localhost:27917/
MONGO_DB=smart_checkout

# MinIO Connections
MINIO_ENDPOINT=localhost:9200
MINIO_ACCESS=admin
MINIO_SECRET=adminpass
```

## 🚀 Cách chạy
Môi trường ảo (virtualenv) cần được kích hoạt trước khi chạy:
```bash
# Từ thư mục gốc (smart-checkout)
.venv/bin/python collection/object_scrape.py
```
