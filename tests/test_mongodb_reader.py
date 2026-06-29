"""Unit tests for MongoDBCatalogReader.

All MongoDB interactions are mocked – no live MongoDB required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core.config import MongoDBConfig
from src.core.data_models import SKUInfo
from src.database.readers.mongodb_reader import MongoDBCatalogReader

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def cfg() -> MongoDBConfig:
    return MongoDBConfig(
        uri="mongodb://localhost:27017",
        database="smart_checkout",
        collection="products",
        field_sku_id="product_id",
        field_name="name",
        field_price="price",
        field_weight="weight_grams",
        field_category="category",
    )


@pytest.fixture()
def sample_docs() -> list[dict]:
    return [
        {
            "product_id": "SKU001",
            "name": "Mì tôm Hảo Hảo",
            "price": 5000.0,
            "weight_grams": 75.0,
            "category": "Thực Phẩm",
        },
        {
            "product_id": "SKU002",
            "name": "Sữa TH True Milk",
            "price": 30000.0,
            "weight_grams": 500.0,
            "category": "Đồ uống",
        },
    ]


# ---------------------------------------------------------------------------
# Tests: connection management
# ---------------------------------------------------------------------------


def test_connect_success(cfg: MongoDBConfig) -> None:
    """connect() should set self._client and not raise on ping success."""
    mock_client = MagicMock()
    mock_client.admin.command.return_value = {"ok": 1}

    with patch("src.database.readers.mongodb_reader.MongoClient", return_value=mock_client):
        reader = MongoDBCatalogReader(cfg)
        reader.connect()
        assert reader._client is not None
        mock_client.admin.command.assert_called_once_with("ping")


def test_connect_idempotent(cfg: MongoDBConfig) -> None:
    """Calling connect() twice should NOT create two clients."""
    mock_client = MagicMock()
    mock_client.admin.command.return_value = {"ok": 1}

    with patch("src.database.readers.mongodb_reader.MongoClient", return_value=mock_client) as mock_ctor:
        reader = MongoDBCatalogReader(cfg)
        reader.connect()
        reader.connect()
        assert mock_ctor.call_count == 1


def test_disconnect(cfg: MongoDBConfig) -> None:
    """disconnect() should close the client and set it to None."""
    mock_client = MagicMock()
    mock_client.admin.command.return_value = {"ok": 1}

    with patch("src.database.readers.mongodb_reader.MongoClient", return_value=mock_client):
        reader = MongoDBCatalogReader(cfg)
        reader.connect()
        reader.disconnect()
        assert reader._client is None
        mock_client.close.assert_called_once()


def test_context_manager(cfg: MongoDBConfig) -> None:
    """Using MongoDBCatalogReader as a context manager should connect/disconnect."""
    mock_client = MagicMock()
    mock_client.admin.command.return_value = {"ok": 1}

    with patch("src.database.readers.mongodb_reader.MongoClient", return_value=mock_client):
        with MongoDBCatalogReader(cfg) as reader:
            assert reader._client is not None
        assert reader._client is None


# ---------------------------------------------------------------------------
# Tests: list_sku_ids
# ---------------------------------------------------------------------------


def test_list_sku_ids(cfg: MongoDBConfig, sample_docs: list[dict]) -> None:
    mock_client = MagicMock()
    mock_client.admin.command.return_value = {"ok": 1}

    # list_sku_ids uses find() with a projection
    mock_client.__getitem__.return_value.__getitem__.return_value.find.return_value = [
        {"product_id": doc["product_id"]} for doc in sample_docs
    ]

    with patch("src.database.readers.mongodb_reader.MongoClient", return_value=mock_client):
        reader = MongoDBCatalogReader(cfg)
        ids = reader.list_sku_ids()

    assert ids == ["SKU001", "SKU002"]


def test_list_sku_ids_empty(cfg: MongoDBConfig) -> None:
    mock_client = MagicMock()
    mock_client.admin.command.return_value = {"ok": 1}
    mock_client.__getitem__.return_value.__getitem__.return_value.find.return_value = []

    with patch("src.database.readers.mongodb_reader.MongoClient", return_value=mock_client):
        reader = MongoDBCatalogReader(cfg)
        ids = reader.list_sku_ids()

    assert ids == []


# ---------------------------------------------------------------------------
# Tests: get_sku
# ---------------------------------------------------------------------------


def test_get_sku_found(cfg: MongoDBConfig, sample_docs: list[dict]) -> None:
    mock_client = MagicMock()
    mock_client.admin.command.return_value = {"ok": 1}
    mock_client.__getitem__.return_value.__getitem__.return_value.find_one.return_value = sample_docs[0]

    with patch("src.database.readers.mongodb_reader.MongoClient", return_value=mock_client):
        reader = MongoDBCatalogReader(cfg)
        sku = reader.get_sku("SKU001")

    assert isinstance(sku, SKUInfo)
    assert sku.sku_id == "SKU001"
    assert sku.name == "Mì tôm Hảo Hảo"
    assert sku.price == 5000.0
    assert sku.weight_grams == 75.0
    assert sku.category == "Thực Phẩm"


def test_get_sku_not_found(cfg: MongoDBConfig) -> None:
    mock_client = MagicMock()
    mock_client.admin.command.return_value = {"ok": 1}
    mock_client.__getitem__.return_value.__getitem__.return_value.find_one.return_value = None

    with patch("src.database.readers.mongodb_reader.MongoClient", return_value=mock_client):
        reader = MongoDBCatalogReader(cfg)
        sku = reader.get_sku("NONEXISTENT")

    assert sku is None


# ---------------------------------------------------------------------------
# Tests: list_all_skus
# ---------------------------------------------------------------------------


def test_list_all_skus(cfg: MongoDBConfig, sample_docs: list[dict]) -> None:
    mock_client = MagicMock()
    mock_client.admin.command.return_value = {"ok": 1}
    mock_client.__getitem__.return_value.__getitem__.return_value.find.return_value = sample_docs

    with patch("src.database.readers.mongodb_reader.MongoClient", return_value=mock_client):
        reader = MongoDBCatalogReader(cfg)
        skus = reader.list_all_skus()

    assert len(skus) == 2
    assert all(isinstance(s, SKUInfo) for s in skus)


def test_list_all_skus_skips_malformed(cfg: MongoDBConfig, sample_docs: list[dict]) -> None:
    """Malformed documents (missing required field) should be skipped gracefully."""
    # Use a doc that will fail by missing product_id key entirely (KeyError on cfg.field_sku_id)
    really_bad = {"name": "Missing ID field entirely"}
    docs = sample_docs + [really_bad]


    mock_client = MagicMock()
    mock_client.admin.command.return_value = {"ok": 1}
    mock_client.__getitem__.return_value.__getitem__.return_value.find.return_value = docs

    with patch("src.database.readers.mongodb_reader.MongoClient", return_value=mock_client):
        reader = MongoDBCatalogReader(cfg)
        skus = reader.list_all_skus()

    # Only the 2 valid docs should be returned
    assert len(skus) == 2


# ---------------------------------------------------------------------------
# Tests: _doc_to_sku
# ---------------------------------------------------------------------------


def test_doc_to_sku_valid(cfg: MongoDBConfig, sample_docs: list[dict]) -> None:
    reader = MongoDBCatalogReader(cfg)
    sku = reader._doc_to_sku(sample_docs[0])
    assert sku is not None
    assert sku.sku_id == "SKU001"


def test_doc_to_sku_missing_key(cfg: MongoDBConfig) -> None:
    reader = MongoDBCatalogReader(cfg)
    result = reader._doc_to_sku({"name": "No ID", "price": 100})
    assert result is None


def test_doc_to_sku_invalid_price(cfg: MongoDBConfig) -> None:
    reader = MongoDBCatalogReader(cfg)
    result = reader._doc_to_sku(
        {"product_id": "SKU999", "name": "Bad Price", "price": "not-a-number", "weight_grams": 100}
    )
    assert result is None
