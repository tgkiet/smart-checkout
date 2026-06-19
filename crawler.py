import os
import signal
import json
import time
import uuid
import hashlib
import shutil
import requests
import random
import logging
import argparse
import threading
import concurrent.futures
from datetime import datetime, timezone
from PIL import Image
from io import BytesIO
from abc import ABC, abstractmethod
from bs4 import BeautifulSoup

# ---------------------------------------------------------
# Cấu hình Logging
# ---------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logging.getLogger("urllib3").setLevel(logging.ERROR)

# =====================================================================
# CẤU HÌNH
# =====================================================================
OLD_DATA_DIR = "ssc_data_lake/bronze"
OLD_PRODUCT_DIR = os.path.join(OLD_DATA_DIR, "products")
OLD_IMG_DIR = os.path.join(OLD_DATA_DIR, "images")

NEW_DATA_DIR = "ssc_data_lake/bronze_v2"
NEW_PRODUCT_DIR = os.path.join(NEW_DATA_DIR, "products")
NEW_IMG_DIR = os.path.join(NEW_DATA_DIR, "images")

# Target mỗi LEAF: 9000-11000 SP
LEAF_MIN_PRODUCTS = 9000
LEAF_MAX_PRODUCTS = 11000

# =====================================================================
# KEYWORD MAP: Keyword dùng để crawl bổ sung khi Phase 1 chưa đủ
# Mỗi root category có list keyword, dùng rotate khi crawl cho các leaf
# =====================================================================
CATEGORY_KEYWORDS = {
    "4384": ["sữa", "bánh", "mì", "gạo", "nước uống", "cà phê", "trà",
             "gia vị", "dầu ăn", "đồ hộp", "snack", "kẹo", "rau củ", "thịt", "hải sản"],
    "1883": ["nội thất", "đèn", "rèm", "thảm", "gối", "chăn", "ga giường",
             "nhà bếp", "phòng tắm", "trang trí", "kệ", "tủ", "bàn", "ghế", "hộp đựng"],
    "1520": ["kem", "serum", "toner", "sữa rửa mặt", "son", "phấn",
             "mascara", "nước hoa", "dầu gội", "sữa tắm", "kem chống nắng",
             "vitamin", "thực phẩm chức năng", "mặt nạ", "tẩy trang"],
    "8322": ["sách", "truyện", "tiểu thuyết", "manga", "vở", "bút",
             "văn phòng phẩm", "quà tặng", "thiệp", "sổ tay", "sticker",
             "giáo trình", "từ điển", "tô màu", "lịch"],
    "1789": ["điện thoại", "iphone", "samsung", "xiaomi", "oppo", "máy tính bảng",
             "ipad", "ốp lưng", "cường lực", "sạc", "cáp", "tai nghe",
             "pin dự phòng", "sim", "thẻ nhớ"],
    "1815": ["camera", "loa", "smartwatch", "đồng hồ thông minh", "usb",
             "ổ cứng", "chuột", "bàn phím", "webcam", "micro", "tripod",
             "gimbal", "drone", "máy in", "máy chiếu"],
    "1882": ["máy giặt", "tủ lạnh", "điều hòa", "quạt", "máy lọc",
             "nồi cơm", "bếp điện", "lò vi sóng", "máy xay", "máy ép",
             "bàn ủi", "máy hút bụi", "máy sấy", "ấm đun", "nồi chiên"],
    "2549": ["đồ chơi", "lego", "búp bê", "xe đồ chơi", "bỉm", "tã",
             "sữa bột", "bình sữa", "xe đẩy", "ghế ăn", "quần áo trẻ em",
             "đồ sơ sinh", "phấn rôm", "núm vú", "khăn ướt"],
    "8594": ["ô tô", "xe máy", "xe đạp", "mũ bảo hiểm", "phụ tùng",
             "dầu nhớt", "camera hành trình", "nệm ô tô", "gương",
             "đèn xe", "lốp", "găng tay", "áo mưa", "GPS", "bọc ghế"],
    "931":  ["váy", "đầm", "áo kiểu", "quần jean nữ", "áo khoác nữ",
             "túi xách", "ví nữ", "khăn choàng", "mắt kính nữ", "đồ ngủ nữ",
             "áo dài", "set đồ nữ", "chân váy", "áo len nữ", "đồ bơi nữ"],
    "915":  ["áo polo", "áo sơ mi", "quần jean nam", "quần tây", "áo khoác nam",
             "balo", "ví nam", "thắt lưng", "đồng hồ nam", "kính mát nam",
             "áo thun nam", "quần short", "vest", "cà vạt", "đồ thể thao nam"],
    "1846": ["laptop", "macbook", "màn hình", "ram", "ssd", "cpu",
             "card đồ họa", "case máy tính", "nguồn máy tính", "tản nhiệt",
             "router", "switch mạng", "phần mềm", "máy tính để bàn", "mainboard"],
    "4221": ["tivi", "loa bluetooth", "amply", "dàn âm thanh", "đầu thu",
             "máy lạnh", "tủ đông", "máy rửa bát", "máy nước nóng",
             "quạt trần", "máy sưởi", "remote", "dây điện", "ổ cắm", "đèn led"],
    "1703": ["giày cao gót", "giày búp bê", "sandal nữ", "dép nữ",
             "giày thể thao nữ", "giày lười nữ", "giày boot nữ", "giày oxford nữ",
             "giày đế xuồng", "dép xỏ ngón nữ", "giày mọi nữ", "giày bệt",
             "giày sneaker nữ", "giày đi bộ nữ", "giày công sở nữ"],
    "1686": ["giày da nam", "giày thể thao nam", "giày lười nam", "dép nam",
             "sandal nam", "giày boot nam", "giày sneaker nam", "giày tây",
             "dép xỏ ngón nam", "giày công sở nam", "giày chạy bộ",
             "giày bóng đá", "giày leo núi", "giày vải nam", "giày đế cao nam"],
}

