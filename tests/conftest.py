import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

from src.core.config import (
    AppConfig,
    CameraInfo,
    DataConfig,
    DetectionConfig,
    EmbeddingConfig,
    FusionConfig,
    MilvusConfig,
    MultiCameraConfig,
    ScaleConfig,
    ServerConfig,
)


@pytest.fixture
def mock_config():
    """Returns a config optimized for testing (running on CPU, using mock scale)"""
    return AppConfig(
        detection=DetectionConfig(
            model_path="models/yolo11n-seg.pt", confidence_threshold=0.1, iou_threshold=0.45, device="cpu"
        ),
        embedding=EmbeddingConfig(
            model_name_or_path="google/siglip2-base-patch16-224",
            device="cpu",
            embedding_dim=768,
            batch_size=2,
            model_checkpoint_path="models/siglip2_arcface_finetuned",
        ),
        milvus=MilvusConfig(host="localhost", port=19530, collection_name="test_sku_embeddings"),
        fusion=FusionConfig(
            alpha=0.7,
            beta=0.3,
            min_similarity_threshold=0.2,
            confident_similarity_threshold=0.95,
            knapsack_max_boxes_exact=5,
            beam_width=100,
        ),
        scale=ScaleConfig(type="mock", mock_noise_std=1.0),
        camera=MultiCameraConfig(
            match_threshold=0.40,
            weight_embedding=0.60,
            weight_spatial=0.40,
            cameras=[
                CameraInfo(name="top", is_primary=True, device_id=0),
                CameraInfo(name="side_left", is_primary=False, device_id=1, x_scale=0.0, x_offset=0.25),
                CameraInfo(name="side_right", is_primary=False, device_id=2, x_scale=0.0, x_offset=0.75),
            ],
        ),
        data=DataConfig(sku_metadata_path="data/sku_metadata.json", catalog_dir="data/catalog"),
        server=ServerConfig(host="127.0.0.1", port=8080, log_level="debug"),
    )


@pytest.fixture
def sample_image():
    """Generates a dummy image (640x480) with a white box on a black background"""
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    # Draw a solid red square in the middle (simulating a detected item)
    image[100:300, 200:400] = [0, 0, 255]  # BGR Red
    return image


@pytest.fixture
def mock_sku_metadata():
    return [
        {
            "sku_id": "SKU001",
            "name": "Coca Cola 390ml",
            "price": 10000.0,
            "weight_grams": 400.0,
            "category": "beverage",
        },
        {
            "sku_id": "SKU002",
            "name": "Hao Hao Instant Noodles",
            "price": 4500.0,
            "weight_grams": 75.0,
            "category": "food",
        },
        {"sku_id": "SKU003", "name": "Aquafina 500ml", "price": 5000.0, "weight_grams": 520.0, "category": "beverage"},
    ]


@pytest.fixture
def temp_metadata_file(mock_sku_metadata):
    """Creates a temporary sku_metadata.json and yields its path"""
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False, encoding="utf-8") as temp_file:
        json.dump(mock_sku_metadata, temp_file, indent=2)
        temp_path = temp_file.name

    yield Path(temp_path)

    # Cleanup
    try:
        Path(temp_path).unlink()
    except OSError:
        pass
