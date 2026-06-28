import io
import logging
import numpy as np
import requests
import base64
from PIL import Image
from utils.segmentation_model import SegmentationModel
from utils.embedding_model import EmbeddingModel


class ObjectProcessor:
    """
    Module xử lý ảnh theo luồng:
      1. Segment  : Dùng YOLOv8x-seg để phát hiện & tạo mask cho từng object
      2. Object   : Crop vùng object ra khỏi ảnh gốc (dùng mask hoặc bbox)
      3. Embedding: Tạo embedding vector từ ảnh object đã crop
      4. Metadata : Gán metadata từ document MongoDB gốc cho mỗi object
    """

    def __init__(self, config=None):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.config = config or {}

        # Khởi tạo MinIO Client để tải ảnh nếu cần
        minio_endpoint = self.config.get("minio_endpoint", "ssc-minio:9000")
        minio_ak = self.config.get("minio_access_key", "admin")
        minio_sk = self.config.get("minio_secret_key", "adminpass")
        try:
            from minio import Minio
            self.minio_client = Minio(
                minio_endpoint,
                access_key=minio_ak,
                secret_key=minio_sk,
                secure=False
            )
        except Exception as e:
            self.logger.warning(f"Không thể khởi tạo MinIO client: {e}")
            self.minio_client = None

        # Lựa chọn chế độ chạy: API (không cần tải model lên RAM) hoặc Local (tải model trực tiếp)
        self.use_api = self.config.get("use_api", False)
        self.api_endpoint = self.config.get("api_endpoint", "http://localhost:8800/api/v1")

        if self.use_api:
            self.logger.info(f"Khởi tạo ObjectProcessor với API mode: {self.api_endpoint}")
            # Bỏ qua việc khởi tạo mô hình nặng
            self.seg_model = None
            self.emb_model = None
        else:
            self.logger.info("Khởi tạo ObjectProcessor với Local model mode")
            seg_model = self.config.get("seg_model_path", "yolo11n-seg.pt")
            emb_model = self.config.get("emb_model_name", "openai/clip-vit-base-patch32")

            # Khởi tạo 1 lần duy nhất trên mỗi partition/worker
            self.seg_model = SegmentationModel(model_path=seg_model)
            self.emb_model = EmbeddingModel(model_name=emb_model)

    # ------------------------------------------------------------------
    # STEP 1 + 2: Segment & Crop object
    # ------------------------------------------------------n------------
    def segment_and_crop(self, image: Image.Image, conf_threshold=0.5, image_bytes: bytes=None):
        """
        Chạy segmentation, cắt từng object ra khỏi ảnh.
        Trả về list dict:
          - bbox          : [x1, y1, x2, y2]
          - confidence    : float
          - class_id      : int
          - cropped_image : PIL.Image (ảnh object đã crop)
          - mask_applied  : bool  (True nếu dùng mask, False nếu dùng bbox)
        """
        img_np = np.array(image)
        objects = []

        if self.use_api:
            # GỌI API INFERENCE
            if not image_bytes:
                buf = io.BytesIO()
                image.save(buf, format="JPEG")
                image_bytes = buf.getvalue()
                
            try:
                response = requests.post(
                    f"{self.api_endpoint}/segment",
                    files={"image": ("image.jpg", image_bytes, "image/jpeg")},
                    data={"conf_threshold": conf_threshold},
                    timeout=180
                )
                response.raise_for_status()
                # Giả định API trả về {"detections": [{"bbox": [..], "confidence": 0.9, "class_id": 0, "mask_base64": "..."}]}
                detections = response.json().get("detections", [])
                
                # Giải mã mask_base64 thành numpy array
                for det in detections:
                    if "mask_base64" in det:
                        try:
                            mask_bytes = base64.b64decode(det["mask_base64"])
                            mask_pil = Image.open(io.BytesIO(mask_bytes))
                            det["mask"] = np.array(mask_pil) // 255
                        except Exception as e:
                            self.logger.warning(f"Lỗi giải mã mask_base64: {e}")
                            det["mask"] = None
            except Exception as e:
                self.logger.error(f"Lỗi khi gọi API Segmentation: {e}")
                detections = []
        else:
            # GỌI LOCAL MODEL
            detections = self.seg_model.predict(image, conf_threshold=conf_threshold)

        for det in detections:
            x1, y1, x2, y2 = [int(v) for v in det["bbox"]]
            mask = det.get("mask")

            if mask is not None:
                # Resize mask về kích thước ảnh gốc
                h, w = img_np.shape[:2]
                if mask.shape != (h, w):
                    from PIL import Image as _PIL
                    mask_pil = _PIL.fromarray((mask * 255).astype(np.uint8)).resize(
                        (w, h), resample=_PIL.NEAREST
                    )
                    mask = np.array(mask_pil) // 255

                # Áp mask: vùng ngoài object → trắng
                masked_img = img_np.copy()
                masked_img[mask == 0] = 255
                cropped = Image.fromarray(masked_img[y1:y2, x1:x2])
                mask_applied = True
            else:
                # Fallback: crop theo bbox
                cropped = image.crop((x1, y1, x2, y2))
                mask_applied = False

            objects.append({
                "bbox": det["bbox"],
                "confidence": det["confidence"],
                "class_id": det["class_id"],
                "cropped_image": cropped,
                "mask_applied": mask_applied,
            })

        # Lọc giữ lại DUY NHẤT 1 object có confidence cao nhất (vì mỗi ảnh chỉ chứa 1 sản phẩm chính)
        if objects:
            objects = sorted(objects, key=lambda x: x["confidence"], reverse=True)[:1]

        return objects

    # ------------------------------------------------------------------
    # STEP 3: Embedding
    # ------------------------------------------------------------------
    def create_embedding(self, cropped_image: Image.Image) -> list:
        """Tạo embedding vector từ ảnh object đã crop."""
        if self.use_api:
            try:
                buf = io.BytesIO()
                cropped_image.save(buf, format="JPEG")
                img_bytes = buf.getvalue()
                response = requests.post(
                    f"{self.api_endpoint}/embed",
                    files={"image": ("crop.jpg", img_bytes, "image/jpeg")},
                    timeout=120
                )
                response.raise_for_status()
                return response.json().get("embedding", [])
            except Exception as e:
                self.logger.error(f"Lỗi khi gọi API Embedding: {e}")
                return []
        else:
            return self.emb_model.embed(cropped_image)

    # ------------------------------------------------------------------
    # STEP 4: Assign metadata + build output record
    # ------------------------------------------------------------------
    def build_object_record(self, original_row: dict, idx: int, obj: dict, embedding: list):
        """
        Gán metadata từ document gốc cho từng object đã crop + embedding.
        Trả về dict chứa đủ thông tin để:
          - Upload ảnh crop lên MinIO
          - Lưu metadata vào MongoDB
          - Đẩy vector vào Qdrant
        """
        original_id = str(original_row.get("_id") or original_row.get("product_id", "unknown"))
        sku = str(original_row.get("sku") or original_row.get("product_id", "unknown_sku"))

        # Chuyển ảnh crop sang bytes để truyền qua Spark RDD / lưu MinIO
        buf = io.BytesIO()
        obj["cropped_image"].save(buf, format="JPEG", quality=90)
        cropped_bytes = buf.getvalue()

        return {
            # --- Định danh ---
            "original_id": original_id,
            "sub_id": f"{original_id}_obj{idx}",

            # --- Metadata gán nhãn từ document gốc (MongoDB) ---
            "sku": sku,
            "name": str(original_row.get("name") or original_row.get("title", "")),
            "price": original_row.get("price"),
            "platform": original_row.get("platform", ""),
            "minio_image_path": original_row.get("minio_image_path", ""),  # đường dẫn ảnh gốc

            # --- Kết quả segmentation ---
            "bbox": obj["bbox"],
            "confidence": obj["confidence"],
            "class_id": obj["class_id"],
            "mask_applied": obj["mask_applied"],

            # --- Embedding vector (đẩy lên Qdrant) ---
            "embedding": embedding,
            "embedding_dim": len(embedding),

            # --- Dữ liệu ảnh (tạm, sẽ bóc tách trước khi lưu Mongo) ---
            "cropped_image_data": cropped_bytes,
        }

    # ------------------------------------------------------------------
    # MAIN: Xử lý 1 dòng (1 ảnh) → nhiều object records
    # ------------------------------------------------------------------
    def process(self, row_dict: dict, conf_threshold=0.5) -> list:
        """
        Luồng đầy đủ:
          img → segment → object crop → embedding → assign metadata
        Trả về danh sách object records (mỗi record là 1 sản phẩm được cắt ra).
        """
        import concurrent.futures

        image_bytes = row_dict.get("image_data")
        minio_path = row_dict.get("minio_transform_path") or row_dict.get("minio_image_path")
        original_id = str(row_dict.get("_id") or row_dict.get("product_id", "unknown"))

        # 1. Tải ảnh từ MinIO nếu trong MongoDB đã drop `image_data`
        if not image_bytes and minio_path and self.minio_client:
            try:
                # minio_path thường có dạng "s3://bucket_name/path/to/object.jpg"
                path = minio_path.replace("s3://", "")
                parts = path.split("/", 1)
                if len(parts) == 2:
                    bucket_name, object_name = parts
                    response = self.minio_client.get_object(bucket_name, object_name)
                    image_bytes = response.read()
                    response.close()
                    response.release_conn()
            except Exception as e:
                self.logger.error(f"Lỗi tải ảnh từ MinIO ({minio_path}): {e}")

        if not image_bytes:
            self.logger.warning(
                f"Bỏ qua record {original_id}: không có image_data và không thể tải từ MinIO."
            )
            return []

        try:
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        except Exception as e:
            self.logger.error(f"Không thể mở ảnh {original_id}: {e}")
            return []

        # Bước 1 + 2: Segment & Crop
        objects = self.segment_and_crop(image, conf_threshold=conf_threshold, image_bytes=image_bytes)
        if not objects:
            self.logger.debug(f"Không phát hiện object nào trong {original_id}")
            return []

        records = []
        
        def process_single_object(idx, obj):
            # Bước 3: Embedding
            embedding = self.create_embedding(obj["cropped_image"])
            # Bước 4: Assign metadata
            return self.build_object_record(row_dict, idx, obj, embedding)

        # Chạy ThreadPool để lấy Embedding song song cho các objects trong cùng 1 ảnh
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(process_single_object, idx, obj) for idx, obj in enumerate(objects)]
            for future in concurrent.futures.as_completed(futures):
                try:
                    records.append(future.result())
                except Exception as e:
                    self.logger.error(f"Lỗi xử lý 1 object trong {original_id}: {e}")

        return records
