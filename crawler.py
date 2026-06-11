import os
import json
import time
import requests
import random
import logging
import threading
import concurrent.futures
from PIL import Image
from io import BytesIO
from abc import ABC, abstractmethod

# ---------------------------------------------------------
# Cấu hình Logging chuyên nghiệp giúp dễ dàng theo dõi lỗi
# ---------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logging.getLogger("urllib3").setLevel(logging.ERROR)

# =====================================================================
# 1. CLASS CƠ SỞ (BASE CRAWLER) - GOM LOGIC TÁI SỬ DỤNG
# =====================================================================
class BaseCrawler(ABC):
    def __init__(self, source_name, img_dir, meta_dir):
        self.source_name = source_name
        self.img_dir = img_dir
        self.meta_dir = meta_dir
        # Sử dụng Session để tối ưu kết nối
        self.session = requests.Session()

        # Header ngẫu nhiên giả lập trình duyệt thật
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "vi-VN,vi;q=0.9,fr-FR;q=0.8,fr;q=0.7,en-US;q=0.6,en;q=0.5",
        }
        self.session.headers.update(self.headers)

    def _tai_anh_an_toan(self, url, filepath, pipeline_stats, stats_lock, retries=3):
        """Hàm tải ảnh an toàn, bắt mọi Exception mạng và xử lý định dạng ảnh"""
        if not url: return False
        
        # Kiểm tra nếu ảnh đã tồn tại thì bỏ qua ngay để tăng tốc độ
        if os.path.exists(filepath):
            with stats_lock:
                pipeline_stats["skipped"] += 1
            return False # Trả về False để KHÔNG cộng vào success

        for i in range(retries):
            try:
                res = self.session.get(url, timeout=15)
                res.raise_for_status()
                
                if 'image' not in res.headers.get('Content-Type', ''):
                    logging.warning(f"[{self.source_name}] URL không trả về định dạng ảnh: {url}")
                    return False
                
                img = Image.open(BytesIO(res.content))
                
                # SỬA LỖI NỀN ĐEN: Tạo nền trắng cho các ảnh có kênh trong suốt (RGBA/PNG)
                if img.mode in ("RGBA", "P"):
                    if img.mode == "P":
                        img = img.convert("RGBA")
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    if len(img.split()) == 4:
                        bg.paste(img, mask=img.split()[3])
                    else:
                        bg.paste(img)
                    img = bg
                elif img.mode != "RGB":
                    img = img.convert("RGB")
                    
                # LƯU CHẤT LƯỢNG CAO
                img.save(filepath, "JPEG", quality=95)
                
                # Giải phóng RAM hình ảnh
                del img
                return True
            
            except requests.exceptions.RequestException as e:
                logging.debug(f"[{self.source_name}] Lỗi mạng khi tải ảnh (thử lại {i+1}/{retries}): {e}")
                time.sleep(random.uniform(1.0, 3.0)) 
            except Exception as e:
                logging.error(f"[{self.source_name}] Ảnh bị hỏng hoặc lỗi định dạng: {e}")
                break 
        return False

    def request_api_an_toan(self, url, retries=3):
        """Hàm gọi API chung bắt đủ các trường hợp ngoại lệ HTTP/JSON"""
        for i in range(retries):
            try:
                response = self.session.get(url, timeout=15)
                
                if response.status_code in [401, 403]:
                    logging.warning(f"[{self.source_name}] Bị từ chối HTTP {response.status_code}")
                    return None
                    
                response.raise_for_status()
                return response.json()
                
            except requests.exceptions.JSONDecodeError:
                logging.error(f"[{self.source_name}] Server không trả về JSON. API URL: {url}")
                return None
            except requests.exceptions.HTTPError as e:
                if response.status_code in [404, 429]: 
                    return None
            except requests.exceptions.RequestException:
                pass
            except ValueError:
                return None
            
            time.sleep(random.uniform(2.0, 5.0))
        return None

    def _lay_leaf_categories(self, category_id):
        """Đệ quy lấy tất cả các danh mục con sâu nhất (leaf nodes) để vượt giới hạn 50 trang của API."""
        url = f"https://tiki.vn/api/v2/categories?parent_id={category_id}"
        data = self.request_api_an_toan(url)
        
        if not data or not isinstance(data, dict):
            return [category_id]
            
        sub_categories = data.get("data", [])
        if not sub_categories:
            return [category_id]
            
        leaves = []
        for c in sub_categories:
            c_id = c.get("id")
            if not c_id: continue
            
            if c.get("is_leaf"):
                leaves.append(c_id)
            else:
                # Tránh gọi quá dồn dập
                time.sleep(random.uniform(0.1, 0.3))
                leaves.extend(self._lay_leaf_categories(c_id))
                
        return leaves if leaves else [category_id]

    def cao_san_pham_theo_danh_muc(self, category_info, pipeline_stats, stats_lock, pipeline_state, save_state_callback):
        root_id = category_info['id']
        root_name = category_info['name']
        
        logging.info(f"[{self.source_name}] Đang bóc tách danh mục gốc '{root_name}' ({root_id}) để lấy các sub-categories...")
        leaf_ids = self._lay_leaf_categories(root_id)
        logging.info(f"[{self.source_name}] Đã tìm thấy {len(leaf_ids)} danh mục con sâu nhất cho '{root_name}'. Bắt đầu cào data...")
        
        for leaf_id in leaf_ids:
            state_key = f"{self.source_name}_{leaf_id}_page"
            page = pipeline_state.get(state_key, 1)
            
            # Bỏ qua nếu danh mục con này đã cào xong (được đánh dấu bằng page = -1)
            if page == -1:
                continue
                
            logging.info(f"[{self.source_name}] Đang quét Category nhánh ID {leaf_id} thuộc '{root_name}' (Từ trang {page})")
            
            while True:
                api_url = f"https://tiki.vn/api/personalish/v1/blocks/listings?limit=40&category={leaf_id}&page={page}"
                data = self.request_api_an_toan(api_url)
                
                if not isinstance(data, dict): 
                    break
                
                danh_sach_sp = data.get("data") or []
                if not isinstance(danh_sach_sp, list) or not danh_sach_sp:
                    logging.info(f"[{self.source_name}] Hoàn thành quét nhánh {leaf_id}.")
                    pipeline_state[state_key] = -1 # Đánh dấu đã hoàn thành nhánh này
                    save_state_callback()
                    break

                # Tích hợp Multi-threading để cào đồng loạt 40 sản phẩm trong trang
                with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                    futures = [executor.submit(self._xu_ly_mot_san_pham, sp, root_name, pipeline_stats, stats_lock) for sp in danh_sach_sp]
                    concurrent.futures.wait(futures)

                page += 1
                pipeline_state[state_key] = page
                
                # Lưu state mỗi 5 trang để tránh ghi đĩa quá nhiều
                if page % 5 == 0:
                    save_state_callback()

                # Tối ưu: giảm thời gian chờ nhờ đa luồng xử lý cực nhanh
                time.sleep(random.uniform(0.1, 0.5))

    def _xu_ly_mot_san_pham(self, sp_data, cat_name, pipeline_stats, stats_lock):
        try:
            sp_id = str(sp_data.get("id", ""))
            ten_sp = sp_data.get("name", "Unknown")
            link_anh = sp_data.get("thumbnail_url", "")
            
            if not sp_id or not link_anh: return

            # TĂNG CHẤT LƯỢNG ẢNH TỪ 280x280 LÊN KÍCH THƯỚC CHUẨN HD
            link_anh = link_anh.replace('/cache/280x280/', '/cache/w1200/')
            
            prefix = self.source_name.upper()
            img_path = os.path.join(self.img_dir, f"{prefix}_{sp_id}.jpg")
            meta_path = os.path.join(self.meta_dir, f"{prefix}_{sp_id}.json")

            if self._tai_anh_an_toan(link_anh, img_path, pipeline_stats, stats_lock):
                gia_sp = sp_data.get("price", 0)

                metadata = {
                    "product_id": f"{prefix}_{sp_id}",
                    "product_name": ten_sp,
                    "source": self.source_name,
                    "category_group": cat_name,
                    "attributes": {"price": int(gia_sp) if gia_sp else 0}
                }
                with open(meta_path, 'w', encoding='utf-8') as f:
                    json.dump(metadata, f, ensure_ascii=False, indent=4)
                
                with stats_lock:
                    pipeline_stats["success"] += 1
            else:
                # Chỉ cộng failed nếu file thật sự không tồn tại, tránh cộng sai khi file đã bị skip
                if not os.path.exists(img_path):
                    with stats_lock:
                        pipeline_stats["failed"] += 1
                    
        except Exception as e:
            logging.error(f"[{self.source_name}] Bỏ qua sản phẩm lỗi: {e}")
            with stats_lock:
                pipeline_stats["failed"] += 1

    @abstractmethod
    def lay_danh_sach_danh_muc(self): pass

