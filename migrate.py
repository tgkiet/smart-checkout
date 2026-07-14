"""
migrate.py — Script migrate dữ liệu sang kiến trúc Medallion (Bronze / Silver / Gold)
======================================================================================

CHIẾN LƯỢC:
  - KHÔNG xóa dữ liệu cũ, chỉ sao chép sang bucket/collection mới.
  - Cập nhật path trong MongoDB để trỏ đúng vị trí dữ liệu mới.

MAPPING MINIO:
  products-images/<object>                         → bronze/<object>
  smart-checkout/preprocessing/cleaning/<id>.jpg  → silver/cleaning/<id>.jpg
  smart-checkout/preprocessing/integrate/<id>.jpg → silver/integrate/<id>.jpg
  smart-checkout/preprocessing/transform/<id>.jpg → silver/transform/<id>.jpg
  smart-checkout/processing/objects/<sku>/<id>.jpg → gold/processing/objects/<sku>/<id>.jpg

MAPPING MONGODB (cập nhật các trường path):
  smart_checkout.products          : minio_image_path   → trỏ tới bronze/...
  preprocessing.cleaning           : minio_clean_path   → trỏ tới silver/...
  preprocessing.integrated         : minio_integrate_path → trỏ tới silver/...
  preprocessing.transformed        : minio_transform_path → trỏ tới silver/...
  processing.objects               : minio_object_path  → trỏ tới gold/...

CÁCH CHẠY:
  python migrate.py
  python migrate.py --dry-run          # Xem preview, không thực sự thay đổi
  python migrate.py --step minio       # Chỉ migrate MinIO
  python migrate.py --step mongodb     # Chỉ cập nhật MongoDB paths
  python migrate.py --batch-size 500   # Số objects/docs mỗi lần xử lý (mặc định 200)
"""

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Dependencies: pip install minio pymongo python-dotenv
# ─────────────────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # .env không bắt buộc, có thể truyền qua biến môi trường

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler("logs/migrate.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("migrate")


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Config:
    # MongoDB
    mongo_uri: str = os.getenv("MONGO_URI", "mongodb://root:rootpass@localhost:27917/")
    # MinIO
    minio_endpoint: str = os.getenv("MINIO_ENDPOINT", "localhost:9200")
    minio_access: str = os.getenv("MINIO_ACCESS", "admin")
    minio_secret: str = os.getenv("MINIO_SECRET", "adminpass")
    minio_secure: bool = False
    # Batch
    batch_size: int = 200
    # Max objects
    max_objects: Optional[int] = None
    # Dry run
    dry_run: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Medallion bucket / path mapping
# ─────────────────────────────────────────────────────────────────────────────

# Tên các bucket mới (Medallion Layer)
BRONZE_BUCKET = "bronze"
SILVER_BUCKET = "silver"
GOLD_BUCKET   = "gold"

# Nguồn → đích cho MinIO objects
# Mỗi entry: (src_bucket, src_prefix, dst_bucket, dst_prefix)
MINIO_MIGRATION_RULES = [
    # products-images/* → bronze/*
    ("products-images", "",                             BRONZE_BUCKET, ""),
    # smart-checkout/preprocessing/* → silver/<step>/  (bỏ tiền tố preprocessing/)
    ("smart-checkout", "preprocessing/cleaning/",       SILVER_BUCKET, "cleaning/"),
    ("smart-checkout", "preprocessing/integrate/",      SILVER_BUCKET, "integrate/"),
    ("smart-checkout", "preprocessing/transform/",      SILVER_BUCKET, "transform/"),
    # smart-checkout/processing/* → gold/processing/*
    ("smart-checkout", "processing/objects/",           GOLD_BUCKET,   "processing/objects/"),
]

# Mô tả các trường path trong MongoDB cần cập nhật
# Mỗi entry: (db, collection, field_name, src_bucket_pattern, dst_bucket, path_prefix_mapping)
# path_prefix_mapping: (old_prefix_in_path, new_prefix_in_path)
MONGO_PATH_RULES = [
    # smart_checkout.products → minio_image_path
    # Cũ: s3://product-image/<path>   Mới: s3://bronze/<path>
    {
        "db":         "smart_checkout",
        "collection": "products",
        "fields":     ["minio_image_path", "minio_path"],
        "old_bucket": "products-images",
        "new_bucket": BRONZE_BUCKET,
        "old_prefix": "",   # giữ nguyên phần path sau bucket
        "new_prefix": "",
    },
    # preprocessing.cleaning → minio_clean_path / minio_cleaning_path
    # Cũ: s3://smart-checkout/preprocessing/cleaning/<id>.jpg
    # Mới: s3://silver/preprocessing/cleaning/<id>.jpg
    {
        "db":         "preprocessing",
        "collection": "cleaning",
        "fields":     ["minio_clean_path", "minio_cleaning_path", "minio_image_path"],
        "old_bucket": "smart-checkout",
        "new_bucket": SILVER_BUCKET,
        "old_prefix": "preprocessing/cleaning/",
        "new_prefix": "cleaning/",
    },
    # preprocessing.integrated → minio_integrate_path
    {
        "db":         "preprocessing",
        "collection": "integrated",
        "fields":     ["minio_integrate_path", "minio_image_path"],
        "old_bucket": "smart-checkout",
        "new_bucket": SILVER_BUCKET,
        "old_prefix": "preprocessing/integrate/",
        "new_prefix": "integrate/",
    },
    # preprocessing.transformed → minio_transform_path
    {
        "db":         "preprocessing",
        "collection": "transformed",
        "fields":     ["minio_transform_path", "minio_image_path"],
        "old_bucket": "smart-checkout",
        "new_bucket": SILVER_BUCKET,
        "old_prefix": "preprocessing/transform/",
        "new_prefix": "transform/",
    },
    # processing.objects → minio_object_path, minio_image_path
    # minio_object_path: s3://smart-checkout/processing/objects/<sku>/<sub_id>.jpg → s3://gold/...
    # minio_image_path : s3://smart-checkout/preprocessing/transform/<id>.jpg     → s3://silver/...
    {
        "db":         "processing",
        "collection": "objects",
        "fields":     ["minio_object_path"],
        "old_bucket": "smart-checkout",
        "new_bucket": GOLD_BUCKET,
        "old_prefix": "processing/objects/",
        "new_prefix": "processing/objects/",
    },
    {
        "db":         "processing",
        "collection": "objects",
        "fields":     ["minio_image_path"],
        "old_bucket": "smart-checkout",
        "new_bucket": SILVER_BUCKET,
        "old_prefix": "preprocessing/transform/",
        "new_prefix": "transform/",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _new_path_from_old(old_path: str, old_bucket: str, new_bucket: str,
                        old_prefix: str, new_prefix: str) -> Optional[str]:
    """
    Chuyển đổi path MinIO cũ sang path mới theo Medallion Layer.

    Ví dụ:
        s3://product-image/abc/123.jpg → s3://bronze/abc/123.jpg
        s3://smart-checkout/preprocessing/cleaning/x.jpg → s3://silver/preprocessing/cleaning/x.jpg

    Trả về None nếu path không khớp với rule.
    """
    if not old_path:
        return None

    # Chuẩn hóa: bỏ tiền tố s3:// hoặc s3a://
    clean = old_path.replace("s3a://", "").replace("s3://", "").strip("/")

    # Tách bucket và object path
    parts = clean.split("/", 1)
    if len(parts) < 2:
        bucket = parts[0]
        obj_path = ""
    else:
        bucket, obj_path = parts[0], parts[1]

    if bucket != old_bucket:
        return None  # không khớp rule này

    # Nếu có prefix bắt buộc, kiểm tra obj_path bắt đầu bằng prefix đó
    if old_prefix and not obj_path.startswith(old_prefix):
        return None  # không khớp

    # Xây dựng path mới
    # Thay phần prefix cũ bằng prefix mới (nếu khác nhau)
    if old_prefix:
        suffix = obj_path[len(old_prefix):]
        new_obj_path = new_prefix + suffix
    else:
        new_obj_path = obj_path

    return f"s3://{new_bucket}/{new_obj_path}"


def _ensure_bucket(minio_client, bucket_name: str, dry_run: bool):
    """Tạo bucket nếu chưa tồn tại."""
    try:
        if minio_client.bucket_exists(bucket_name):
            logger.info(f"  Bucket '{bucket_name}' đã tồn tại.")
            return
        if dry_run:
            logger.info(f"  [DRY-RUN] Sẽ tạo bucket '{bucket_name}'")
            return
        minio_client.make_bucket(bucket_name)
        logger.info(f"  ✔ Đã tạo bucket '{bucket_name}'")
    except Exception as e:
        logger.error(f"  ✗ Không thể tạo bucket '{bucket_name}': {e}")
        raise


# ─────────────────────────────────────────────────────────────────────────────
# State file — ghi nhớ các rule đã hoàn thành để skip khi chạy lại
# ─────────────────────────────────────────────────────────────────────────────

STATE_FILE = "logs/migrate_state.json"


def _load_state() -> dict:
    """Đọc state từ file JSON. Trả về dict rỗng nếu chưa có."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"minio_done": [], "mongodb_done": [], "minio_progress": {}}


def _save_state(state: dict):
    """Ghi state ra file JSON."""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"  ⚠ Không thể ghi state file: {e}")


def _minio_rule_key(src_bucket: str, src_prefix: str, dst_bucket: str, dst_prefix: str) -> str:
    return f"minio|{src_bucket}/{src_prefix}→{dst_bucket}/{dst_prefix}"


def _mongo_rule_key(db: str, collection: str, field: str, old_pattern: str) -> str:
    return f"mongodb|{db}.{collection}.{field}|{old_pattern}"


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: Migrate MinIO objects
# ─────────────────────────────────────────────────────────────────────────────

def migrate_minio(cfg: Config):
    """
    Sao chép objects từ bucket/prefix cũ sang bucket/prefix mới.
    Không xóa dữ liệu cũ.
    """
    from minio import Minio
    from minio.error import S3Error

    logger.info("=" * 65)
    logger.info("  BƯỚC 1/2 — MIGRATE MINIO OBJECTS")
    logger.info("=" * 65)

    client = Minio(
        cfg.minio_endpoint,
        access_key=cfg.minio_access,
        secret_key=cfg.minio_secret,
        secure=cfg.minio_secure,
    )

    # Tạo trước các bucket đích nếu chưa có
    for dst_bucket in [BRONZE_BUCKET, SILVER_BUCKET, GOLD_BUCKET]:
        _ensure_bucket(client, dst_bucket, cfg.dry_run)

    total_copied = 0
    total_skipped = 0
    total_errors = 0

    # Tải state để biết rule nào đã xong và vị trí cuối cùng
    state = _load_state()
    # Nếu chưa có cấu trúc progress, khởi tạo
    if "minio_progress" not in state:
        state["minio_progress"] = {}

    for src_bucket, src_prefix, dst_bucket, dst_prefix in MINIO_MIGRATION_RULES:
        rule_key = _minio_rule_key(src_bucket, src_prefix, dst_bucket, dst_prefix)
        logger.info("")
        logger.info(
            f"  ── Rule: '{src_bucket}/{src_prefix}' → '{dst_bucket}/{dst_prefix}'"
        )

        # Bỏ qua rule đã hoàn thành trước đó
        if rule_key in state["minio_done"]:
            logger.info(f"  ✔ Rule này đã migrate xong (theo state file). Bỏ qua.")
            continue

        # Kiểm tra bucket nguồn tồn tại không
        try:
            if not client.bucket_exists(src_bucket):
                logger.warning(f"  ⚠ Bucket nguồn '{src_bucket}' không tồn tại. Bỏ qua rule này.")
                continue
        except S3Error as e:
            logger.error(f"  ✗ Lỗi kiểm tra bucket '{src_bucket}': {e}")
            continue

        rule_copied = 0
        rule_skipped = 0
        rule_errors = 0
        processed_counter = 0
        # Đếm số lần liên tiếp gặp StorageFull để early-abort
        consecutive_full_errors = 0
        MAX_CONSECUTIVE_FULL = 10
        # Lấy vị trí resume nếu có
        last_processed = state["minio_progress"].get(rule_key)

        try:
            objects = client.list_objects(src_bucket, prefix=src_prefix, recursive=True)
        except S3Error as e:
            logger.error(f"  ✗ Không thể liệt kê objects trong '{src_bucket}/{src_prefix}': {e}")
            continue

        for obj in objects:
            src_object_name = obj.object_name
            # Skip objects đã được xử lý trong lần chạy trước
            if last_processed and src_object_name <= last_processed:
                continue

            # Tính object_name trong bucket đích
            if src_prefix and src_object_name.startswith(src_prefix):
                relative = src_object_name[len(src_prefix):]
            else:
                relative = src_object_name
            dst_object_name = dst_prefix + relative

            if not dst_object_name:
                rule_skipped += 1
                continue

            try:
                # Kiểm tra đã tồn tại ở bucket đích chưa
                try:
                    client.stat_object(dst_bucket, dst_object_name)
                    rule_skipped += 1
                    consecutive_full_errors = 0
                    logger.debug(
                        f"  → SKIP (đã tồn tại): {dst_bucket}/{dst_object_name}"
                    )
                    continue
                except S3Error as e:
                    if e.code != "NoSuchKey":
                        raise

                if cfg.dry_run:
                    logger.info(
                        f"  [DRY-RUN] {src_bucket}/{src_object_name} "
                        f"→ {dst_bucket}/{dst_object_name}"
                    )
                    rule_copied += 1
                    processed_counter += 1
                    continue

                # Server-side copy (không tải dữ liệu về client, tiết kiệm RAM & băng thông)
                from minio.commonconfig import CopySource
                MAX_RETRIES = 3
                for attempt in range(1, MAX_RETRIES + 1):
                    try:
                        client.copy_object(
                            bucket_name=dst_bucket,
                            object_name=dst_object_name,
                            source=CopySource(src_bucket, src_object_name),
                        )
                        consecutive_full_errors = 0
                        break  # thành công
                    except S3Error as copy_err:
                        if copy_err.code == "XMinioStorageFull":
                            consecutive_full_errors += 1
                            if consecutive_full_errors >= MAX_CONSECUTIVE_FULL:
                                logger.error(
                                    f"  ✗ Đĩa MinIO đầy liên tục ({consecutive_full_errors} lần). Dừng rule '{src_bucket}/{src_prefix}' để tránh mất dữ liệu."
                                )
                                raise RuntimeError("XMinioStorageFull: early abort")
                            wait = 2 ** attempt
                            logger.warning(
                                f"  ⚠ Disk full (lần {attempt}/{MAX_RETRIES}), chờ {wait}s rồi thử lại: {src_object_name}"
                            )
                            time.sleep(wait)
                            if attempt == MAX_RETRIES:
                                raise
                        else:
                            raise

                rule_copied += 1
                processed_counter += 1
                logger.debug(
                    f"  ✔ Copied: {src_bucket}/{src_object_name} "
                    f"→ {dst_bucket}/{dst_object_name}"
                )

                # Kiểm tra limit max objects nếu được chỉ định
                if cfg.max_objects is not None and processed_counter >= cfg.max_objects:
                    # Lưu vị trí cuối cùng để resume lần sau
                    state["minio_progress"][rule_key] = src_object_name
                    _save_state(state)
                    logger.info(f"  ⚡ Đã đạt max_objects={cfg.max_objects}. Dừng rule tạm thời, sẽ resume ở lần chạy sau.")
                    raise RuntimeError("max_objects_reached")

            except RuntimeError as e:
                # Early-abort hoặc max object limit
                logger.error(f"  ✗ Early abort rule: {e}")
                rule_errors += 1
                # Lưu vị trí nếu chưa lưu
                if cfg.max_objects is not None and "max_objects_reached" in str(e):
                    state["minio_progress"][rule_key] = src_object_name
                    _save_state(state)
                break
            except Exception as e:
                rule_errors += 1
                logger.error(
                    f"  ✗ Lỗi copy '{src_bucket}/{src_object_name}': {e}"
                )

        logger.info(
            f"  Rule xong: copied={rule_copied}, skipped={rule_skipped}, errors={rule_errors}"
        )
        total_copied += rule_copied
        total_skipped += rule_skipped
        total_errors += rule_errors

        # Ghi state nếu rule hoàn thành không có lỗi
        if rule_errors == 0 and not cfg.dry_run:
            if rule_key not in state["minio_done"]:
                state["minio_done"].append(rule_key)
                _save_state(state)
                logger.info(f"  ✔ Đã lưu state: rule '{rule_key}' hoàn tất.")

    logger.info("")
    logger.info("  ── TỔNG KẾT MINIO ──")
    logger.info(f"  ✔ Đã copy   : {total_copied:,} objects")
    logger.info(f"  ⏭ Bỏ qua    : {total_skipped:,} objects (đã tồn tại)")
    logger.info(f"  ✗ Lỗi       : {total_errors:,} objects")

    return total_errors == 0


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: Cập nhật path trong MongoDB
# ─────────────────────────────────────────────────────────────────────────────

def migrate_mongodb(cfg: Config):
    """
    Cập nhật các trường path trong MongoDB để trỏ sang bucket mới.
    Không tạo collection mới, không xóa document cũ — chỉ updateMany.
    """
    from pymongo import MongoClient, UpdateMany
    from pymongo.errors import PyMongoError

    logger.info("")
    logger.info("=" * 65)
    logger.info("  BƯỚC 2/2 — CẬP NHẬT PATH TRONG MONGODB")
    logger.info("=" * 65)

    mongo = MongoClient(cfg.mongo_uri)

    total_matched = 0
    total_modified = 0

    # Tải state để biết rule nào đã xong
    state = _load_state()

    # YÊU CẦU ĐẶC BIỆT: Copy toàn bộ smart_checkout sang collection DB
    try:
        old_col = mongo["smart_checkout"]["products"]
        new_col = mongo["collection"]["products"]
        if new_col.count_documents({}) == 0 and old_col.count_documents({}) > 0:
            logger.info("")
            logger.info("  ── COPY DATA: 'smart_checkout.products' -> 'collection.products'")
            if not cfg.dry_run:
                docs = list(old_col.find())
                if docs:
                    new_col.insert_many(docs)
                    logger.info(f"  ✔ Đã copy {len(docs)} documents sang DB 'collection'.")
        else:
            logger.info("")
            logger.info("  ── COPY DATA: 'collection.products' đã có dữ liệu hoặc 'smart_checkout.products' trống. Bỏ qua.")
    except Exception as e:
        logger.error(f"  ✗ Lỗi copy database sang 'collection': {e}")

    for rule in MONGO_PATH_RULES:
        db_name   = rule["db"]
        coll_name = rule["collection"]
        fields    = rule["fields"]
        old_bucket = rule["old_bucket"]
        new_bucket = rule["new_bucket"]
        old_prefix = rule["old_prefix"]
        new_prefix = rule["new_prefix"]

        logger.info("")
        logger.info(
            f"  ── {db_name}.{coll_name} | fields={fields} | "
            f"'{old_bucket}/{old_prefix}' → '{new_bucket}/{new_prefix}'"
        )

        try:
            collection = mongo[db_name][coll_name]
        except Exception as e:
            logger.error(f"  ✗ Không thể truy cập {db_name}.{coll_name}: {e}")
            continue

        for field in fields:
            # Xây dựng các prefix cũ có thể có trong MongoDB
            # (lưu theo cả dạng "s3://bucket/..." và "bucket/..." và "s3a://bucket/...")
            old_path_patterns = [
                f"s3://{old_bucket}/{old_prefix}",
                f"s3a://{old_bucket}/{old_prefix}",
                f"{old_bucket}/{old_prefix}",
            ]

            for old_pattern in old_path_patterns:
                new_pattern = f"s3://{new_bucket}/{new_prefix}"
                mongo_key = _mongo_rule_key(db_name, coll_name, field, old_pattern)

                # Bỏ qua nếu đã hoàn thành theo state file
                if mongo_key in state["mongodb_done"]:
                    logger.info(
                        f"    Field={field!r} | pattern={old_pattern!r} → đã migrate xong (state file). Bỏ qua."
                    )
                    continue

                # Kiểm tra nhanh: còn document nào với path cũ không?
                try:
                    remaining = collection.count_documents(
                        {field: {"$regex": f"^{_escape_regex(old_pattern)}"}},
                        limit=1,
                    )
                    if remaining == 0:
                        logger.info(
                            f"    Field={field!r} | pattern={old_pattern!r} → 0 docs còn path cũ. Bỏ qua."
                        )
                        # Ghi vào state để lần sau không check lại
                        if not cfg.dry_run and mongo_key not in state["mongodb_done"]:
                            state["mongodb_done"].append(mongo_key)
                            _save_state(state)
                        continue
                except Exception:
                    pass  # Nếu count lỗi, vẫn xử lý bình thường

                # Tìm các documents có field này bắt đầu bằng pattern cũ
                # Dùng $regex để khớp prefix

                try:
                    cursor = collection.find(
                        {field: {"$regex": f"^{_escape_regex(old_pattern)}"}},
                        {field: 1, "_id": 1},
                        batch_size=cfg.batch_size,
                    )

                    updates = []
                    matched = 0
                    modified = 0

                    for doc in cursor:
                        matched += 1
                        old_val = doc.get(field, "")
                        if not old_val:
                            continue

                        # Tính path mới
                        new_val = _new_path_from_old(
                            old_val, old_bucket, new_bucket, old_prefix, new_prefix
                        )
                        if new_val is None:
                            # Thử không có s3:// prefix
                            clean_old = old_val.replace("s3://", "").replace("s3a://", "")
                            new_val = _new_path_from_old(
                                "s3://" + clean_old, old_bucket, new_bucket, old_prefix, new_prefix
                            )
                        if new_val is None:
                            logger.debug(f"  Không thể convert path: {old_val}")
                            continue

                        if cfg.dry_run:
                            logger.info(
                                f"  [DRY-RUN] {db_name}.{coll_name}.{field}: "
                                f"{old_val!r} → {new_val!r}"
                            )
                            modified += 1
                            continue

                        # Cập nhật document
                        result = collection.update_one(
                            {"_id": doc["_id"]},
                            {"$set": {field: new_val}},
                        )
                        if result.modified_count > 0:
                            modified += 1
                            logger.debug(
                                f"  ✔ Updated [{doc['_id']}].{field}: {old_val!r} → {new_val!r}"
                            )

                    if matched > 0:
                        logger.info(
                            f"    Field={field!r} | pattern={old_pattern!r} → "
                            f"matched={matched}, modified={modified}"
                        )
                        total_matched += matched
                        total_modified += modified

                    # Ghi state nếu xử lý xong mà 0 modified (không còn path cũ nào)
                    if not cfg.dry_run and modified == matched and mongo_key not in state["mongodb_done"]:
                        state["mongodb_done"].append(mongo_key)
                        _save_state(state)

                except PyMongoError as e:
                    logger.error(
                        f"  ✗ Lỗi MongoDB khi xử lý {db_name}.{coll_name}.{field}: {e}"
                    )

    mongo.close()

    logger.info("")
    logger.info("  ── TỔNG KẾT MONGODB ──")
    logger.info(f"  ✔ Đã khớp   : {total_matched:,} documents")
    logger.info(f"  ✔ Đã sửa    : {total_modified:,} documents")


def _escape_regex(s: str) -> str:
    """Escape các ký tự đặc biệt trong regex để dùng làm prefix match."""
    import re
    return re.escape(s)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3: Verify — kiểm tra tổng quan sau migrate
# ─────────────────────────────────────────────────────────────────────────────

def verify(cfg: Config):
    """In thống kê nhanh về số objects / documents sau migrate."""
    from minio import Minio
    from minio.error import S3Error
    from pymongo import MongoClient

    logger.info("")
    logger.info("=" * 65)
    logger.info("  KIỂM TRA SAU MIGRATE")
    logger.info("=" * 65)

    # MinIO
    try:
        minio = Minio(
            cfg.minio_endpoint,
            access_key=cfg.minio_access,
            secret_key=cfg.minio_secret,
            secure=cfg.minio_secure,
        )
        for bkt in ["products-images", "smart-checkout", BRONZE_BUCKET, SILVER_BUCKET, GOLD_BUCKET]:
            try:
                if not minio.bucket_exists(bkt):
                    logger.info(f"  MinIO bucket '{bkt}': KHÔNG TỒN TẠI")
                    continue
                count = sum(1 for _ in minio.list_objects(bkt, recursive=True))
                logger.info(f"  MinIO bucket '{bkt}': {count:,} objects")
            except S3Error as e:
                logger.warning(f"  MinIO bucket '{bkt}': lỗi liệt kê — {e}")
    except Exception as e:
        logger.error(f"  ✗ Không kết nối được MinIO: {e}")

    # MongoDB
    try:
        mongo = MongoClient(cfg.mongo_uri)
        checks = [
            ("smart_checkout", "products"),
            ("preprocessing", "cleaning"),
            ("preprocessing", "integrated"),
            ("preprocessing", "transformed"),
            ("processing", "objects"),
        ]
        for db_name, coll in checks:
            try:
                count = mongo[db_name][coll].count_documents({})
                logger.info(f"  MongoDB {db_name}.{coll}: {count:,} documents")
            except Exception as e:
                logger.warning(f"  MongoDB {db_name}.{coll}: lỗi — {e}")
        mongo.close()
    except Exception as e:
        logger.error(f"  ✗ Không kết nối được MongoDB: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Migrate dữ liệu Smart Checkout sang Medallion Architecture (Bronze/Silver/Gold)"
    )
    parser.add_argument(
        "--step",
        choices=["all", "minio", "mongodb", "verify"],
        default="all",
        help="Bước migrate cần chạy: all (mặc định), minio, mongodb, verify",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Chỉ in ra những gì sẽ thay đổi, không thực sự sửa dữ liệu",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Số objects/docs mỗi lần xử lý (mặc định 200)",
    )
    parser.add_argument(
        "--max-objects",
        type=int,
        default=None,
        help="Giới hạn số objects tối đa sẽ được copy trong một lần chạy (để chạy dần dần). Nếu None, copy hết.",
    )
    parser.add_argument(
        "--mongo-uri",
        default=None,
        help="Override MongoDB URI (mặc định đọc từ .env hoặc MONGO_URI)",
    )
    parser.add_argument(
        "--minio-endpoint",
        default=None,
        help="Override MinIO endpoint (mặc định đọc từ .env hoặc MINIO_ENDPOINT)",
    )
    args = parser.parse_args()

    cfg = Config(batch_size=args.batch_size, dry_run=args.dry_run, max_objects=args.max_objects)
    if args.mongo_uri:
        cfg.mongo_uri = args.mongo_uri
    if args.minio_endpoint:
        cfg.minio_endpoint = args.minio_endpoint

    logger.info("=" * 65)
    logger.info("  SMART CHECKOUT — DATA MIGRATION (Medallion Architecture)")
    logger.info("=" * 65)
    logger.info(f"  Chế độ      : {'DRY-RUN (không thay đổi thực sự)' if cfg.dry_run else 'LIVE'}")
    logger.info(f"  Bước        : {args.step}")
    logger.info(f"  Batch size  : {cfg.batch_size}")
    logger.info(f"  MongoDB URI : {cfg.mongo_uri}")
    logger.info(f"  MinIO EP    : {cfg.minio_endpoint}")
    logger.info("=" * 65)

    if args.step in ("all", "verify"):
        logger.info("\n[PRE-MIGRATE] Thống kê trước khi migrate:")
        verify(cfg)

    if args.step in ("all", "minio"):
        migrate_minio(cfg)

    if args.step in ("all", "mongodb"):
        migrate_mongodb(cfg)

    if args.step in ("all", "verify"):
        logger.info("\n[POST-MIGRATE] Thống kê sau khi migrate:")
        verify(cfg)

    logger.info("")
    logger.info("=" * 65)
    logger.info("  HOÀN TẤT MIGRATE")
    logger.info("=" * 65)


if __name__ == "__main__":
    main()
