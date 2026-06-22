import logging
import io
try:
    from PIL import Image
except ImportError:
    logging.warning("Thư viện PIL (Pillow) chưa được cài đặt. Nên cài đặt bằng `pip install Pillow` để xử lý ảnh.")

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s')

class DataCleaner:
    def __init__(self):
        # Có thể dùng set/dict hoặc kết nối Redis để check trùng lặp nếu scale lớn
        self.processed_identifiers = set()
        
    def clean_metadata(self, data: dict) -> dict:
        """
        Làm sạch dữ liệu metadata.
        - Xử lý duplicate
        - Chuẩn hóa chuỗi và kiểm tra tính hợp lệ
        """
        if not isinstance(data, dict):
            logging.warning("Dữ liệu không phải định dạng JSON (dict).")
            return None

        # 1. Xử lý duplicate
        identifier = data.get('id') or data.get('product_id') or data.get('barcode')
        
        if identifier:
            if identifier in self.processed_identifiers:
                logging.info(f"Phát hiện dữ liệu trùng lặp (ID: {identifier}). Bỏ qua.")
                return None
            self.processed_identifiers.add(identifier)
            
        # 2. Xử lý các quy tắc làm sạch khác
        data = self._normalize_strings(data)
        data = self._validate_fields(data)
        
        return data

    def _normalize_strings(self, data: dict) -> dict:
        """Chuẩn hoá chuỗi, xoá khoảng trắng thừa..."""
        for key, value in data.items():
            if isinstance(value, str):
                data[key] = value.strip()
        return data

    def _validate_fields(self, data: dict) -> dict:
        """Kiểm tra hoặc format các trường dữ liệu bắt buộc..."""
        # Ví dụ: đảm bảo price luôn là kiểu số
        if 'price' in data:
            try:
                data['price'] = float(data['price'])
            except ValueError:
                data['price'] = 0.0
        return data

    def clean_image(self, image_bytes: bytes, max_size=(800, 800), format='JPEG', quality=85) -> bytes:
        """
        Làm sạch và tối ưu hình ảnh.
        - Kiểm tra tính hợp lệ của ảnh
        - Chuyển đổi định dạng sang chuẩn (vd: JPEG/RGB) để đồng nhất
        - Thay đổi kích thước (resize) nếu ảnh quá lớn để tránh tốn dung lượng
        - Nén chất lượng ảnh (quality)
        Trả về bytes của ảnh sau khi xử lý, hoặc None nếu có lỗi.
        """
        try:
            # Nếu chưa cài Pillow, trả về nguyên bản
            if 'Image' not in globals():
                logging.warning("Chưa có Pillow, bỏ qua xử lý ảnh.")
                return image_bytes

            image_stream = io.BytesIO(image_bytes)
            img = Image.open(image_stream)
            
            # Xử lý chuẩn hoá ảnh (chuyển RGBA hoặc P sang RGB để lưu JPEG)
            if img.mode in ("RGBA", "P") and format.upper() == 'JPEG':
                img = img.convert("RGB")
                
            # Resize ảnh nhưng vẫn giữ đúng tỷ lệ (thumbnail)
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            
            # Ghi ảnh ra bytes stream mới
            out_stream = io.BytesIO()
            img.save(out_stream, format=format, quality=quality)
            
            return out_stream.getvalue()
        except Exception as e:
            logging.error(f"Lỗi khi làm sạch ảnh: {e}")
            return None
