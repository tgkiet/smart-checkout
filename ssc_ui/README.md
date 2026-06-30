# Smart Checkout UI (Frontend)

`ssc_ui` là giao diện web dành cho người dùng cuối (khách hàng hoặc thu ngân) để thực hiện thao tác checkout tự động dựa trên hình ảnh. Giao diện được thiết kế theo xu hướng hiện đại (Glassmorphism, Dark mode, Animations) đảm bảo mang lại trải nghiệm WOW.

## Luồng Logic (Architecture & Logic)

Giao diện được xây dựng bằng **Vanilla HTML/CSS/JS (ES6 Modules)**, không cần framework nặng nề (như React/Vue) nhưng vẫn tối ưu hóa được việc quản lý file và khả năng mở rộng.

1. **Quản lý sự kiện (Event Handling)**:
   - Module `app.js` lắng nghe sự kiện kéo thả ảnh (Drag & Drop) hoặc nút chọn tệp.
   - Khi có file ảnh, hiển thị màn hình Preview để người dùng xác nhận.
2. **Giao tiếp API (API Integration)**:
   - Khi ấn *Process Checkout*, module `api.js` tạo `FormData` và gửi một request HTTP POST tới Backend FastAPI (`http://localhost:8000/checkout`).
   - Xử lý Loading Spinner trong lúc chờ đợi backend gọi các mô hình AI mất vài giây.
3. **Cập nhật DOM (Dynamic Rendering)**:
   - Sau khi nhận được mảng `items` và `total_price` từ server.
   - Module `ui.js` sẽ tạo các khối HTML thẻ item động, đính kèm animation trượt `slideInRight` và cập nhật trực tiếp vào danh sách.
4. **CSS Modular**:
   - `variables.css`: Khai báo bộ mã màu và biến toàn cục.
   - `layout.css`: Định nghĩa cấu trúc lưới (grid) và bố cục các mảng chính.
   - `components.css`: Tùy biến các phần tử độc lập như nút bấm, thẻ (card).
   - `animations.css`: Quản lý các keyframes animation dùng chung.

## Hướng dẫn cài đặt và chạy (Run Guide)

Dự án dùng JS ES6 Modules (`<script type="module">`). Do cơ chế bảo mật của trình duyệt, bạn **không thể** click đúp mở file `index.html` trực tiếp bằng giao thức `file://` (sẽ bị lỗi CORS hoặc CORS Module). Bạn bắt buộc phải chạy thông qua một Local Web Server.

### Cách 1: Sử dụng VSCode Live Server (Khuyên dùng)
- Cài đặt extension **Live Server** trong VSCode.
- Chuột phải vào file `ssc_ui/index.html` và chọn **"Open with Live Server"**.
- Trình duyệt sẽ tự động bật ở địa chỉ `http://localhost:5500`.

### Cách 2: Sử dụng Python HTTP Server
Vì môi trường của bạn đã có sẵn Python, bạn có thể khởi tạo một server tĩnh vô cùng nhanh chóng:
```bash
cd ssc_ui
python3 -m http.server 3000
```
- Mở trình duyệt và truy cập: `http://localhost:3000`

### ⚠️ Lưu ý quan trọng
- Khi test UI, Backend `ssc_service` bắt buộc phải đang chạy ở `http://localhost:8000`.
- UI sẽ gửi request trực tiếp sang `localhost:8000`. Nếu port có thay đổi, vui lòng cập nhật lại URL trong `ssc_ui/js/api.js`.
