# smart-checkout
VSSCS - Vietnam Supermarket Smart Checkout System

Hệ thống xử lý dữ liệu (Data Pipeline) cho dự án Smart Checkout được chia thành các phân hệ chính sau. Mời bạn tham khảo tài liệu chi tiết tại từng phân hệ:

- [**Preprocessing Pipeline**](./preprocessing/README.md): Quá trình tiền xử lý, làm sạch và biến đổi dữ liệu hình ảnh.
- [**Processing Pipeline**](./processing/README.md): Quá trình xử lý AI (phân vùng đối tượng, mã hóa vector) và đưa lên Vector Database.
- [**Smart Checkout Service**](./ssc_service/README.md): Backend APIs phục vụ tính toán giỏ hàng thời gian thực.
