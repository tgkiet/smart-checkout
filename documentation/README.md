# VSSCS — Vietnam Supermarket Smart Checkout System

## Tổng quan hệ thống

VSSCS là hệ thống **thanh toán siêu thị thông minh** sử dụng Computer Vision và Vector Database để tự động nhận diện sản phẩm từ ảnh và tính tiền giỏ hàng theo thời gian thực.

Hệ thống được chia thành 4 phân hệ chính:

| Phân hệ | Công nghệ | Mô tả |
|---|---|---|
| **Collection** | Python, BeautifulSoup, requests | Thu thập dữ liệu sản phẩm từ Tiki |
| **Preprocessing** | Apache Spark, MongoDB, MinIO | Làm sạch và chuẩn hóa dữ liệu |
| **Processing** | Apache Spark, YOLOv8, CLIP, Qdrant | AI inference & lưu vector DB |
| **Inference API + UI** | FastAPI, Qdrant, HTML/JS | Phục vụ real-time checkout |

---

## Kiến trúc tổng thể

```
┌─────────────────────────────────────────────────────────────────┐
│                     DATA PIPELINE (Offline)                     │
│                                                                 │
│  [Tiki Website]                                                 │
│       ↓ crawler.py / gdrive_collector.py                        │
│  [Raw Data: JSON + Images]                                      │
│       ↓ storage.py → MinIO (bronze/)                            │
│       ↓              MongoDB (raw_data)                         │
│                                                                 │
│  [PREPROCESSING — PySpark]                                      │
│   Extract → Clean → Integrate → Transform                       │
│       ↓ ảnh/metadata → MinIO (preprocessing/) + MongoDB         │
│                                                                 │
│  [PROCESSING — PySpark + Inference API]                         │
│   Segment(YOLO) → Crop → Embed(CLIP) → Push                     │
│       ↓ ảnh crop → MinIO (processing/objects/)                  │
│       ↓ metadata → MongoDB (processing.objects)                 │
│       ↓ vectors  → Qdrant (smart_checkout_objects)              │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                   INFERENCE SERVICE (Online)                    │
│                                                                 │
│  [Browser UI: ssc_ui]                                           │
│       ↓ POST /checkout/ (upload ảnh)                            │
│  [SSC Service: FastAPI :8801]                                   │
│       ↓ POST /api/v1/segment                                    │
│  [Inference API: FastAPI :8800]  ← YOLOv8-seg + CLIP           │
│       ↓ bbox + mask                                             │
│  [SSC Service] → crop ảnh → POST /api/v1/embed                 │
│       ↓ embedding vector                                        │
│  [Qdrant :6433] → similarity search → metadata                 │
│       ↓                                                         │
│  [Browser UI] ← Cart items + total price                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Cấu trúc thư mục

```
smart-checkout/
├── main.py                    # Pipeline orchestrator (preprocessing + processing)
├── requirements.txt           # Dependencies cho Spark pipeline
├── requirements_api.txt       # Dependencies cho Inference API
├── .env                       # Biến môi trường (MongoDB, MinIO, GDrive)
│
├── collection/                # Thu thập dữ liệu
│   ├── crawler.py             # Crawl Tiki (15 danh mục, ~9k-11k SP/leaf)
│   ├── gdrive_collector.py    # Download từ Google Drive
│   ├── object_scrape.py       # Scrape ảnh object riêng lẻ
│   ├── cleaning.py            # Làm sạch dữ liệu thô
│   ├── storage.py             # Upload lên MinIO
│   └── utils/
│       └── gdrive_handler.py  # Google Drive API helper
│
├── preprocessing/             # Tiền xử lý (Spark pipeline)
│   ├── data_preprocessing.py  # Spark job orchestrator
│   ├── extract_data.py        # Đọc MongoDB + fetch ảnh từ MinIO (UDF)
│   ├── cleaning.py            # Làm sạch metadata + ảnh
│   ├── integrate.py           # Chuẩn hóa schema
│   ├── transform.py           # Đổi nền ảnh → trắng
│   └── storage.py             # Lưu xuống MongoDB
│
├── processing/                # AI Processing (Spark + Inference API)
│   ├── data_processing.py     # Spark job orchestrator (5 bước)
│   ├── object_processor.py    # Segment → Crop → Embed → Metadata
│   ├── api_server.py          # Inference API server (FastAPI :8800)
│   ├── labeling.py            # Gán nhãn cho dữ liệu
│   ├── yolo11n-seg.pt         # YOLO model weights
│   └── utils/
│       ├── segmentation_model.py  # YOLOv8x-seg wrapper
│       └── embedding_model.py     # CLIP / ResNet50 wrapper
│
├── ssc_service/               # Backend API (FastAPI :8801)
│   ├── main.py                # App FastAPI + CORS
│   ├── config.py              # Cấu hình endpoints
│   ├── routers/
│   │   └── checkout.py        # POST /checkout/ endpoint
│   ├── services/
│   │   ├── inference.py       # HTTP client → Inference API
│   │   └── vector_db.py       # Qdrant client + search
│   └── utils/
│       └── image.py           # Crop/mask ảnh PIL
│
├── ssc_ui/                    # Frontend Web UI
│   ├── index.html             # Single-page app
│   ├── js/
│   │   ├── app.js             # Logic chính (drag/drop, checkout flow)
│   │   ├── api.js             # HTTP calls tới ssc_service
│   │   └── ui.js              # DOM rendering
│   └── css/
│       ├── variables.css      # CSS custom properties
│       ├── layout.css         # Bố cục trang
│       ├── components.css     # UI components
│       └── animations.css     # Micro-animations
│
├── docker/
│   ├── dockerfile/
│   │   ├── Dockerfile.spark   # Image Spark + dependencies pipeline
│   │   └── Dockerfile.api     # Image Inference API (python:3.10-slim)
│   └── docker_compose/
│       ├── spark-docker-compose.yml    # Spark Master + Worker
│       ├── mongo-docker-compose.yml    # MongoDB + Mongo Express
│       ├── minio-docker-compose.yml    # MinIO Object Storage
│       ├── qdrant-docker-compose.yml   # Qdrant Vector DB
│       ├── kafka-docker-compose.yml    # Kafka + Kafka UI
│       └── docker-compose.api.yml      # Inference API container
│
├── config/                    # Google OAuth credentials
├── data/                      # Docker volumes (mongo, minio, qdrant, kafka)
├── logs/                      # Log files từ pipeline
└── documentation/             # Tài liệu dự án
```

---

## Phân hệ 1: Collection (Thu thập dữ liệu)

### Mô tả
Thu thập dữ liệu sản phẩm từ sàn thương mại điện tử Tiki.vn, lưu trữ raw data vào hệ thống dưới dạng JSON + ảnh JPEG.

### Nguồn dữ liệu
- **Tiki.vn**: 15 danh mục gốc (Thực phẩm, Điện tử, Thời trang, v.v.)
- Mỗi leaf category: **9.000 – 11.000 sản phẩm**
- **Google Drive**: Download dataset được chuẩn bị sẵn qua `gdrive_collector.py`

### Chiến lược crawl (`crawler.py`)
1. **Phase 1 — Reuse**: Copy dữ liệu cũ đã crawl, ưu tiên leaf có nhiều data cũ nhất
2. **Phase 2 — Crawl mới**: Sử dụng keyword search API của Tiki để crawl bổ sung

```
SSCCollectionPipeline
    ├── BƯỚC 1: Resolve tất cả leaf categories (đệ quy từ 15 root)
    ├── BƯỚC 2: Build index data cũ (đếm SP theo leaf)
    ├── BƯỚC 3: Sort giảm dần → leaf nhiều data cũ xử lý trước
    └── BƯỚC 4: Xử lý từng leaf (Phase 1 → Phase 2)
               ├── ThreadPoolExecutor (10 threads/leaf)
               └── Resume qua state file (crawler_state_v2.json)
