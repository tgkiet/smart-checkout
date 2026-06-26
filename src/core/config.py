import os
from pathlib import Path
from typing import Any, Dict

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DetectionConfig(BaseModel):
    model_path: str = "models/yolo11n-seg.pt"
    confidence_threshold: float = 0.15
    iou_threshold: float = 0.60
    device: str = "cpu"


class EmbeddingConfig(BaseModel):
    model_name_or_path: str = "google/siglip2-base-patch16-224"
    device: str = "cpu"
    embedding_dim: int = 768
    batch_size: int = 16
    model_checkpoint_path: str = "models/siglip2_arcface_finetuned"


class MilvusConfig(BaseModel):
    host: str = "localhost"
    port: int = 19530
    collection_name: str = "sku_embeddings"
    vector_dim: int = 768
    index_type: str = "IVF_FLAT"
    metric_type: str = "COSINE"
    search_params: Dict[str, Any] = Field(default_factory=lambda: {"nprobe": 10})
    min_similarity_threshold: float = 0.5


class FusionConfig(BaseModel):
    alpha: float = 0.7
    beta: float = 0.3
    min_similarity_threshold: float = 0.5
    confident_similarity_threshold: float = 0.98
    knapsack_max_boxes_exact: int = 8
    beam_width: int = 1000


class ScaleConfig(BaseModel):
    enabled: bool = True
    type: str = "mock"
    mock_noise_std: float = 5.0
    serial_port: str = "/dev/ttyUSB0"
    baud_rate: int = 9600


class CameraInfo(BaseModel):
    name: str
    is_primary: bool = False
    device_id: int | str = 0
    x_scale: float = 1.0
    x_offset: float = 0.0
    y_scale: float = 1.0
    y_offset: float = 0.0


class MultiCameraConfig(BaseModel):
    match_threshold: float = 0.40
    weight_embedding: float = 0.60
    weight_spatial: float = 0.40
    cameras: list[CameraInfo] = Field(default_factory=list)


class DataConfig(BaseModel):
    sku_metadata_path: str = "data/sku_metadata.json"
    catalog_dir: str = "data/catalog"
    training_dir: str = "data/training"
    test_dir: str = "data/test"


class MongoDBConfig(BaseModel):
    """MongoDB connection settings for the product catalog metadata source."""

    uri: str = "mongodb://localhost:27017"
    database: str = "smart_checkout"
    collection: str = "products"
    # Field-name mapping: MongoDB document field → internal SKUInfo field.
    # Override these if the collection uses different names.
    field_sku_id: str = "product_id"
    field_name: str = "name"
    field_price: str = "price"
    field_weight: str = "weight_grams"
    field_category: str = "category"
    connect_timeout_ms: int = 5000
    server_selection_timeout_ms: int = 5000


class MinIOCatalogConfig(BaseModel):
    """MinIO (S3-compatible) settings for catalog image storage."""

    endpoint: str = "localhost:9000"
    access_key: str = "minioadmin"
    secret_key: str = "minioadmin"
    secure: bool = False
    bucket: str = "products-images"
    # Expected object path pattern inside the bucket:
    #   {sku_id}/{object_name}  e.g. "SKU001/front.jpg"
    # The SKU directory prefix is derived from the sku_id field in MongoDB.
    connect_timeout: int = 10  # seconds


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    admin_api_key: str | None = None


class AppConfig(BaseSettings):
    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    milvus: MilvusConfig = Field(default_factory=MilvusConfig)
    fusion: FusionConfig = Field(default_factory=FusionConfig)
    scale: ScaleConfig = Field(default_factory=ScaleConfig)
    camera: MultiCameraConfig = Field(default_factory=MultiCameraConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    mongodb: MongoDBConfig = Field(default_factory=MongoDBConfig)
    minio_catalog: MinIOCatalogConfig = Field(default_factory=MinIOCatalogConfig)

    model_config = SettingsConfigDict(
        env_nested_delimiter="__", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


def load_yaml(file_path: Path) -> Dict[str, Any]:
    if not file_path.exists():
        return {}
    with open(file_path, "r", encoding="utf-8") as f:
        try:
            return yaml.safe_load(f) or {}
        except Exception:
            return {}


def merge_dicts(dict1: Dict[str, Any], dict2: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict1.copy()
    for key, value in dict2.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(config_path: str | Path | None = None) -> AppConfig:
    if config_path is None:
        config_path = os.getenv("CONFIG_PATH", "config/settings.yaml")

    config_path = Path(config_path)
    settings_dict = load_yaml(config_path)

    profile = os.getenv("CONFIG_PROFILE", "").strip().lower()
    if config_path.name == "settings.yaml" and profile == "dev":
        dev_path = config_path.parent / "settings.dev.yaml"
        if dev_path.exists():
            dev_settings = load_yaml(dev_path)
            settings_dict = merge_dicts(settings_dict, dev_settings)

    return AppConfig(**settings_dict)
