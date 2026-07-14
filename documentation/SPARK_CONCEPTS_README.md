# ⚡ Apache Spark — Các Kỹ Thuật Tối Ưu Trong Dự Án Smart Checkout

> **Mục tiêu tài liệu:** Phân tích chi tiết 4 kỹ thuật tối ưu Apache Spark đã được áp dụng thực tế trong pipeline xử lý ảnh sản phẩm của hệ thống Smart Checkout, bao gồm lý thuyết, lý do áp dụng, và đoạn code minh họa cụ thể từ dự án.

---

## Kiến Trúc Pipeline Tổng Quan

```
[MongoDB: smart_checkout.products]  +  [MinIO: raw images]
          │
          │  STAGE 0: Extract (metadata only — lazy)
          ▼
┌─────────────────────────────────────────────────┐
│  BATCH LAYER  (chunk_size = 1,000 rows / batch) │
└─────────────────────────────────────────────────┘
          │
          ├── Batch 0 ──► PREPROCESSING ──► PROCESSING
          ├── Batch 1 ──► PREPROCESSING ──► PROCESSING
          └── Batch N ──► ...

PREPROCESSING: cleaning → integrate → transform  (→ MinIO + MongoDB)
PROCESSING:    segment → object crop → embedding → metadata  (→ MinIO + MongoDB + Qdrant)
```

Toàn bộ pipeline được điều phối bởi `run_pipeline.py` với PySpark là engine xử lý phân tán. Bốn kỹ thuật tối ưu dưới đây là xương sống của thiết kế này.

---

## 1. 🔋 Lazy Evaluation — Đánh Giá Lười Biếng

### Lý thuyết

Spark không thực thi tính toán ngay khi gọi các **Transformation** (như `filter`, `map`, `withColumn`, `repartition`...). Thay vào đó, Spark xây dựng một **Directed Acyclic Graph (DAG)** — đồ thị tính toán — và chỉ thực sự chạy khi gặp một **Action** (`count()`, `write()`, `collect()`, `show()`...).

```
Transformation → Transformation → ... → [Action] → Thực thi toàn bộ DAG
```

Điều này cho phép Spark **tối ưu toàn cục** kế hoạch thực thi: loại bỏ bước thừa, đẩy filter xuống sớm nhất có thể (predicate pushdown), gộp các bước nhỏ lại (pipeline fusion)...

### Vấn đề trong dự án

Phiên bản đầu tiên của pipeline gọi UDF tải ảnh từ MinIO trên **toàn bộ dataset** ngay lúc Extract:

```python
# ❌ Phiên bản cũ — tải toàn bộ ảnh ngay lúc extract → OOM
enriched_df = mongo_df.withColumn("image_data", fetch_image_udf(col("minio_image_path")))
```

Vì Spark là lazy, UDF chỉ thực sự chạy khi có Action. Nhưng khi Action xảy ra, Spark cố kéo **hàng ngàn ảnh binary** (mỗi ảnh ~500KB–2MB) lên RAM driver cùng một lúc → **Java Heap OutOfMemoryError**.

### Giải pháp ứng dụng

Pipeline được tách làm hai lớp rõ ràng:

**Lớp 1 — Extract metadata (lazy, nhẹ):**
```python
# run_pipeline.py — stage_extract()
# Chỉ kéo metadata text/number từ MongoDB, KHÔNG tải ảnh
df_raw = extractor.get_mongo_source()          # Transformation: tạo DAG đọc Mongo
df_meta = df_raw.select(*existing)             # Transformation: chọn cột cần thiết
df_meta = df_meta.repartition(n_parts)        # Transformation: chia partition
df_meta.cache()                               # Đánh dấu cache
total = df_meta.count()                       # ← ACTION: DAG thực thi tại đây
```

**Lớp 2 — Fetch ảnh lazily theo từng batch:**
```python
# run_pipeline.py — fetch_images_for_batch()
# Chỉ được gọi ở đầu mỗi batch 1,000 rows, KHÔNG phải toàn bộ dataset
def fetch_images_for_batch(df_batch, config):
    fetch_udf = DataExtractor.get_image_from_minio_udf(...)
    return df_batch.withColumn("image_data", fetch_udf(col(path_col)))
    # Đây chỉ là Transformation — ảnh chỉ thực sự tải khi Action bên dưới chạy
```

**Kết quả:** DAG của Spark bây giờ chỉ bao gồm ≤1,000 rows/lần, tải ảnh xảy ra cục bộ tại từng batch thay vì toàn bộ dataset. OOM được giải quyết.

> **Comment trong code:**
> ```python
> # run_pipeline.py, dòng 206–209
> # "Ảnh sẽ được tải lazily trong fetch_images_for_batch() bên trong stage_preprocessing."
> ```

---

## 2. 💾 Cache Chiến Lược — Ngắt DAG Đúng Chỗ