```

### Cấu trúc dữ liệu lưu trữ
```
ssc_data_lake/
├── bronze/           # Data cũ (v1)
│   ├── products/     # <uuid>.json
│   └── images/       # <uuid>.jpg
└── bronze_v2/        # Data mới (v2)
    ├── products/
    └── images/
```

### Chạy crawler
```bash
cd collection
python crawler.py
python gdrive_collector.py
```

---

## Phân hệ 2: Preprocessing Pipeline (Apache Spark)

### Mô tả
Tiền xử lý dữ liệu thô qua 4 bước tuần tự trên Apache Spark, kết quả mỗi bước được lưu vào MongoDB và MinIO.

### Luồng xử lý

```
MongoDB (smart_checkout.products)
        ↓  [Extract] Spark read + MinIO UDF fetch ảnh
    DataFrame (metadata + image_data binary)
        ↓  [Clean] Làm sạch metadata, lọc ảnh lỗi
    DataFrame (cleaned)  → MongoDB preprocessing.cleaning
                         → MinIO preprocessing/clean/
        ↓  [Integrate] Chuẩn hóa schema
    DataFrame (integrated) → MongoDB preprocessing.integrated
                           → MinIO preprocessing/integrate/
        ↓  [Transform] Đổi nền ảnh → trắng
    DataFrame (transformed) → MongoDB preprocessing.transformed
                            → MinIO preprocessing/transform/
