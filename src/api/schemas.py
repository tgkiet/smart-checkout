from typing import Any

from pydantic import BaseModel, Field


class CheckoutRequest(BaseModel):
    image_base64: str | None = Field(
        default=None, description="Base64 encoded image string (supports JPEG/PNG formats) for single-camera mode."
    )
    images_base64: dict[str, str] | None = Field(
        default=None, description="Dictionary of camera name to base64 encoded image string for multi-camera mode."
    )
    weight_grams: float | None = Field(
        default=None, description="Physical weight of items in grams. If None, reads from MockScale."
    )
    use_scale: bool = Field(
        default=True, description="Whether to use weight fusion logic. If False, ignores physical weight entirely."
    )


class CheckoutItemResponse(BaseModel):
    sku_id: str
    name: str
    price: float
    quantity: int
    confidence: float
    bbox: list[float] | None = None


class CheckoutResponse(BaseModel):
    items: list[CheckoutItemResponse]
    total_price: float
    scale_weight: float
    weight_match: bool
    confidence: float
    processing_time_ms: float


class CatalogIngestRequest(BaseModel):
    sku_id: str | None = Field(
        default=None, description="SKU ID to ingest. If None, scans and ingests all SKUs in directory."
    )
    data_dir: str = Field(default="data/catalog", description="Root directory containing SKU subfolders")


class HealthResponse(BaseModel):
    status: str
    milvus_connected: bool
    models_loaded: bool
    collection_stats: dict[str, Any]


class ProductScanRequest(BaseModel):
    image_base64: str = Field(..., description="Base64 encoded raw image of the product to scan.")
    bg_color: list[int] = Field(
        default=[0, 0, 0], min_length=3, max_length=3, description="BGR background color to apply to the masked crop."
    )


class DetectedProductCrop(BaseModel):
    crop_base64: str = Field(..., description="Base64 encoded cropped image (background removed).")
    bbox: list[float] = Field(..., description="Bounding box [x1, y1, x2, y2].")
    confidence: float = Field(..., description="YOLO detection confidence.")


class ProductScanResponse(BaseModel):
    detected: bool = Field(..., description="Whether any object was detected.")
    crops: list[DetectedProductCrop] = Field(default_factory=list, description="List of cropped detections.")


class ProductRegisterRequest(BaseModel):
    sku_id: str = Field(..., pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", description="Unique SKU ID for the product.")
    name: str = Field(..., min_length=1, max_length=200, description="Name of the product.")
    price: float = Field(..., ge=0, description="Price of the product in VND.")
    weight_grams: float = Field(..., gt=0, description="Weight of the product in grams.")
    category: str = Field(default="uncategorized", max_length=100, description="Category of the product.")
    images_base64: list[str] = Field(
        ..., min_length=1, max_length=20, description="List of base64 encoded images representing product views."
    )
    are_images_pre_cropped: bool = Field(
        default=True, description="If True, encodes images directly. If False, runs YOLOv11-seg to crop them first."
    )


class ProductRegisterResponse(BaseModel):
    status: str = Field(..., description="Success or error status.")
    message: str = Field(..., description="Status explanation.")
    sku_id: str = Field(..., description="Registered SKU ID.")
    num_vectors_registered: int = Field(..., description="Number of embedding vectors registered in Milvus.")