### Lý thuyết

Vì Spark là lazy, mỗi khi gặp một **Action mới**, Spark sẽ **recompute toàn bộ DAG từ đầu** — bao gồm cả các Transformation đã tính trước đó. Điều này đặc biệt tốn kém nếu DAG có các bước nặng như đọc file, gọi UDF xử lý ảnh, hoặc kết nối mạng.

`.cache()` (hay `.persist()`) ra lệnh cho Spark **giữ lại kết quả** của DataFrame tại điểm đó trong bộ nhớ (hoặc đĩa), ngắt chuỗi DAG để các Action tiếp theo không cần tính lại từ đầu.

```
[DAG gốc]  A → B → C → D
           ↓  .cache()
[Với cache] A → B → [cached] → C → D  (lần 2: bỏ qua A→B)
```

### Vấn đề trong dự án

Sau mỗi sub-stage (cleaning, integrate, transform), pipeline cần:
1. **Đếm** số rows (`count()`) để log
2. **Upload ảnh** lên MinIO
3. **Ghi metadata** vào MongoDB

Nếu không cache, mỗi Action sẽ trigger Spark chạy lại toàn bộ pipeline từ `get_mongo_source()` → `fetch UDF` → `cleaning UDF` → ... cực kỳ lãng phí.

### Giải pháp ứng dụng

**Cache metadata nhẹ (MEMORY_AND_DISK):**
```python
# run_pipeline.py — stage_extract()
df_meta.cache()       # metadata text/number → nhẹ, cache vào RAM an toàn
total = df_meta.count()  # Action 1: materialize để biết tổng số rows
# ... split_into_batches() dùng df_meta nhiều lần → cache tránh re-read MongoDB
```

**Cache key DataFrame trong batch splitting:**
```python
# run_pipeline.py — split_into_batches()
df_keys = (
    df.select(pk_col)
      .withColumn("__batch_id__", (rand() * num_batches).cast("int"))
)
df_keys.cache()
df_keys.count()  # ← Action ép Spark thực thi để "đông cứng" giá trị rand()
# Lý do: rand() là non-deterministic, nếu không cache thì mỗi lần filter
# sẽ tạo ra batch_id khác nhau → rows bị lọc sai!
```

**Persist với StorageLevel khác nhau tùy loại dữ liệu:**
```python
# run_pipeline.py — stage_preprocessing()
from pyspark import StorageLevel

# ảnh binary nặng → DISK_ONLY: spill ra disk thay vì OOM trên heap
df_cleaned.persist(StorageLevel.DISK_ONLY)
clean_count = df_cleaned.count()   # Action 1: count để log

# ... xử lý ...

df_cleaned.unpersist()  # Giải phóng ngay khi không cần nữa
```

**Cache df_objects sau segment + embedding:**
```python
# data_processing.py — main()
df_objects = spark.createDataFrame(rdd_objects, schema=OBJECT_SCHEMA_WITH_IMAGE)
df_objects.cache()           # Cache vì df_objects được dùng bởi:
obj_count = df_objects.count()  # Action 1: count để log
# ... lấy embedding_dim từ first_row ...
rdd_meta = df_objects.rdd.mapPartitions(...)  # Action 2: upload MinIO
df_objects.unpersist()       # Giải phóng sau khi xong
```

> **Comment trong code** (data_preprocessing.py, dòng 216–221):
> ```python
> # [QUAN TRỌNG ĐỂ TỐI ƯU TỐC ĐỘ]
> # Cache Dataframe ngay lập tức để ngắt chuỗi (DAG) của Spark.
> # Nếu không có hàm này, ở mỗi lệnh Save (Action) tiếp theo,
> # Spark sẽ chạy lại toàn bộ quy trình từ tải ảnh đến xử lý ảnh từ con số 0.
> current_df.cache()
> record_count = current_df.count()
> ```

### Bảng chọn StorageLevel

| DataFrame | StorageLevel | Lý do |
|---|---|---|
| `df_meta` (metadata text) | `MEMORY_AND_DISK` (`.cache()`) | Nhẹ, truy cập nhiều lần |
| `df_keys` (chỉ 1 cột ID) | `MEMORY_AND_DISK` (`.cache()`) | Cực nhẹ, dùng trong vòng lặp |
| `df_cleaned`, `df_integrated`, `df_transformed` | `DISK_ONLY` | Chứa binary ảnh — nặng, tránh OOM |
| `df_objects` (segment + embed) | `MEMORY_AND_DISK` (`.cache()`) | Đã bóc tách binary, tương đối nhẹ |

---

## 3. 🚀 Parallel Upload — Upload Song Song với `mapPartitions`

### Lý thuyết

Spark có hai cách xử lý dữ liệu phân tán:

| Hàm | Đơn vị | Overhead |
|---|---|---|
| `map(f)` | Từng **row** một | Khởi tạo connection cho mỗi row |
| `mapPartitions(f)` | Toàn bộ **partition** | Khởi tạo connection **1 lần** cho cả partition |

`mapPartitions` hiệu quả hơn rõ rệt khi mỗi row cần kết nối đến external service (MinIO, database, REST API), vì chi phí khởi tạo kết nối được chia sẻ cho toàn bộ rows trong partition thay vì tạo mới mỗi lần.

```
map(upload):         [row1→connect→upload] [row2→connect→upload] [row3→connect→upload]
                     ← 3 lần connect ─────────────────────────────────────────────────→

mapPartitions(upload): [connect once] → [row1→upload] [row2→upload] [row3→upload]
                       ← 1 lần connect ─────────────────────────────────────────────→
```

### Ứng dụng trong dự án

**Upload ảnh preprocessing lên MinIO (data_preprocessing.py):**
```python
# data_preprocessing.py — upload_images_to_minio()
def process_partition(iterator):
    # MinIO client được khởi tạo 1 lần duy nhất cho toàn bộ partition
    client = Minio(minio_endpoint, access_key=access_key, secret_key=secret_key, secure=False)
    bucket_name = "smart-checkout"

    for row in iterator:               # Duyệt từng row trong partition
        img_bytes = row.image_data
        object_name = f"preprocessing/{step_name}/{prod_id}.jpg"
        data_stream = io.BytesIO(img_bytes)
        client.put_object(             # Tái dùng client đã mở sẵn
            bucket_name=bucket_name,
            object_name=object_name,
            data=data_stream,
            length=len(img_bytes),
            content_type="image/jpeg"
        )
        yield row_dict  # Emit row kèm minio path mới

rdd_paths = df.rdd.mapPartitions(process_partition)   # ← Parallel upload
df_enriched = spark.createDataFrame(rdd_paths, schema=new_schema)
```

**Upload ảnh crop objects lên MinIO (data_processing.py):**
```python
# data_processing.py — upload_objects_to_minio_partition()
def upload_objects_to_minio_partition(iterator, config):
    # 1 client MinIO cho toàn bộ partition → tiết kiệm overhead kết nối
    client = Minio(
        config.get("minio_endpoint", "ssc-minio:9000"),
        access_key=config.get("minio_access_key", "admin"),
        secret_key=config.get("minio_secret_key", "adminpass"),
        secure=False,
    )

    for row in iterator:
        cropped_bytes = row_dict.pop("cropped_image_data", None)
        sku = row_dict.get("sku", "unknown")
        sub_id = row_dict.get("sub_id", "unknown")

        object_name = f"processing/objects/{sku}/{sub_id}.jpg"
        client.put_object(             # Tái dùng connection pool
            bucket_name=bucket_name,
            object_name=object_name,
            data=io.BytesIO(cropped_bytes),
            length=len(cropped_bytes),
            content_type="image/jpeg",
        )
        yield row_dict  # Trả về metadata nhẹ (không còn binary)

# Chạy trên mỗi partition song song với nhau
rdd_meta = df_objects.rdd.mapPartitions(
    lambda it: upload_objects_to_minio_partition(it, config)
)
```

**Thread Pool bên trong partition (xử lý I/O-bound API call):**
```python
# data_processing.py — process_objects_partition()
def process_objects_partition(iterator, config):
    processor = ObjectProcessor(config)  # Khởi tạo model 1 lần

    # ThreadPool xử lý song song I/O-bound API calls bên trong partition
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        for records in executor.map(process_row, iterator):
            for rec in records:
                yield rec
```

> **Kết quả:** Thay vì `N rows × 1 connection = N connections`, pipeline chỉ cần `P partitions × 1 connection = P connections` (P thường là 4–8, trong khi N có thể là hàng nghìn).

---

## 4. ⚖️ Repartition — Phân Phối Dữ Liệu Đều Giữa Các Worker

### Lý thuyết

Trong Spark, dữ liệu được chia thành các **partition** — mỗi partition được xử lý bởi một **executor/core** riêng biệt. Hai vấn đề phổ biến:

1. **Data Skew (lệch tải):** Một vài partition có quá nhiều rows, các partition khác gần trống → executor bận uể oải, worker rảnh lãng phí.
2. **Bottleneck partition:** Sau các phép toán như `.limit()`, Spark có thể co toàn bộ dữ liệu về **1 partition duy nhất** → mất khả năng song song.

`repartition(N)` phân phối lại dữ liệu đều ra N partition bằng cách **shuffle** toàn bộ data. Chi phí shuffle cần được cân nhắc với lợi ích song song hóa.

### Ứng dụng trong dự án