# 15 danh mục gốc
ROOT_CATEGORIES = [
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
    {"id": "1686", "name": "Giày - Dép nam"},
]


# =====================================================================
# 1. BASE CRAWLER
# =====================================================================
class BaseCrawler(ABC):
    def __init__(self, source_name, product_dir, img_dir, old_product_dir=None, old_img_dir=None):
        self.source_name = source_name
        self.product_dir = product_dir
        self.img_dir = img_dir
        self.old_product_dir = old_product_dir
        self.old_img_dir = old_img_dir
        self.session = requests.Session()

        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "vi-VN,vi;q=0.9,fr-FR;q=0.8,fr;q=0.7,en-US;q=0.6,en;q=0.5",
        }
        self.session.headers.update(self.headers)

        # Index data cũ theo leaf (lazy build)
        self._old_index_by_leaf = None
        self._old_index_lock = threading.Lock()

    # ---------------------------------------------------------
    # INDEX DATA CŨ: Nhóm SP cũ theo leaf category
    # ---------------------------------------------------------
    def _build_old_data_index(self):
        """Xây dựng index: leaf_id -> list of product_uuid.
        Dùng primary_category_path để xác định leaf."""
        if self._old_index_by_leaf is not None:
            return
        with self._old_index_lock:
            if self._old_index_by_leaf is not None:
                return

            if not self.old_product_dir or not os.path.exists(self.old_product_dir):
                self._old_index_by_leaf = {}
                logging.info(f"[{self.source_name}] No old data directory found.")
                return

            logging.info(f"[{self.source_name}] Building old data index from {self.old_product_dir}...")
            index = {}  # leaf_id (str) -> list of uuid
            count = 0
            for fname in os.listdir(self.old_product_dir):
                if not fname.endswith(".json"):
                    continue
                fpath = os.path.join(self.old_product_dir, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        d = json.load(f)
                    puuid = fname.replace(".json", "")
                    path = d.get("primary_category_path", "")
                    if path:
                        # Leaf = phần tử cuối cùng trong path
                        parts = path.split("/")
                        leaf_id = parts[-1]
                        if leaf_id not in index:
                            index[leaf_id] = []
                        index[leaf_id].append(puuid)
                    count += 1
                except Exception:
                    pass
                if count % 20000 == 0 and count > 0:
                    logging.info(f"[{self.source_name}] Indexed {count:,} old products...")

            self._old_index_by_leaf = index
            total_leaves = len(index)
            logging.info(f"[{self.source_name}] Old data index built: {count:,} products across {total_leaves:,} leaves")

    def _get_old_products_for_leaf(self, leaf_id):
        """Lấy danh sách UUID sản phẩm cũ thuộc 1 leaf."""
        self._build_old_data_index()
        return self._old_index_by_leaf.get(str(leaf_id), [])

    # ---------------------------------------------------------
    # API helpers
    # ---------------------------------------------------------
    def request_api_an_toan(self, url, retries=3):
        """Hàm gọi API chung"""
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

    def _fetch_product_detail_api(self, sp_id):
        """Lay full data tu Product Detail API."""
        url = f"https://tiki.vn/api/v2/products/{sp_id}"
        data = self.request_api_an_toan(url)
        if data and isinstance(data, dict) and data.get("id"):
            return data
        return None

    def _fetch_ld_json_requests(self, url_path, retries=2):
        """Dung requests + bs4 lay ld+json (thread-safe)."""
        if not url_path:
            return None
        full_url = f"https://tiki.vn/{url_path}"
        for attempt in range(retries):
            try:
                html_headers = {
                    "User-Agent": self.headers["User-Agent"],
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.6,en;q=0.5",
                }
                response = self.session.get(full_url, headers=html_headers, timeout=15)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                ld_json_scripts = soup.find_all("script", type="application/ld+json")
                if not ld_json_scripts:
                    return None
                ld_data_list = []
                for script_tag in ld_json_scripts:
                    try:
                        parsed = json.loads(script_tag.string)
                        ld_data_list.append(parsed)
                    except (json.JSONDecodeError, TypeError):
                        continue
                if not ld_data_list:
                    return None
                return ld_data_list[0] if len(ld_data_list) == 1 else ld_data_list
            except requests.exceptions.RequestException as e:
                logging.debug(f"[{self.source_name}] ld+json fetch error (attempt {attempt+1}/{retries}): {e}")
                time.sleep(random.uniform(1.0, 3.0))
            except Exception as e:
                logging.debug(f"[{self.source_name}] ld+json parse error: {e}")
                break
        return None

    def _fetch_full_product_data(self, sp_id, url_path):
        """Case 1 (API detail) -> Case 2 (requests+bs4 ld+json)."""
        detail = self._fetch_product_detail_api(sp_id)
        if detail:
            return detail, "api_detail"
        if url_path:
            time.sleep(random.uniform(0.3, 0.8))
            ld_json = self._fetch_ld_json_requests(url_path)
            if ld_json:
                return ld_json, "requests_ld_json"
        return None, None

    # ---------------------------------------------------------
    # Tải ảnh an toàn
    # ---------------------------------------------------------
    def _tai_anh_an_toan(self, url, filepath, pipeline_stats, stats_lock, retries=3):
        if not url: return False
        if os.path.exists(filepath):
            with stats_lock:
                pipeline_stats["skipped"] += 1
            return False

        for i in range(retries):
            try:
                res = self.session.get(url, timeout=15)
                res.raise_for_status()
                if 'image' not in res.headers.get('Content-Type', ''):
                    return False
                img = Image.open(BytesIO(res.content))
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
                img.save(filepath, "JPEG", quality=95)
                del img
                return True
            except requests.exceptions.RequestException:
                time.sleep(random.uniform(1.0, 3.0))
            except Exception:
                break
        return False

    # ---------------------------------------------------------
    # Resolve leaf categories (đệ quy)
    # ---------------------------------------------------------
    def _lay_leaf_categories(self, category_id):
        """Đệ quy lấy tất cả leaf nodes."""
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
                time.sleep(random.uniform(0.1, 0.3))
                leaves.extend(self._lay_leaf_categories(c_id))
        return leaves if leaves else [category_id]

    # ---------------------------------------------------------
    # Copy SP cũ sang folder mới
    # ---------------------------------------------------------
    def _copy_old_product(self, product_uuid):
        """Copy JSON + ảnh từ old -> new. Return True nếu OK."""
        old_json = os.path.join(self.old_product_dir, f"{product_uuid}.json")
        new_json = os.path.join(self.product_dir, f"{product_uuid}.json")

        if os.path.exists(new_json):
            return True
        if not os.path.exists(old_json):
            return False
        try:
            shutil.copy2(old_json, new_json)
            if self.old_img_dir:
                old_img = os.path.join(self.old_img_dir, f"{product_uuid}.jpg")
                new_img = os.path.join(self.img_dir, f"{product_uuid}.jpg")
                if os.path.exists(old_img) and not os.path.exists(new_img):
                    shutil.copy2(old_img, new_img)
            return True
        except Exception:
            return False

    # ---------------------------------------------------------
    # Xử lý 1 SP mới (crawl từ API)
    # ---------------------------------------------------------
    def _xu_ly_mot_san_pham_moi(self, sp_data, pipeline_stats, stats_lock):
        """Crawl 1 SP mới. Return True nếu lưu thành công (mới hoặc reuse)."""
        try:
            sp_id = sp_data.get("id")
            link_anh = sp_data.get("thumbnail_url", "")
            url_path = sp_data.get("url_path", "")
            if not sp_id:
                return False

            product_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"tiki_{sp_id}"))
            product_path = os.path.join(self.product_dir, f"{product_uuid}.json")

            # Đã có trong folder mới → skip
            if os.path.exists(product_path):
                with stats_lock:
                    pipeline_stats["skipped"] += 1
                return False

            # Thử copy từ data cũ
            if self.old_product_dir and self._copy_old_product(product_uuid):
                with stats_lock:
                    pipeline_stats["reused"] += 1
                return True

            # Crawl mới
            now_iso = datetime.now(timezone.utc).isoformat()
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            document = {
                "product_id": product_uuid,
                "source": "ecommerce_docker_run",
                "platform": "tiki",
            }
            for key, value in sp_data.items():
                if key not in document:
                    document[key] = value

            extra_data, data_source = self._fetch_full_product_data(sp_id, url_path)
            if data_source == "api_detail" and extra_data:
                for key, value in extra_data.items():
                    document[key] = value
                document["data_source"] = "api_detail"
            elif data_source == "requests_ld_json" and extra_data:
                document["ld_json"] = extra_data
                document["data_source"] = "requests_ld_json"
            else:
                document["data_source"] = "listing_only"

            doc_json = json.dumps(document, ensure_ascii=False, sort_keys=True)
            doc_hash = hashlib.md5(doc_json.encode("utf-8")).hexdigest()
            document["storage_refs"] = {
                "bucket": "bronze_v2",
                "object_name": f"ecommerce_docker_run/{today_str}/products/{product_uuid}.json",
                "size": len(doc_json.encode("utf-8")),
                "etag": doc_hash,
                "timestamp": now_iso,
            }
            document["stored_at"] = now_iso
            document["site"] = "unknown"
            document["pipeline_run_id"] = None
            document["record_id"] = None

            with open(product_path, 'w', encoding='utf-8') as f:
                json.dump(document, f, ensure_ascii=False, indent=2)

            if link_anh:
                link_anh_hd = link_anh.replace('/cache/280x280/', '/cache/w1200/')
                img_path = os.path.join(self.img_dir, f"{product_uuid}.jpg")
                self._tai_anh_an_toan(link_anh_hd, img_path, pipeline_stats, stats_lock)

            with stats_lock:
                pipeline_stats["crawled"] += 1
            return True

        except Exception as e:
            logging.error(f"[{self.source_name}] Product error ({sp_data.get('id', '?')}): {e}")
            with stats_lock:
                pipeline_stats["failed"] += 1
            return False

    # =========================================================
    # XỬ LÝ 1 LEAF: Phase 1 (reuse) -> Phase 2 (crawl keyword)
    # =========================================================
    def process_leaf(self, leaf_id, root_id, root_name, target,
                     pipeline_stats, stats_lock, pipeline_state,
                     save_state_callback, shutdown_event=None,
                     leaf_idx=0, total_leaves=0):
        """Xử lý 1 leaf:
        Phase 1: Copy data cũ thuộc leaf này
        Phase 2: Crawl thêm bằng keyword search nếu chưa đủ target
        """

        leaf_key = f"leaf_{leaf_id}"
        count_key = f"leaf_{leaf_id}_count"
        done_key = f"leaf_{leaf_id}_done"

        # Đã hoàn thành?
        if pipeline_state.get(done_key):
            return

        leaf_count = pipeline_state.get(count_key, 0)
        label = f"[{leaf_idx}/{total_leaves}]" if total_leaves else ""

        # ---------------------------------------------------
        # PHASE 1: Copy data cũ thuộc leaf này
        # ---------------------------------------------------
        phase1_key = f"leaf_{leaf_id}_phase1_done"
        if not pipeline_state.get(phase1_key):
            old_uuids = self._get_old_products_for_leaf(leaf_id)
            if old_uuids:
                copied = 0
                for puuid in old_uuids:
                    if shutdown_event and shutdown_event.is_set():
                        save_state_callback()
                        return
                    if leaf_count >= target:
                        break
                    if self._copy_old_product(puuid):
                        copied += 1
                        leaf_count += 1
                        with stats_lock:
                            pipeline_stats["reused"] += 1

                pipeline_state[count_key] = leaf_count
                logging.info(f"[{self.source_name}] {label} Leaf {leaf_id} | PHASE 1 done: copied {copied:,} from old data | count: {leaf_count:,}/{target:,}")
            else:
                logging.info(f"[{self.source_name}] {label} Leaf {leaf_id} | PHASE 1: no old data found")

            pipeline_state[phase1_key] = True
            save_state_callback()

        # Đã đủ target?
        if leaf_count >= target:
            pipeline_state[done_key] = True
            save_state_callback()
            logging.info(f"[{self.source_name}] {label} Leaf {leaf_id} | ✓ DONE after Phase 1 ({leaf_count:,}/{target:,})")
            return

        # ---------------------------------------------------
        # PHASE 2: Crawl bổ sung bằng keyword search
        # Rotate qua từng keyword của root category
        # ---------------------------------------------------
        keywords = CATEGORY_KEYWORDS.get(str(root_id), [])
        if not keywords:
            # Fallback: crawl bằng category browsing (không keyword)
            keywords = [""]  # empty = no keyword filter

        for kw_idx, keyword in enumerate(keywords):
            if shutdown_event and shutdown_event.is_set():
                save_state_callback()
                return
            if leaf_count >= target:
                break

            kw_state_key = f"leaf_{leaf_id}_kw_{kw_idx}_page"
            page = pipeline_state.get(kw_state_key, 1)

            if page == -1:
                continue  # Keyword này đã hết SP

            kw_label = f"keyword='{keyword}'" if keyword else "no-keyword"
            logging.info(f"[{self.source_name}] {label} Leaf {leaf_id} | PHASE 2: {kw_label} @ page {page} | count: {leaf_count:,}/{target:,}")

            consecutive_empty = 0
            while not (shutdown_event and shutdown_event.is_set()):
                if leaf_count >= target:
                    break

                # Build API URL
                if keyword:
                    api_url = f"https://tiki.vn/api/personalish/v1/blocks/listings?limit=40&q={keyword}&category={leaf_id}&page={page}"
                else:
                    api_url = f"https://tiki.vn/api/personalish/v1/blocks/listings?limit=40&category={leaf_id}&page={page}"

                data = self.request_api_an_toan(api_url)

                if not isinstance(data, dict):
                    consecutive_empty += 1
                    if consecutive_empty >= 3:
                        pipeline_state[kw_state_key] = -1
                        save_state_callback()
                        break
                    time.sleep(random.uniform(1.0, 2.0))
                    continue

                danh_sach_sp = data.get("data") or []
                if not isinstance(danh_sach_sp, list) or not danh_sach_sp:
                    pipeline_state[kw_state_key] = -1  # Hết SP cho keyword này
                    save_state_callback()
                    break

                consecutive_empty = 0

                # Multi-thread xử lý SP
                new_in_page = 0
                with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                    futures = [
                        executor.submit(self._xu_ly_mot_san_pham_moi, sp, pipeline_stats, stats_lock)
                        for sp in danh_sach_sp
                    ]
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            if future.result():
                                new_in_page += 1
                        except Exception:
                            pass

                leaf_count += new_in_page
                pipeline_state[count_key] = leaf_count

                page += 1
                pipeline_state[kw_state_key] = page

                if page % 5 == 0:
                    save_state_callback()

                if page % 20 == 0:
                    logging.info(f"[{self.source_name}] {label} Leaf {leaf_id} | {kw_label} page {page} | count: {leaf_count:,}/{target:,}")

                time.sleep(random.uniform(0.3, 0.8))

        # Kết thúc leaf
        pipeline_state[done_key] = True
        pipeline_state[count_key] = leaf_count
        save_state_callback()

        status = "✓" if leaf_count >= target else "⚠ PARTIAL"
        logging.info(f"[{self.source_name}] {label} Leaf {leaf_id} | {status} DONE ({leaf_count:,}/{target:,})")

    # =========================================================
    # XỬ LÝ 1 ROOT CATEGORY: Resolve leaves -> process each
    # =========================================================
    def process_root_category(self, category_info, pipeline_stats, stats_lock,
                              pipeline_state, save_state_callback,
                              shutdown_event=None, root_index=0, total_roots=0):
        root_id = category_info['id']
        root_name = category_info['name']
        root_label = f"[{root_index}/{total_roots}]" if total_roots else ""

        logging.info(f"[{self.source_name}] {root_label} Resolving leaves for '{root_name}' (ID: {root_id})...")

        # Cache leaf IDs trong state để không resolve lại
        leaf_cache_key = f"root_{root_id}_leaves"
        if leaf_cache_key in pipeline_state:
            leaf_ids = pipeline_state[leaf_cache_key]
            logging.info(f"[{self.source_name}] {root_label} Using cached leaves: {len(leaf_ids)} branches")
        else:
            leaf_ids = self._lay_leaf_categories(root_id)
            pipeline_state[leaf_cache_key] = leaf_ids
            save_state_callback()
            logging.info(f"[{self.source_name}] {root_label} Resolved {len(leaf_ids)} leaves for '{root_name}'")

        total_leaves = len(leaf_ids)
        completed = sum(1 for lid in leaf_ids if pipeline_state.get(f"leaf_{lid}_done"))
        remaining = total_leaves - completed

        logging.info(f"[{self.source_name}] {root_label} '{root_name}': {total_leaves} leaves | {completed} done | {remaining} remaining")

        if completed == total_leaves:
            logging.info(f"[{self.source_name}] {root_label} '{root_name}' fully completed - skipping.")
            return

        for idx, leaf_id in enumerate(leaf_ids, 1):
            if shutdown_event and shutdown_event.is_set():
                save_state_callback()
                return

            if pipeline_state.get(f"leaf_{leaf_id}_done"):
                continue

            # Random target cho mỗi leaf (lưu state để resume)
            target_key = f"leaf_{leaf_id}_target"
            if target_key in pipeline_state:
                target = pipeline_state[target_key]
            else:
                target = random.randint(LEAF_MIN_PRODUCTS, LEAF_MAX_PRODUCTS)
                pipeline_state[target_key] = target

            self.process_leaf(
                leaf_id=leaf_id,
                root_id=root_id,
                root_name=root_name,
                target=target,
                pipeline_stats=pipeline_stats,
                stats_lock=stats_lock,
                pipeline_state=pipeline_state,
                save_state_callback=save_state_callback,
                shutdown_event=shutdown_event,
                leaf_idx=idx,
                total_leaves=total_leaves,
            )

        done_now = sum(1 for lid in leaf_ids if pipeline_state.get(f"leaf_{lid}_done"))
        pct = (done_now / total_leaves * 100) if total_leaves else 100
        logging.info(f"[{self.source_name}] {root_label} DONE '{root_name}' ({pct:.0f}%) | reused: {pipeline_stats['reused']:,} | crawled: {pipeline_stats['crawled']:,}")

    @abstractmethod
    def lay_danh_sach_danh_muc(self): pass


