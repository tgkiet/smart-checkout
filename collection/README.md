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

Bạn có thể xem biểu đồ luồng hoạt động (Data Collection Flow) dưới đây:

```mermaid
flowchart TD
    Start([Bắt đầu]) --> Init[Bước 1: Khởi tạo kết nối<br/>GDrive, MongoDB, MinIO]
    Init --> LoadCache[Tải Checkpoint từ MongoDB<br/>Danh sách file đã xử lý]
    LoadCache --> LoadGDrive[Bước 2: Quét thư mục GDrive<br/>Lấy danh sách ID file JSON và Ảnh]
    
    LoadGDrive --> ThreadPool[Bắt đầu xử lý song song<br/>ThreadPoolExecutor - 10 workers]
    
    subgraph Vòng lặp: Xử lý từng file JSON
        ThreadPool --> CheckJSON{Đã xử lý<br/>trước đó?}
        
        CheckJSON -->|Đã có metadata & ảnh| Skip[Bỏ qua]
        CheckJSON -->|Đã có metadata nhưng thiếu ảnh| CheckImgGDrive{GDrive có<br/>ảnh mới?}
        CheckImgGDrive -->|Không| Skip
        CheckImgGDrive -->|Có| Process[Tiến hành tải &<br/>xử lý]
        CheckJSON -->|Chưa xử lý| Process
        
        Process --> DownloadJSON[Bước 3: Tải & làm sạch<br/>Metadata JSON]
        DownloadJSON --> FindImg{Tìm thấy ảnh<br/>tương ứng?}
        
        FindImg -->|Có| DownloadImg[Bước 4: Tải ảnh &<br/>Làm sạch ảnh]
        DownloadImg --> UploadMinIO[Bước 5: Lưu trữ<br/>Upload ảnh lên MinIO]
        UploadMinIO --> MergeData[Gộp MinIO path<br/>vào Metadata]
        
        FindImg -->|Không| MergeDataNoImg[Metadata không<br/>có ảnh]
        MergeDataNoImg --> ReturnResult
        MergeData --> ReturnResult[Trả về kết quả<br/>cho Main Thread]
    end
    
    Skip --> NextTask[Task tiếp theo]
    ReturnResult --> BatchCheck{Đủ batch<br/>50 records?}
    
    BatchCheck -->|Có| BulkWrite[Bulk Write / Upsert<br/>vào MongoDB]
    BatchCheck -->|Chưa đủ| Wait[Chờ thêm kết quả]
    
    BulkWrite --> NextTask
    
    Wait -.-> ThreadPool
    NextTask -.-> ThreadPool
    
    NextTask -->|Đã duyệt hết| FinalWrite[Lưu batch cuối<br/>vào MongoDB]
    FinalWrite --> End([Kết thúc])
```

### Giải thích các bước:
1. **Khởi tạo các kết nối (Initialization):** Thiết lập kết nối tới Google Drive API, MongoDB và MinIO để chuẩn bị tải và lưu trữ.
2. **Quét thư mục Google Drive (Extraction):** Quét lấy danh sách file metadata (JSON) và hình ảnh. Kiểm tra Checkpoint từ MongoDB để tối ưu hóa, tự động **bỏ qua** những file đã có ảnh và metadata đầy đủ nhằm tránh chạy lại (resume capability).
3. **Tải và Làm sạch Dữ liệu Text (Transformation - Text):** Tải nội dung file JSON vào bộ nhớ, xử lý chuẩn hóa và kiểm tra tính hợp lệ của các trường thông tin.
4. **Tải và Tối ưu Hình ảnh (Transformation - Image):** Nếu tìm thấy ảnh tương ứng trong Google Drive, hệ thống sẽ tải về, tự động resize và nén (chuyển đổi định dạng, loại bỏ hệ màu không cần thiết) thông qua module DataCleaner.
5. **Lưu trữ Hệ thống (Load):** Đẩy hình ảnh đã được xử lý lên Bucket của MinIO để lấy đường dẫn. Toàn bộ record metadata hoàn chỉnh sẽ được đưa vào hàng chờ. Khi đủ **Batch 50 records**, hệ thống sẽ thực hiện **Bulk Write (Upsert)** vào MongoDB để tối ưu tốc độ ghi dữ liệu.

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
