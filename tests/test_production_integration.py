import numpy as np
import pytest
from fastapi.testclient import TestClient

from src.api.server import app
from src.core.config import load_config
from src.database.milvus_client import MilvusProductDB
from src.embedding.siglip_encoder import SigLIPEncoder
from src.pipeline.checkout_pipeline import CheckoutPipeline


@pytest.fixture
def real_config():
    """Loads the real configuration settings."""
    config = load_config()
    # Force use CPU for testing if needed or just use default (which falls back automatically)
    return config


def test_real_siglip_encoder(real_config):
    """Verifies that the actual SigLIP encoder loads and extracts L2-normalized embeddings."""
    encoder = SigLIPEncoder(real_config.embedding)

    # Create a dummy BGR image (400x400x3)
    dummy_image = np.zeros((400, 400, 3), dtype=np.uint8)
    # Draw a blue rectangle in the middle
    dummy_image[100:300, 100:300] = [255, 0, 0]

    # Encode image
    embedding = encoder.encode(dummy_image)

    assert embedding is not None
    assert embedding.shape == (768,)
    # Check L2 normalization (norm should be approximately 1.0)
    norm = np.linalg.norm(embedding)
    assert np.allclose(norm, 1.0, atol=1e-3)


def test_real_milvus_db(real_config):
    """Verifies actual Milvus CRUD operations using a test SKU."""
    import time

    db = MilvusProductDB(real_config.milvus)

    test_sku = "SKU_PROD_TEST"
    test_vector = np.random.randn(768).astype(np.float32)
    # L2 normalize the test vector
    test_vector = test_vector / np.linalg.norm(test_vector)

    # 1. Clean up potential leftovers
    db.delete_sku(test_sku)
    db.client.flush(db.collection_name)

    # 2. Insert vector
    db.insert_sku_vectors(test_sku, [test_vector], ["front"])

    # 3. Search and group
    results = db.search_and_group(test_vector, top_k_raw=5, top_k_grouped=2)

    assert len(results) > 0
    assert results[0].sku_id == test_sku
    assert results[0].best_similarity > 0.95
    assert "front" in results[0].matched_views

    # 4. Clean up
    db.delete_sku(test_sku)
    db.client.flush(db.collection_name)
    time.sleep(1.0)

    # Verify deletion
    results_after = db.search_and_group(test_vector, top_k_raw=5, top_k_grouped=2)
    # It shouldn't match SKU_PROD_TEST anymore
    for r in results_after:
        assert r.sku_id != test_sku


def test_real_checkout_pipeline_empty(real_config):
    """Tests the real end-to-end checkout pipeline with an empty frame (no objects)."""
    pipeline = CheckoutPipeline()
    pipeline.warmup()

    # Create an empty black image (no objects to detect)
    empty_frame = np.zeros((480, 640, 3), dtype=np.uint8)

    result = pipeline.process_frame(empty_frame, weight_grams=0.0)

    assert result is not None
    assert len(result.items) == 0
    assert result.total_price == 0.0
    assert result.scale_weight == 0.0
    assert result.weight_match is True


def test_api_real_health():
    """Performs an integration test on the live API health endpoint with real backend loaded."""
    # Using the real startup lifespan instead of mocking pipeline in tests/test_api.py
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "healthy"
        assert data["milvus_connected"] is True
        assert data["models_loaded"] is True
        assert "row_count" in data["collection_stats"]