# =====================================================================
# 2. MULTI SOURCE CRAWLER
# =====================================================================
class MultiSourceCrawler(BaseCrawler):
    def __init__(self, product_dir, img_dir, old_product_dir=None, old_img_dir=None):
        super().__init__("MULTI", product_dir, img_dir, old_product_dir, old_img_dir)

    def lay_danh_sach_danh_muc(self):
        return ROOT_CATEGORIES


# =====================================================================
# 3. PIPELINE CHÍNH
# =====================================================================
class SSCCollectionPipeline:
    def __init__(self):
        self.base_dir = NEW_DATA_DIR
        self.product_dir = NEW_PRODUCT_DIR
        self.img_dir = NEW_IMG_DIR
        self.state_file = os.path.join(self.base_dir, "crawler_state_v2.json")

        os.makedirs(self.product_dir, exist_ok=True)
        os.makedirs(self.img_dir, exist_ok=True)

        self.stats = {"reused": 0, "crawled": 0, "failed": 0, "skipped": 0}
        self.stats_lock = threading.Lock()
        self.state = self._load_state()

        self.crawler = MultiSourceCrawler(
            self.product_dir, self.img_dir,
            old_product_dir=OLD_PRODUCT_DIR,
            old_img_dir=OLD_IMG_DIR,
        )

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"Error reading state file: {e}")
        return {}

    def _save_state(self):
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self.state, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logging.error(f"Error writing state file: {e}")

    def _setup_shutdown(self):
        self.shutdown_event = threading.Event()
        def _signal_handler(sig, frame):
            if self.shutdown_event.is_set():
                logging.warning("FORCE EXIT - Second Ctrl+C received.")
                raise SystemExit(1)
            logging.info("")
            logging.info("SHUTDOWN - Graceful stop. Finishing current batch...")
            logging.info("           Press Ctrl+C again to force exit.")
            self.shutdown_event.set()
        signal.signal(signal.SIGINT, _signal_handler)

    def chay_he_thong(self):
        self._setup_shutdown()

        logging.info("=" * 70)
        logging.info("SSC CRAWLER v2 - LEAF-BASED + KEYWORD CRAWL")
        logging.info(f"15 categories → resolve leaves → sort by old data → each leaf {LEAF_MIN_PRODUCTS:,}-{LEAF_MAX_PRODUCTS:,} SP")
        logging.info("Strategy: Leaf nhiều data cũ nhất → xử lý trước (copy nhanh)")
        logging.info(f"Old data   : {OLD_DATA_DIR}")
        logging.info(f"New data   : {self.base_dir}")
        logging.info(f"State file : {self.state_file}")
        logging.info("Graceful shutdown: Ctrl+C")
        logging.info("=" * 70)

        # ===========================================================
        # BƯỚC 1: Resolve tất cả leaves cho 15 categories
        # ===========================================================
        danh_muc_list = self.crawler.lay_danh_sach_danh_muc()
        all_leaves = []  # list of (leaf_id, root_id, root_name)

        logging.info("STEP 1: Resolving all leaf categories...")
        for dm in danh_muc_list:
            if self.shutdown_event.is_set():
                break
            root_id = dm['id']
            root_name = dm['name']

            # Cache leaf IDs trong state
            leaf_cache_key = f"root_{root_id}_leaves"
            if leaf_cache_key in self.state:
                leaf_ids = self.state[leaf_cache_key]
                logging.info(f"  [{root_id}] {root_name}: {len(leaf_ids)} leaves (cached)")
            else:
                leaf_ids = self.crawler._lay_leaf_categories(root_id)
                self.state[leaf_cache_key] = leaf_ids
                self._save_state()
                logging.info(f"  [{root_id}] {root_name}: {len(leaf_ids)} leaves (resolved)")

            for lid in leaf_ids:
                all_leaves.append((lid, root_id, root_name))

        logging.info(f"Total leaves across all categories: {len(all_leaves):,}")

        # ===========================================================
        # BƯỚC 2: Build index data cũ → đếm SP mỗi leaf
        # ===========================================================
        logging.info("STEP 2: Building old data index & counting per leaf...")
        self.crawler._build_old_data_index()

        # Đếm SP cũ cho mỗi leaf, loại bỏ leaf đã done
        leaf_with_counts = []
        for leaf_id, root_id, root_name in all_leaves:
            if self.state.get(f"leaf_{leaf_id}_done"):
                continue  # Đã hoàn thành, bỏ qua
            old_count = len(self.crawler._get_old_products_for_leaf(leaf_id))
            leaf_with_counts.append((leaf_id, root_id, root_name, old_count))

        # ===========================================================
        # BƯỚC 3: Sort giảm dần theo old_count
        # Leaf nhiều data cũ nhất → xử lý trước (copy nhanh, tiết kiệm)
        # ===========================================================
        leaf_with_counts.sort(key=lambda x: x[3], reverse=True)

        already_done = len(all_leaves) - len(leaf_with_counts)
        total_remaining = len(leaf_with_counts)
        total_old_reusable = sum(c[3] for c in leaf_with_counts)

        logging.info(f"STEP 3: Sorted {total_remaining:,} leaves by old data count (descending)")
        logging.info(f"  Already completed : {already_done:,} leaves")
        logging.info(f"  Remaining         : {total_remaining:,} leaves")
        logging.info(f"  Old data reusable : {total_old_reusable:,} products")

        # Log top 10 leaves
        if leaf_with_counts:
            logging.info("  Top 10 leaves with most old data:")
            for i, (lid, rid, rname, cnt) in enumerate(leaf_with_counts[:10], 1):
                logging.info(f"    {i:2d}. Leaf {lid} ({rname}): {cnt:,} old SP")

        logging.info("=" * 70)

        # ===========================================================
        # BƯỚC 4: Xử lý từng leaf theo thứ tự đã sort
        # ===========================================================
        for idx, (leaf_id, root_id, root_name, old_count) in enumerate(leaf_with_counts, 1):
            if self.shutdown_event.is_set():
                break

            # Random target (lưu state để resume)
            target_key = f"leaf_{leaf_id}_target"
            if target_key in self.state:
                target = self.state[target_key]
            else:
                target = random.randint(LEAF_MIN_PRODUCTS, LEAF_MAX_PRODUCTS)
                self.state[target_key] = target

            logging.info(f"[{idx}/{total_remaining}] Leaf {leaf_id} @ {root_name} | old: {old_count:,} | target: {target:,}")

            self.crawler.process_leaf(
                leaf_id=leaf_id,
                root_id=root_id,
                root_name=root_name,
                target=target,
                pipeline_stats=self.stats,
                stats_lock=self.stats_lock,
                pipeline_state=self.state,
                save_state_callback=self._save_state,
                shutdown_event=self.shutdown_event,
                leaf_idx=idx,
                total_leaves=total_remaining,
            )

        self._save_state()

        logging.info("")
        logging.info("=" * 70)
        logging.info("FINAL REPORT:")
        logging.info(f"  Reused from old data    : {self.stats['reused']:,}")
        logging.info(f"  Crawled new             : {self.stats['crawled']:,}")
        logging.info(f"  Skipped (duplicates)    : {self.stats['skipped']:,}")
        logging.info(f"  Failed (errors)         : {self.stats['failed']:,}")
        logging.info(f"  Total collected         : {self.stats['reused'] + self.stats['crawled']:,}")
        logging.info("=" * 70)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    parser = argparse.ArgumentParser(
        description="SSC Crawler v2 - Leaf-based collection with keyword crawling",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    args = parser.parse_args()

    pipeline = SSCCollectionPipeline()
    pipeline.chay_he_thong()