# =====================================================================
# 2. CLASS CRAWLER ĐA NĂNG (GOM NHIỀU NGUỒN VÀO 1)
# =====================================================================
class MultiSourceCrawler(BaseCrawler):
    def __init__(self, img_dir, meta_dir):
        super().__init__("MULTI", img_dir, meta_dir)

    def lay_danh_sach_danh_muc(self):
        # Mở rộng số lượng danh mục để đa dạng hóa tập dữ liệu
        return [
            {"id": "4384", "name": "Bách Hóa Online - Thực Phẩm"},
            {"id": "1883", "name": "Nhà Cửa - Đời Sống"},
            {"id": "1520", "name": "Làm Đẹp - Sức Khỏe"},
            {"id": "8322", "name": "Sách, VPP & Quà Tặng"},
            {"id": "1789", "name": "Điện thoại - Máy tính bảng"},
            {"id": "1815", "name": "Thiết bị số - Phụ kiện số"},
            {"id": "1882", "name": "Điện Gia Dụng"},
            {"id": "2549", "name": "Đồ Chơi - Mẹ & Bé"},
            {"id": "8594", "name": "Ô Tô - Xe Máy - Xe Đạp"},
            {"id": "931",  "name": "Thời trang nữ"},
            {"id": "915",  "name": "Thời trang nam"},
            {"id": "1846", "name": "Laptop - Máy Vi Tính - Linh kiện"},
            {"id": "4221", "name": "Điện Tử - Điện Lạnh"},
            {"id": "1703", "name": "Giày - Dép nữ"},
            {"id": "1686", "name": "Giày - Dép nam"}
        ]


