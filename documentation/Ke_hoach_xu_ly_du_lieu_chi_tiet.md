# Kế hoạch Xử lý Dữ liệu Sản phẩm (Chi tiết theo Sample JSON)

Tài liệu này mô tả chi tiết kế hoạch xử lý dữ liệu từ dạng JSON thô (như dữ liệu sản phẩm từ Tiki) thành vector đặc trưng (embeddings) để phục vụ cho bài toán nhận diện sản phẩm tại quầy Smart Checkout.

Dữ liệu đầu vào là dạng **Ảnh đơn sản phẩm (Single-object image)**, ví dụ JSON có các trường `product_id`, `sku`, và `minio_image_path`. Mặc dù ảnh có thể chứa nhiều chi tiết phức tạp (người cầm túi, hậu cảnh), mục tiêu là chỉ bóc tách sản phẩm chính (cái túi).

Pipeline được chia làm 2 giai đoạn chính: **Preprocessing** (dùng PySpark) và **Processing** (dùng YOLO & Embedding Model).

---

## Giai đoạn 1: Preprocessing (Thư mục `preprocessing/`)

Mục tiêu của giai đoạn này là chuẩn hóa, làm sạch hàng triệu file JSON thô và lưu kết quả ra định dạng Parquet để chuẩn bị cho quá trình chạy Model inference. Luồng chính sử dụng **PySpark**.

### 1. `extract_data.py` (Trích xuất)
*   **Nhiệm vụ:** Đọc dữ liệu từ MongoDB hoặc các file JSON trên MinIO.
*   **Logic:**
    *   Giữ lại các trường quan trọng phục vụ pipeline: `_id`, `product_id`, `sku`, `name`, `storage_refs.bucket`, `minio_image_path`.
    *   Bỏ qua các trường không cần thiết (mô tả dài, badges, thông tin seller) để giảm kích thước dữ liệu.

### 2. `cleaning.py` (Làm sạch)
*   **Nhiệm vụ:** Lọc bỏ dữ liệu lỗi.
*   **Logic:**
    *   Lọc bỏ các records thiếu ID định danh (thiếu cả `product_id` và `sku`).
    *   Lọc bỏ các records không có ảnh (trường `minio_image_path` bị null hoặc rỗng).

### 3. `integrate.py` & `transform.py` (Chuẩn hóa và Tích hợp)
*   **Nhiệm vụ:** Chuẩn bị đường dẫn ảnh đầy đủ và gắn nhãn loại dữ liệu.
*   **Logic:**
    *   Tạo đường dẫn ảnh tuyệt đối (full_image_path): Nối giá trị `storage_refs.bucket` (ví dụ: `bronze_v2`) và `minio_image_path` (ví dụ: `products-images/b71b45...jpg`) thành `bronze_v2/products-images/b71b45...jpg`.
    *   Thêm trường `image_type = 'single_object'` để phân biệt luồng xử lý ở giai đoạn sau.

### 4. `data_preprocessing.py` (Orchestrator - Luồng chạy chính)
*   **Nhiệm vụ:** Dùng **PySpark** để điều phối các hàm logic trên.
*   **Quy trình:**
    1. Khởi tạo `SparkSession`.
    2. Đọc dữ liệu JSON/MongoDB thành Spark DataFrame.
    3. Áp dụng UDFs (User Defined Functions) bọc các logic từ `cleaning.py`, `extract_data.py`, v.v.
    4. Ghi DataFrame kết quả ra các file `.parquet` lưu tại thư mục đích (ví dụ `processed_data/single_object/`).

---

## Giai đoạn 2: Processing (Thư mục `processing/`)

Giai đoạn này nhận đầu vào là các file Parquet gọn nhẹ từ bước Preprocessing, thực hiện tải ảnh về và đưa qua các mô hình Deep Learning (chạy trên GPU).

### 1. `yolo_inference.py` (Phân mảnh - Segmentation)
*   **Nhiệm vụ:** Tách sản phẩm ra khỏi hậu cảnh.
*   **Logic:**
    *   Kéo ảnh từ MinIO dựa trên `full_image_path`.
    *   Đưa ảnh qua model YOLO Segmentation (đã train để nhận diện class chung `object`).
    *   **Xử lý ngoại lệ cho ảnh phức tạp (như ví dụ người cầm túi):** YOLO có thể ra 2 mask (người và túi). Cần thêm thuật toán lọc: Ưu tiên mask có bounding box tiệm cận tâm bức ảnh hoặc có tỷ lệ khung hình / diện tích hợp lý để lấy đúng sản phẩm.

### 2. `embedding.py` (Cắt và Trích xuất đặc trưng)
*   **Nhiệm vụ:** Xóa phông và lấy Vector đặc trưng.
*   **Logic:**
    *   Nhân (Multiply) ảnh gốc với YOLO mask để xóa trắng hậu cảnh (cắt bỏ phần tay, chân người, sân cỏ xung quanh túi).
    *   Cắt (Crop) sát vùng sản phẩm và resize về kích thước chuẩn của model (ví dụ 224x224).
    *   Đưa ảnh đã crop qua mô hình Embedding (ResNet, EfficientNet, CLIP) để trích xuất ra Vector đặc trưng (Feature Embedding 512 hoặc 1024 chiều).

### 3. `database_writer.py` (Lưu trữ kết quả)
*   **Nhiệm vụ:** Lưu lại kết quả để phục vụ quá trình Checkout thực tế.
*   **Logic:**
    *   Tải ảnh crop (đã xóa nền) lên lại MinIO (ví dụ vào bucket `silver` hoặc `gold`).
    *   Lưu vào **Vector Database** (như Milvus, Qdrant) hoặc MongoDB một bản ghi bao gồm:
        *   `product_id`
        *   `sku`
        *   `name`
        *   `embedding_vector`
        *   `minio_crop_path`

### 4. `main_processing.py`
*   **Nhiệm vụ:** Điều phối toàn bộ luồng tải file Parquet -> tải ảnh -> qua YOLO -> cắt ảnh -> qua Embedding model -> lưu DB. Thiết kế tối ưu có thể dùng Message Queue (Kafka) để chia nhỏ task ra cho nhiều worker (GPU).
