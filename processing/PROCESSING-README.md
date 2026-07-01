# Data Processing Pipeline (AI & Vector DB)

**File thực thi chính:** `data_processing.py`
**Mục tiêu:** Trích xuất các đối tượng (objects) độc lập từ bức ảnh lớn, chuyển đổi hình ảnh thành dữ liệu vector (embedding) và đồng bộ lên Cơ sở dữ liệu Vector để phục vụ cho thao tác tính toán tương đồng (Similarity Search) ở thời gian thực lúc Checkout.

### Logic Hoạt Động (Các Bước)
Pipeline này tận dụng `mapPartitions` của Spark kết hợp với đa luồng (`ThreadPoolExecutor`) để xử lý các tác vụ AI phân tán mà không làm sập API Inference.

1. **Đọc Dữ Liệu:** 
   - Bắt đầu với nguồn dữ liệu chất lượng cao từ collection `preprocessing.transformed`.
2. **Segmentation (Phân vùng Object):** 
   - Đưa bức ảnh qua mô hình **YOLOv8-seg** (thường host tại một Inference API server độc lập nhằm hỗ trợ tăng tốc GPU tốt hơn). Mô hình phát hiện tất cả các sản phẩm và trả về một bộ Bounding Box cùng Polygon Mask tương ứng.
3. **Object Crop (Cắt Sản Phẩm):** 
   - Bóc tách bức ảnh lớn thành nhiều ảnh con (sub-objects). Hệ thống sẽ ưu tiên sử dụng Mask (che mờ nền) để cắt chuẩn xác đối tượng. Nếu không có mask, nó sẽ dự phòng (fallback) dùng Bounding Box.
4. **Embedding (Mã Hóa Vector):** 
   - Từng ảnh sản phẩm vừa cắt sẽ được đưa qua mạng thần kinh (như CLIP hoặc ResNet50) để số hóa thành một mảng vector đặc trưng nhiều chiều (ví dụ: 512 chiều).
5. **Assign Metadata (Gắn Dữ Liệu):** 
   - Map ngược lại siêu dữ liệu của bức ảnh mẹ (SKU, Name, Price, Platform) xuống từng object con.
6. **Lưu trữ & Indexing (Đồng bộ kho):** 
   - **Upload MinIO:** Cắt bỏ hoàn toàn data nhị phân nặng ra khỏi Spark RAM bằng cách upload ảnh cắt lên MinIO tại `processing/objects/<sku>/<sub_id>.jpg`.
   - **Lưu MongoDB:** Lưu log toàn bộ lịch sử metadata (bao gồm path ảnh mẹ, path ảnh con, bounding box, thông tin SP) vào `processing.objects`.
   - **Push to Qdrant (Vector DB):** Tiến hành batch-upsert các Embedding Vector cùng Metadata tương ứng (payload) lên Collection `smart_checkout_objects` ở Qdrant. Id của mỗi điểm ảnh (PointStruct) được hash từ sub_id để đảm bảo tính duy nhất.

### Sơ Đồ Luồng Hoạt Động (Flowchart)

```mermaid
graph TD
    START((Từ Preprocessing <br> collection: transformed)) --> R[1. Đọc Dữ Liệu]
    
    subgraph Spark mapPartitions (Xử lý Đa Luồng Parallel)
        R --> S[2. Segmentation <br> YOLOv8-seg API]
        S --> C[3. Object Crop <br> Tách nền bằng Mask/Bbox]
        C --> E[4. Embedding Vector <br> Sinh mã đặc trưng CLIP/ResNet]
        E --> M[5. Assign Metadata <br> Kế thừa SKU, Price từ ảnh mẹ]
    end

    subgraph Hệ thống Lưu Trữ Cuối
        MINIO[MinIO <br> path: processing/objects/]
        MONGO[(MongoDB <br> coll: processing.objects)]
        QDRANT[(Qdrant Vector DB <br> coll: smart_checkout_objects)]
    end

    M -->|6a. Upload Ảnh Cắt (Crop)| MINIO
    M -->|6b. Lưu Thông tin Lịch sử| MONGO
    M -->|6c. Batch Push Vector & Payload| QDRANT
```