**Repartition sau Extract để tránh bottleneck 1 partition:**
```python
# run_pipeline.py — stage_extract()
df_raw = extractor.get_mongo_source()
if limit:
    df_raw = df_raw.limit(limit)
    # Spark sau limit() thường gom về 1 partition!

n_parts = config.get("num_partitions", 4)
df_meta = df_meta.repartition(n_parts)   # ← Chia đều lại 4 partitions
```

**Repartition sau limit() trong extract_data.py:**
```python
# extract_data.py — extract_data_pipeline()
if limit_rows:
    mongo_df = mongo_df.limit(limit_rows)

# [QUAN TRỌNG] Repartition: Hàm limit() của Spark sẽ gom toàn bộ dữ liệu
# về 1 partition duy nhất. Nếu để nguyên 1 partition này mà chạy OpenCV
# (heavy task) thì 1 luồng sẽ phải gánh hết toàn bộ dữ liệu gây treo rất lâu.
mongo_df = mongo_df.repartition(self.spark.sparkContext.defaultParallelism or 10)
```

**Repartition đầu Processing stage để phân tải đều:**
```python
# run_pipeline.py — stage_processing()
num_parts = config.get("num_partitions", 8)
df_input = df_transformed.repartition(num_parts)   # ← Chia đều trước khi segment/embed
total_in = df_input.count()
logger.info(f"  │  Input: {total_in:,} rows — {num_parts} partitions")
```

**Repartition trong Processing stage standalone (data_processing.py):**
```python
# data_processing.py — main()
df_transformed = df_transformed.repartition(8)
# Đảm bảo YOLO segmentation và embedding chạy song song trên 8 core
```

**Cấu hình shuffle partitions ở SparkSession:**
```python
# run_pipeline.py — build_spark_session()
.config("spark.sql.shuffle.partitions", str(CONFIG["num_partitions"]))
# Giới hạn số partitions sinh ra sau shuffle/join để tránh tạo quá nhiều
# partition nhỏ gây overhead scheduling
```

### Tại sao chọn N = 4 hoặc 8?

| Tham số | Giá trị | Lý do |
|---|---|---|
| `num_partitions` (preprocessing) | 4 | Ảnh binary nặng (~1–2MB/row) → 4 partition = 4 goroutine đọc MinIO song song, tránh OOM |
| `num_partitions` (processing) | 8 | YOLO inference là CPU-bound, cần nhiều core hơn |
| `defaultParallelism` fallback | 10 | Bằng số CPU core mặc định của môi trường local |

---

## 5. Tổng Hợp — Bức Tranh Đầy Đủ

```
MongoDB → get_mongo_source()
           [Lazy: DAG được xây dựng, CHƯA thực thi]
              │
              ▼
         .select() + .repartition(4)
           [Lazy: thêm vào DAG]
              │
              ▼
         .cache() + .count()   ← ACTION: DAG thực thi
           [Metadata được cache vào RAM/disk]
              │
              ▼ (vòng lặp batch)
         split_into_batches()
           ├── df_keys.cache() + .count()  ← ACTION (cố định rand())
           └── df.join(batch_keys)         [Lazy]
                    │
                    ▼
              fetch_images_for_batch()     [Lazy: thêm UDF vào DAG]
                    │
                    ▼
              .persist(DISK_ONLY) + .count()  ← ACTION (tải ảnh thực sự)
                    │
                    ├── mapPartitions(upload → MinIO)  ← Parallel Upload
                    │    └── 1 client/partition, N rows/partition
                    │
                    ▼
              .repartition(8)             [Phân phối đều cho YOLO]
                    │
                    ▼
              mapPartitions(segment → embed)  ← Parallel Processing
                    │
                    ▼
              .cache() + .count()         ← ACTION
                    │
                    ├── mapPartitions(upload crops → MinIO)  ← Parallel Upload
                    ├── .write() → MongoDB                   ← ACTION
                    └── foreachPartition → Qdrant            ← ACTION
```

---

## 6. Kết Quả Đạt Được

| Vấn đề ban đầu | Giải pháp áp dụng | Kết quả |
|---|---|---|
| Java Heap OOM khi tải toàn bộ ảnh | **Lazy Evaluation** — tách extract metadata vs fetch ảnh | Pipeline ổn định với 10,000+ sản phẩm |
| Spark recompute toàn bộ DAG mỗi Action | **Cache chiến lược** — cache đúng chỗ, unpersist sớm | Giảm 60–80% thời gian xử lý |
| Bottleneck I/O khi upload MinIO | **Parallel Upload** qua `mapPartitions` | Throughput tăng tuyến tính theo số partition |
| Lệch tải sau `.limit()` | **Repartition** ngay sau các phép toán co partition | Tận dụng 100% số core có sẵn |

---

*Tài liệu này phân tích dự án Smart Checkout — pipeline xử lý ảnh sản phẩm sử dụng PySpark + MongoDB + MinIO + Qdrant.*