```

### Các bước chi tiết

| Bước | Module | Chức năng |
|---|---|---|
| **Extract** | `extract_data.py` | Spark read MongoDB + UDF fetch ảnh từ MinIO |
| **Clean** | `cleaning.py` | Lọc record thiếu dữ liệu, chuẩn hóa tên trường |
| **Integrate** | `integrate.py` | Chuẩn hóa schema thống nhất |
| **Transform** | `transform.py` | Tách nền → background trắng |

### Tính năng kỹ thuật
- **Lazy evaluation**: Spark DAG chỉ thực thi khi có Action
- **Cache**: `.cache()` ngắt DAG chain sau mỗi bước, tránh re-compute
- **Parallel upload**: `mapPartitions` upload ảnh lên MinIO song song
- **Limit + Repartition**: Test với `limit_rows=1000`, `repartition` để phân phối đều

### Chạy Preprocessing
```bash
# Chạy toàn bộ hoặc từng stage
spark-submit \
  --master spark://ssc-spark-master:7077 \
  --packages org.mongodb.spark:mongo-spark-connector_2.13:10.4.0 \
  main.py --stage preprocessing
```

---

## Phân hệ 3: Processing Pipeline (AI + Vector DB)

### Mô tả
Đọc dữ liệu đã qua Transform, chạy AI inference để phân vùng object, tạo embedding vector và lưu vào Qdrant.

### Luồng xử lý

```
MongoDB (preprocessing.transformed)
        ↓  Spark read (PaginateBySizePartitioner, 32MB/partition)
    DataFrame + repartition (defaultParallelism)
        ↓  mapPartitions → ObjectProcessor (song song)
           ├── [1] Segment: POST /api/v1/segment → YOLO bbox + mask (base64)
           ├── [2] Crop: Áp mask vào ảnh gốc → ảnh object sạch nền
           ├── [3] Embed: POST /api/v1/embed → CLIP vector (512-dim)
           └── [4] Metadata: Gán SKU, name, price, platform
        ↓
    [5a] mapPartitions → Upload crop → MinIO processing/objects/<sku>/<sub_id>.jpg
    [5b] DataFrame.write → MongoDB processing.objects
    [5c] Driver → ensure_qdrant_collection() (tạo collection nếu chưa có)
    [5d] foreachPartition → Qdrant upsert (batch 64 points/call)
