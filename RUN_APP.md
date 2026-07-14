# Hướng dẫn khởi chạy Ứng dụng (Backend & Frontend)

Hệ thống được thiết kế nguyên khối (monolith) tiện dụng. Giao diện Frontend (`ssc_ui`) đã được liên kết trực tiếp vào cùng một server FastAPI (`ssc_service`), nên bạn **chỉ cần khởi chạy backend là giao diện cũng sẽ hoạt động tự động**.

## 1. Yêu cầu & Cài đặt môi trường
Đảm bảo bạn đang ở thư mục `smart-checkout` và đã cài đặt đủ các gói phụ thuộc.

```bash
cd ssc_service
pip install -r requirements.txt
```
*(Yêu cầu phải có `fastapi`, `uvicorn`, `python-multipart`)*

## 2. Lệnh Khởi chạy
Tại thư mục `ssc_service`, chạy lệnh:

```bash
python -m uvicorn main:app --host 0.0.0.0 --port 8801 --reload
```
*(Hệ thống AI xử lý ở cổng `8800` và Qdrant ở `6433` phải đang được bật trước).*

## 3. Link Truy cập
Sau khi Terminal báo `Application startup complete`, mở trình duyệt web lên và truy cập:

- 🌐 **Giao diện người dùng (UI):** [http://localhost:8801/](http://localhost:8801/)
*(Server sẽ tự động điều hướng vào trang `/ui/index.html`)*

- ⚙️ **Tài liệu API (Swagger):** [http://localhost:8801/docs](http://localhost:8801/docs)

## 4. Cách sử dụng (UI)
1. Bấm nút **Tạo phiên mới** (Session) ở trang chào mừng.
2. Tại màn hình Workspace, kéo thả các bức ảnh lên khu vực nét đứt hoặc bấm "Chọn ảnh". Bạn có thể up 5-10 ảnh một lúc.
3. Bấm **Checkout tất cả** để hệ thống gọi AI tính toán các ảnh (Song song đa luồng).
4. Qua menu **Giỏ hàng** bên tay trái để xem danh sách món hàng đã nhận diện và giá tiền tổng.
5. Bấm **Xác nhận thanh toán** để kết thúc phiên và chuyển sang khách mới!

## 5. Cấu hình (Nếu cần)
Bạn có thể sửa các cấu hình trong file `ssc_service/config.py`:
- `SESSION_MAX_WORKERS` (Mặc định: 4): Số bức ảnh được quét AI cùng một lúc. Máy khỏe có thể nâng lên 8 hoặc 16 để tính tiền siêu tốc.
- `SESSION_MAX_IMAGES` (Mặc định: 50): Số lượng ảnh upload tối đa trong 1 lần mua hàng.
