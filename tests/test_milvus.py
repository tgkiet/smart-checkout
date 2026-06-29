from unittest.mock import MagicMock, patch

import numpy as np

from src.core.data_models import SKUInfo
from src.database.milvus_client import MilvusProductDB
from src.database.sku_catalog import SKUCatalog


@patch("src.database.milvus_client.MilvusClient")
def test_milvus_client_setup(mock_client_class, mock_config):
    mock_client_instance = MagicMock()
    mock_client_class.return_value = mock_client_instance
    mock_client_instance.has_collection.return_value = False

    MilvusProductDB(mock_config.milvus)

    mock_client_instance.has_collection.assert_called_once_with("test_sku_embeddings")
    mock_client_instance.create_schema.assert_called_once()
    mock_client_instance.create_collection.assert_called_once()
    mock_client_instance.load_collection.assert_called_once_with("test_sku_embeddings")


@patch("src.database.milvus_client.MilvusClient")
def test_milvus_insert(mock_client_class, mock_config):
    mock_client_instance = MagicMock()
    mock_client_class.return_value = mock_client_instance
    mock_client_instance.has_collection.return_value = True

    db = MilvusProductDB(mock_config.milvus)

    embeddings = np.random.randn(2, 768)
    view_types = ["front", "back"]

    db.insert_sku_vectors("SKU001", embeddings, view_types)

    mock_client_instance.insert.assert_called_once()
    insert_call_args = mock_client_instance.insert.call_args[1]
    assert insert_call_args["collection_name"] == "test_sku_embeddings"
    assert len(insert_call_args["data"]) == 2
    assert insert_call_args["data"][0]["sku_id"] == "SKU001"
    assert insert_call_args["data"][0]["view_type"] == "front"


@patch("src.database.milvus_client.MilvusClient")
def test_milvus_search_and_group(mock_client_class, mock_config):
    mock_client_instance = MagicMock()
    mock_client_class.return_value = mock_client_instance
    mock_client_instance.has_collection.return_value = True

    # Mock search response
    # pymilvus MilvusClient search returns list of lists of dicts
    mock_search_results = [
        [
            {"distance": 0.9, "entity": {"sku_id": "SKU001", "view_type": "front"}},
            {"distance": 0.8, "entity": {"sku_id": "SKU001", "view_type": "back"}},
            {"distance": 0.85, "entity": {"sku_id": "SKU002", "view_type": "front"}},
            {"distance": 0.5, "entity": {"sku_id": "SKU003", "view_type": "top"}},
        ]
    ]
    mock_client_instance.search.return_value = mock_search_results

    db = MilvusProductDB(mock_config.milvus)

    query = np.random.randn(768)
    results = db.search_and_group(query, top_k_raw=10, top_k_grouped=2)

    # Verify search call
    mock_client_instance.search.assert_called_once()

    # Verify group-by and sorting:
    # SKU001: max similarity 0.9 (front, back)
    # SKU002: max similarity 0.85 (front)
    # SKU003: max similarity 0.5 (top) - should be excluded due to top_k_grouped=2
    assert len(results) == 2

    # SKU001 should be first because 0.9 > 0.85
    assert results[0].sku_id == "SKU001"
    assert results[0].best_similarity == 0.9
    assert "front" in results[0].matched_views
    assert "back" in results[0].matched_views

    # SKU002 should be second
    assert results[1].sku_id == "SKU002"
    assert results[1].best_similarity == 0.85


@patch("src.database.milvus_client.MilvusClient")
def test_milvus_search_and_group_filtering(mock_client_class, mock_config):
    mock_client_instance = MagicMock()
    mock_client_class.return_value = mock_client_instance
    mock_client_instance.has_collection.return_value = True

    # Mock search response
    mock_search_results = [
        [
            {"distance": 0.80, "entity": {"sku_id": "SKU001", "view_type": "front"}},
            {"distance": 0.45, "entity": {"sku_id": "SKU002", "view_type": "front"}},
            {"distance": 0.20, "entity": {"sku_id": "SKU003", "view_type": "front"}},
        ]
    ]
    mock_client_instance.search.return_value = mock_search_results

    db = MilvusProductDB(mock_config.milvus)

    query = np.random.randn(768)

    # 1. Test using default threshold (defaults to 0.5 since mock_config.milvus doesn't define it)
    results_default = db.search_and_group(query, top_k_raw=10, top_k_grouped=5)
    # SKU001 (0.80) >= 0.5 -> kept
    # SKU002 (0.45) < 0.5 -> filtered out
    # SKU003 (0.20) < 0.5 -> filtered out
    assert len(results_default) == 1
    assert results_default[0].sku_id == "SKU001"

    # 2. Test overriding threshold to 0.4
    results_override = db.search_and_group(query, top_k_raw=10, top_k_grouped=5, min_similarity=0.4)
    # SKU001 (0.80) >= 0.4 -> kept
    # SKU002 (0.45) >= 0.4 -> kept
    # SKU003 (0.20) < 0.4 -> filtered out
    assert len(results_override) == 2
    assert results_override[0].sku_id == "SKU001"
    assert results_override[1].sku_id == "SKU002"


def test_sku_catalog(temp_metadata_file):
    # Initialize catalog with temp json file
    catalog = SKUCatalog(temp_metadata_file)

    # Test get_sku
    sku = catalog.get_sku("SKU001")
    assert sku is not None
    assert sku.name == "Coca Cola 390ml"
    assert sku.price == 10000.0

    # Test get_weight and get_price helper functions
    assert catalog.get_weight("SKU001") == 400.0
    assert catalog.get_price("SKU002") == 4500.0

    # Non existent
    assert catalog.get_sku("SKU_NONE") is None
    assert catalog.get_weight("SKU_NONE") == 0.0

    # Test search
    search_res = catalog.search_by_name("hao hao")
    assert len(search_res) == 1
    assert search_res[0].sku_id == "SKU002"

    # Test add SKU
    new_sku = SKUInfo("SKU004", "Sprite 390ml", 10000.0, 400.0, "beverage")
    catalog.add_sku(new_sku)

    assert catalog.get_sku("SKU004") is not None

    # Verify it saved (re-init catalog)
    catalog2 = SKUCatalog(temp_metadata_file)
    assert catalog2.get_sku("SKU004") is not None
    assert catalog2.get_sku("SKU004").name == "Sprite 390ml"