```

### Inference API (`processing/api_server.py`)

Server FastAPI chạy trên port **8800**, khởi tạo model 1 lần khi boot:

| Endpoint | Method | Mô tả |
|---|---|---|
| `/api/v1/segment` | POST | YOLOv8x-seg → bbox + mask (PNG base64) |
| `/api/v1/predict` | POST | YOLOv8x → bbox only (dùng cho labeling) |
| `/api/v1/embed` | POST | CLIP → embedding vector (512-dim) |
| `/health` | GET | Health check |

### AI Models

| Model | Thư viện | Output | Dùng cho |
|---|---|---|---|
| **YOLOv8x-seg** | ultralytics | bbox + binary mask | Segmentation |
| **CLIP** (clip-vit-base-patch32) | transformers | L2-norm vector 512-dim | Embedding |
| **ResNet50** (fallback) | torchvision | vector 2048-dim | Embedding fallback |

### Chạy Processing
```bash
spark-submit \
  --master spark://ssc-spark-master:7077 \
  --packages org.mongodb.spark:mongo-spark-connector_2.13:10.4.0 \
  main.py --stage processing
```

---

## Phân hệ 4: Inference Service & UI

### SSC Service (`ssc_service/` — Port 8801)

Backend FastAPI trung gian kết nối UI ↔ Inference API ↔ Qdrant.

**Endpoint chính:**

```
POST /checkout/
  Body: multipart/form-data (image file)
  Response:
  {
    "items": [
      { "name": "...", "price": 29.9, "sku": "...", "score": 0.87 }
    ],
    "total_price": 59.8,
    "message": "Thành công"
  }
```

**Luồng xử lý:**

```
1. Nhận ảnh upload từ UI
2. POST /api/v1/segment → Inference API → danh sách detections
3. Với mỗi detection:
   a. Crop ảnh + áp mask (utils/image.py)
   b. POST /api/v1/embed → Inference API → embedding vector
   c. Qdrant.search(vector, limit=3, threshold=0.6)
   d. Nếu best_match.score > 0.6 → thêm vào items
4. Tổng hợp → trả về cart
```

**Cấu hình** (`config.py`):
```python
API_ENDPOINT = os.getenv("API_ENDPOINT", "http://localhost:8800/api/v1")
QDRANT_HOST  = os.getenv("QDRANT_HOST",  "localhost")
QDRANT_PORT  = int(os.getenv("QDRANT_PORT", 6433))
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "smart_checkout_objects")
```

### Chạy SSC Service
```bash
cd ssc_service
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8801 --reload
```

### SSC UI (`ssc_ui/`)

Single-page web app, không cần build tool:

- **Drag & Drop** hoặc Browse để upload ảnh
- Gọi `POST /checkout/` → hiển thị Cart Summary
- Tính tổng tiền tự động
- Responsive, dark theme, glassmorphism

```
ssc_ui/
├── index.html         ← Entry point
├── js/
│   ├── app.js         ← Orchestrator: event handlers + checkout flow
│   ├── api.js         ← fetch() wrapper → POST /checkout/
│   └── ui.js          ← renderItems(), showSpinner(), etc.
└── css/
    ├── variables.css  ← CSS tokens (colors, spacing, radius)
    ├── layout.css     ← Grid/flex layout
    ├── components.css ← Button, card, spinner styles
    └── animations.css ← keyframes, transitions
```

### Mở UI
Mở trực tiếp `ssc_ui/index.html` trong browser hoặc serve qua HTTP server:
```bash
cd ssc_ui
python -m http.server 3000
```

---

## Infrastructure (Docker)

### Network chung
Tất cả container thuộc cùng một Docker network:
```bash
docker network create smart-checkout-networks
```

### Khởi động từng service

```bash
# 1. MongoDB + Mongo Express (Admin UI :8281)
docker compose -f docker/docker_compose/mongo-docker-compose.yml up -d

