# Smart Checkout Service (Backend)

`ssc_service` là backend trung gian (API Gateway) được xây dựng bằng **FastAPI**. Nhiệm vụ chính của nó là kết nối Frontend giao diện người dùng với các mô hình Machine Learning chuyên sâu và cơ sở dữ liệu Vector để xử lý quy trình thanh toán tự động (Smart Checkout).

## Luồng Logic (Architecture & Logic)

Khi người dùng upload một bức ảnh chứa các sản phẩm lên hệ thống, service sẽ thực hiện luồng làm việc sau:

1. **Nhận dữ liệu (UploadFile)**: API endpoint `POST /checkout` tiếp nhận file ảnh từ giao diện.
2. **Segmentation**: 
   - Gọi API Inference Model (YOLOv8-seg tại `localhost:8800/api/v1/segment`).
   - Kết quả trả về gồm danh sách các bounding box và mask (dưới dạng base64) đại diện cho vị trí từng sản phẩm.
3. **Tiền xử lý Ảnh (Image Processing)**:
   - Module `utils/image.py` sẽ cắt (crop) các sản phẩm ra khỏi bức ảnh lớn.
   - Nếu có mask, nó sẽ tách nền (xóa phông) vùng xung quanh sản phẩm để lấy hình ảnh chính xác nhất.
4. **Embedding Vector**:
   - Từng ảnh sản phẩm vừa được cắt ra sẽ được gửi đến API Embedding (`localhost:8800/api/v1/embed`) để biến đổi bức ảnh thành một mảng vector đặc trưng.
5. **Similarity Search (Qdrant)**:
   - Mảng vector này được dùng để query trực tiếp vào **Qdrant Vector Database** (`localhost:6433`).
   - Hệ thống sẽ tìm ra sản phẩm có vector gần giống nhất (với độ tin cậy `score > 0.6`).
   - Trích xuất metadata như `name` (tên), `price` (giá), `sku` của sản phẩm đó.
6. **Tổng hợp**:
   - Tổng hợp các sản phẩm đã nhận diện và tính tổng giá tiền, trả về cho Frontend hiển thị.

## Cấu trúc thư mục

- `main.py`: Entry point chính, khởi tạo ứng dụng và config middlewares.
- `routers/checkout.py`: Xử lý HTTP request và ráp nối luồng nghiệp vụ.
- `services/inference.py`: Helper để kết nối HTTP calls tới inference-api.
- `services/vector_db.py`: Singleton xử lý Qdrant client và search.
- `utils/image.py`: Thao tác numpy/Pillow cho ma trận ảnh.
- `config.py`: Tham số cấu hình (host, port, endpoint).

## Hướng dẫn cài đặt và chạy (Run Guide)

### 1. Cài đặt Dependencies
Bạn cần tạo môi trường ảo và cài đặt các thư viện cần thiết:
```bash
cd ssc_service
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Khởi chạy Services nền (Docker)
Đảm bảo rằng các services AI và Vector DB ở nền đã được bật (sử dụng docker-compose của dự án ở mục `docker/docker_compose/`):
- **Inference API**: Port `8800`
- **Qdrant**: Port `6433`

### 3. Chạy FastAPI Server
Mở terminal tại thư mục `ssc_service` và chạy:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
API sẽ lắng nghe tại: `http://localhost:8000`
Bạn có thể xem Swagger UI Document tại: `http://localhost:8000/docs`