# =====================================================================
# 3. HỆ THỐNG ĐIỀU PHỐI (PIPELINE CHÍNH)
# =====================================================================
class SSCCollectionPipeline:
    def __init__(self):
        self.base_dir = "ssc_data_lake/bronze"
        self.img_dir = os.path.join(self.base_dir, "images")
        self.meta_dir = os.path.join(self.base_dir, "metadata")
        self.state_file = os.path.join(self.base_dir, "crawler_state.json")
        
        os.makedirs(self.img_dir, exist_ok=True)
        os.makedirs(self.meta_dir, exist_ok=True)

        self.stats = {"success": 0, "failed": 0, "skipped": 0}
        self.stats_lock = threading.Lock()
        self.state = self._load_state()
        
        self.crawler = MultiSourceCrawler(self.img_dir, self.meta_dir)

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"Lỗi đọc file state: {e}")
        return {}

    def _save_state(self):
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self.state, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logging.error(f"Lỗi ghi file state: {e}")

    def chay_he_thong(self):
        logging.info("="*60)
        logging.info("🚀 KHỞI ĐỘNG HỆ THỐNG FULL CRAWL - SIÊU TỐC KHÔNG GIỚI HẠN (VƯỢT GIỚI HẠN)")
        logging.info(f"📍 Đang đọc State (Điểm lưu): {self.state_file}")
        logging.info("="*60)

        danh_muc_list = self.crawler.lay_danh_sach_danh_muc()
        for dm in danh_muc_list:
            self.crawler.cao_san_pham_theo_danh_muc(dm, self.stats, self.stats_lock, self.state, self._save_state)

        self._save_state()

        logging.info("="*60)
        logging.info("🎯 BÁO CÁO HOÀN THÀNH TỔNG THỂ:")
        logging.info(f" - Tổng tải mới thành công: {self.stats['success']} mẫu")
        logging.info(f" - Tổng bỏ qua (đã có sẵn): {self.stats['skipped']} mẫu")
        logging.info(f" - Tổng thất bại (lỗi/ảnh hỏng): {self.stats['failed']} mẫu")
        logging.info("="*60)


if __name__ == "__main__":
    pipeline = SSCCollectionPipeline()
    pipeline.chay_he_thong()