# 2. MinIO Object Storage (API :9200, Console :9201)
docker compose -f docker/docker_compose/minio-docker-compose.yml up -d

# 3. Qdrant Vector DB (REST :6433, gRPC :6434)
docker compose -f docker/docker_compose/qdrant-docker-compose.yml up -d

# 4. Kafka + Kafka UI (Broker :9192, UI :8180)
docker compose -f docker/docker_compose/kafka-docker-compose.yml up -d

# 5. Inference API (Port :8800)
docker compose -f docker/docker_compose/docker-compose.api.yml up -d

# 6. Spark Master (:8980) + Worker (:8982)
docker compose -f docker/docker_compose/spark-docker-compose.yml up -d
```

### Port map

| Service | Container Port | Host Port | Mô tả |
|---|---|---|---|
| MongoDB | 27017 | **27917** | Database |
| Mongo Express | 8081 | **8281** | Admin UI |
| MinIO API | 9000 | **9200** | Object Storage API |
| MinIO Console | 9001 | **9201** | Admin Console |
| Qdrant REST | 6333 | **6433** | Vector DB REST |
| Qdrant gRPC | 6334 | **6434** | Vector DB gRPC |
| Kafka | 9092 | **9192** | Message Broker |
| Kafka UI | 8080 | **8180** | Admin UI |
| Inference API | 8800 | **8800** | AI Model API |
| Spark Master UI | 8080 | **8980** | Spark Dashboard |
| Spark Worker UI | 8081 | **8982** | Worker Dashboard |

### Docker Images

**`Dockerfile.spark`** — Apache Spark + Python deps
```dockerfile
FROM apache/spark:latest
COPY requirements.txt /opt/requirements.txt
RUN pip install -r /opt/requirements.txt
```

**`Dockerfile.api`** — Inference API (Python 3.10)
```dockerfile
FROM python:3.10-slim
RUN apt-get install -y libgl1 libglib2.0-0 curl
COPY requirements_api.txt .
RUN pip install -r requirements_api.txt
COPY processing/utils/ /app/utils/
COPY processing/api_server.py /app/
CMD ["uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "8800"]
```

---

## Pipeline Orchestrator (`main.py`)

Entry point duy nhất để chạy toàn bộ hoặc từng giai đoạn:

```bash
# Chạy toàn bộ pipeline
spark-submit main.py --stage all

# Chỉ chạy preprocessing
spark-submit main.py --stage preprocessing

# Chỉ chạy processing
spark-submit main.py --stage processing
```

**Cơ chế hoạt động:**
- Import muộn (`data_preprocessing`, `data_processing`) để tránh load thư viện dư thừa
- Logging song song ra console + file (`logs/smart_checkout_pipeline.log`)
- `sys.exit(1)` nếu một stage thất bại, ngăn stage tiếp theo chạy

---

## Biến môi trường (`.env`)

```ini
# MongoDB
MONGO_URI=mongodb://root:rootpass@localhost:27917/
MONGO_DB=smart_checkout

# MinIO
MINIO_ENDPOINT=localhost:9200
MINIO_ACCESS=admin
MINIO_SECRET=adminpass

# Google Drive (Collection)
PRODUCTS_FOLDER_ID=<folder_id>
IMAGES_FOLDER_ID=<folder_id>
```

> **Lưu ý bảo mật**: File `.env` đã được thêm vào `.gitignore`. Không commit credentials lên repository.

---

## Dependencies

### `requirements.txt` (Spark Pipeline)
```
pymongo, minio, pyspark
google-api-python-client, google-auth-httplib2, google-auth-oauthlib
python-dotenv, requests, Pillow, beautifulsoup4
numpy, opencv-python-headless, qdrant-client
```

### `requirements_api.txt` (Inference API)
```
fastapi==0.110.1, uvicorn==0.29.0
pillow==10.3.0, numpy==1.26.4, opencv-python-headless==4.9.0.80
ultralytics==8.1.47
torch==2.2.2, torchvision==0.17.2
transformers==4.39.3
```

### `ssc_service/requirements.txt`
```
fastapi, uvicorn, python-multipart
pillow, numpy, opencv-python-headless
qdrant-client
```

---

## Dữ liệu trong MongoDB

### Database: `smart_checkout`
| Collection | Nội dung |
|---|---|
| `products` | Raw product data từ Tiki (metadata + minio_image_path) |

### Database: `preprocessing`
| Collection | Nội dung |
|---|---|
| `cleaning` | Metadata sau bước Clean + `minio_clean_path` |
| `integrated` | Metadata sau bước Integrate + `minio_integrate_path` |
| `transformed` | Metadata sau bước Transform + `minio_transform_path` |

### Database: `processing`
| Collection | Nội dung |
|---|---|
| `objects` | Object records: SKU, name, price, bbox, embedding_dim, `minio_object_path` |

---

## Dữ liệu trong MinIO (Bucket: `smart-checkout`)

```
smart-checkout/
├── preprocessing/
│   ├── clean/<product_id>.jpg
│   ├── integrate/<product_id>.jpg
│   └── transform/<product_id>.jpg
└── processing/
    └── objects/<sku>/<sub_id>.jpg
```

---

## Qdrant Collection

**Collection**: `smart_checkout_objects`

| Trường | Kiểu | Mô tả |
|---|---|---|
| `id` | uint64 | `abs(hash(sub_id)) % 2^63` |
| `vector` | float[512] | CLIP embedding (cosine similarity) |
| `payload.sku` | string | SKU sản phẩm |
| `payload.name` | string | Tên sản phẩm |
| `payload.price` | float | Giá |
| `payload.platform` | string | Nguồn (tiki, v.v.) |
| `payload.minio_image_path` | string | Path ảnh gốc |
| `payload.minio_object_path` | string | Path ảnh crop |
| `payload.bbox` | float[] | Bounding box [x1,y1,x2,y2] |
| `payload.confidence` | float | YOLO confidence score |

---

## Hướng dẫn triển khai đầy đủ

```bash
# 1. Clone repository
git clone <repo_url>
cd smart-checkout

# 2. Tạo Docker network
docker network create smart-checkout-networks

# 3. Khởi động storage services
docker compose -f docker/docker_compose/mongo-docker-compose.yml up -d
docker compose -f docker/docker_compose/minio-docker-compose.yml up -d
docker compose -f docker/docker_compose/qdrant-docker-compose.yml up -d

# 4. Thu thập dữ liệu (tùy chọn — nếu chưa có data)
cd collection && python crawler.py

# 5. Khởi động Inference API
docker compose -f docker/docker_compose/docker-compose.api.yml up -d

# 6. Build và khởi động Spark cluster
docker compose -f docker/docker_compose/spark-docker-compose.yml up -d

# 7. Submit Pipeline (preprocessing → processing)
docker exec ssc-spark-master \
  spark-submit \
  --master spark://ssc-spark-master:7077 \
  --packages org.mongodb.spark:mongo-spark-connector_2.13:10.4.0 \
  /app/main.py --stage all

# 8. Khởi động SSC Service
cd ssc_service
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8801 --reload

# 9. Mở UI
cd ssc_ui && python -m http.server 3000
# Truy cập: http://localhost:3000
```

---

## Logs

| File | Nội dung |
|---|---|
| `logs/smart_checkout_pipeline.log` | Log chính từ `main.py` |
| `logs/data_preprocessing.log` | Log bước Preprocessing |
| `logs/processing_pipeline.log` | Log bước Processing |

---

## Tác giả

**VSSCS Team** — Vietnam Supermarket Smart Checkout System
