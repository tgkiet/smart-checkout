"""Unit tests for BulkIngestOrchestrator and BulkIngestStats.

All external dependencies (MongoDB, MinIO, Milvus, SigLIP) are mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from src.core.data_models import SKUInfo
from src.database.ingest_pipeline import BulkIngestOrchestrator, BulkIngestStats

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_sku(sku_id: str = "SKU001") -> SKUInfo:
    return SKUInfo(
        sku_id=sku_id,
        name=f"Product {sku_id}",
        price=10000.0,
        weight_grams=200.0,
        category="Test",
    )


def _dummy_embedding() -> list[float]:
    return [float(x) for x in np.random.rand(768)]


@pytest.fixture()
def mock_encoder() -> MagicMock:
    encoder = MagicMock()
    # encode_batch returns a list of embeddings matching input length
    encoder.encode_batch.side_effect = lambda images: [_dummy_embedding() for _ in images]
    return encoder


@pytest.fixture()
def mock_milvus() -> MagicMock:
    milvus = MagicMock()
    milvus.delete_sku_vectors.return_value = None
    milvus.insert_sku_vectors.return_value = None
    return milvus


@pytest.fixture()
def mock_catalog() -> MagicMock:
    catalog = MagicMock()
    catalog.add_sku.return_value = None
    return catalog


@pytest.fixture()
def mock_mongo(request) -> MagicMock:
    """Configurable MongoDB reader mock. param = list of sku_ids to return."""
    sku_ids = getattr(request, "param", ["SKU001", "SKU002"])
    mongo = MagicMock()
    mongo.list_sku_ids.return_value = sku_ids
    mongo.get_sku.side_effect = lambda sid: _make_sku(sid)
    return mongo


@pytest.fixture()
def mock_minio() -> MagicMock:
    """MinIO reader that returns 2 images per SKU."""
    minio = MagicMock()
    minio._config = MagicMock()
    minio._config.bucket = "products-images"
    dummy_image = np.zeros((32, 32, 3), dtype=np.uint8)
    minio.iter_sku_images.return_value = [("front", dummy_image), ("side", dummy_image)]
    return minio


def _make_orchestrator(
    encoder=None,
    milvus=None,
    catalog=None,
    mongo=None,
    minio=None,
) -> BulkIngestOrchestrator:
    return BulkIngestOrchestrator(
        encoder=encoder or MagicMock(),
        milvus=milvus or MagicMock(),
        catalog=catalog or MagicMock(),
        mongo_reader=mongo or MagicMock(),
        minio_reader=minio or MagicMock(),
    )


# ---------------------------------------------------------------------------
# Tests: BulkIngestStats
# ---------------------------------------------------------------------------


def test_stats_success_count() -> None:
    stats = BulkIngestStats(
        total_skus_processed=10,
        total_vectors_ingested=20,
        skipped_skus=["S1"],
        failed_skus={"S2": "error"},
    )
    assert stats.success_count == 8


def test_stats_summary_keys() -> None:
    stats = BulkIngestStats()
    summary = stats.summary()
    assert "total_skus_processed" in summary
    assert "success" in summary
    assert "skipped" in summary
    assert "failed" in summary
    assert "total_vectors_ingested" in summary
    assert "duration_seconds" in summary


# ---------------------------------------------------------------------------
# Tests: BulkIngestOrchestrator.run (all SKUs)
# ---------------------------------------------------------------------------


def test_run_all_skus(
    mock_encoder: MagicMock,
    mock_milvus: MagicMock,
    mock_catalog: MagicMock,
    mock_mongo: MagicMock,
    mock_minio: MagicMock,
) -> None:
    """run() with no sku_ids should process all SKUs from mongo.list_sku_ids()."""
    orch = BulkIngestOrchestrator(
        encoder=mock_encoder,
        milvus=mock_milvus,
        catalog=mock_catalog,
        mongo_reader=mock_mongo,
        minio_reader=mock_minio,
    )
    stats = orch.run()

    assert stats.total_skus_processed == 2
    assert stats.total_vectors_ingested == 4  # 2 images × 2 SKUs
    assert stats.skipped_skus == []
    assert stats.failed_skus == {}
    assert stats.success_count == 2


def test_run_targeted_skus(
    mock_encoder: MagicMock,
    mock_milvus: MagicMock,
    mock_catalog: MagicMock,
    mock_mongo: MagicMock,
    mock_minio: MagicMock,
) -> None:
    """run() with explicit sku_ids should only process those SKUs."""
    orch = BulkIngestOrchestrator(
        encoder=mock_encoder,
        milvus=mock_milvus,
        catalog=mock_catalog,
        mongo_reader=mock_mongo,
        minio_reader=mock_minio,
    )
    stats = orch.run(sku_ids=["SKU001"])

    assert stats.total_skus_processed == 1
    assert stats.total_vectors_ingested == 2
    mock_mongo.list_sku_ids.assert_not_called()  # Should NOT hit mongo for the list


# ---------------------------------------------------------------------------
# Tests: overwrite behaviour
# ---------------------------------------------------------------------------


def test_run_overwrite_calls_delete(
    mock_encoder: MagicMock,
    mock_milvus: MagicMock,
    mock_catalog: MagicMock,
    mock_mongo: MagicMock,
    mock_minio: MagicMock,
) -> None:
    orch = BulkIngestOrchestrator(
        encoder=mock_encoder,
        milvus=mock_milvus,
        catalog=mock_catalog,
        mongo_reader=mock_mongo,
        minio_reader=mock_minio,
    )
    orch.run(sku_ids=["SKU001"], overwrite=True)
    mock_milvus.delete_sku_vectors.assert_called_once_with("SKU001")


def test_run_no_overwrite_skips_delete(
    mock_encoder: MagicMock,
    mock_milvus: MagicMock,
    mock_catalog: MagicMock,
    mock_mongo: MagicMock,
    mock_minio: MagicMock,
) -> None:
    orch = BulkIngestOrchestrator(
        encoder=mock_encoder,
        milvus=mock_milvus,
        catalog=mock_catalog,
        mongo_reader=mock_mongo,
        minio_reader=mock_minio,
    )
    orch.run(sku_ids=["SKU001"], overwrite=False)
    mock_milvus.delete_sku_vectors.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: skip / error handling
# ---------------------------------------------------------------------------


def test_skip_when_no_images(
    mock_encoder: MagicMock,
    mock_milvus: MagicMock,
    mock_catalog: MagicMock,
    mock_mongo: MagicMock,
) -> None:
    """SKUs with no MinIO images should be skipped (not counted as failures)."""
    minio_empty = MagicMock()
    minio_empty._config = MagicMock()
    minio_empty._config.bucket = "products-images"
    minio_empty.iter_sku_images.return_value = []  # No images

    orch = BulkIngestOrchestrator(
        encoder=mock_encoder,
        milvus=mock_milvus,
        catalog=mock_catalog,
        mongo_reader=mock_mongo,
        minio_reader=minio_empty,
    )
    stats = orch.run(sku_ids=["SKU001"])

    assert stats.skipped_skus == ["SKU001"]
    assert stats.total_vectors_ingested == 0
    mock_milvus.insert_sku_vectors.assert_not_called()


def test_skip_when_mongo_returns_none(
    mock_encoder: MagicMock,
    mock_milvus: MagicMock,
    mock_catalog: MagicMock,
    mock_minio: MagicMock,
) -> None:
    """SKUs not found in MongoDB should be skipped (not failed)."""
    mongo_no_sku = MagicMock()
    mongo_no_sku.list_sku_ids.return_value = ["GHOST"]
    mongo_no_sku.get_sku.return_value = None  # SKU not in MongoDB

    orch = BulkIngestOrchestrator(
        encoder=mock_encoder,
        milvus=mock_milvus,
        catalog=mock_catalog,
        mongo_reader=mongo_no_sku,
        minio_reader=mock_minio,
    )
    stats = orch.run(sku_ids=["GHOST"])

    assert "GHOST" in stats.skipped_skus
    assert stats.failed_skus == {}


def test_error_isolated_per_sku(
    mock_encoder: MagicMock,
    mock_milvus: MagicMock,
    mock_catalog: MagicMock,
) -> None:
    """An exception in one SKU should be recorded but not abort others."""
    mongo = MagicMock()
    mongo.list_sku_ids.return_value = ["SKU_GOOD", "SKU_BAD"]
    mongo.get_sku.side_effect = lambda sid: (
        _make_sku(sid) if sid == "SKU_GOOD" else (_ for _ in ()).throw(RuntimeError("DB error"))
    )

    dummy_image = np.zeros((32, 32, 3), dtype=np.uint8)
    minio = MagicMock()
    minio._config = MagicMock()
    minio._config.bucket = "products-images"
    minio.iter_sku_images.return_value = [("front", dummy_image)]

    orch = BulkIngestOrchestrator(
        encoder=mock_encoder,
        milvus=mock_milvus,
        catalog=mock_catalog,
        mongo_reader=mongo,
        minio_reader=minio,
    )
    stats = orch.run(sku_ids=["SKU_GOOD", "SKU_BAD"])

    assert stats.total_skus_processed == 2
    assert stats.success_count == 1
    assert "SKU_BAD" in stats.failed_skus


# ---------------------------------------------------------------------------
# Tests: catalog update
# ---------------------------------------------------------------------------


def test_catalog_upserted_after_ingest(
    mock_encoder: MagicMock,
    mock_milvus: MagicMock,
    mock_catalog: MagicMock,
    mock_mongo: MagicMock,
    mock_minio: MagicMock,
) -> None:
    """SKUCatalog.add_sku should be called once per successfully ingested SKU."""
    orch = BulkIngestOrchestrator(
        encoder=mock_encoder,
        milvus=mock_milvus,
        catalog=mock_catalog,
        mongo_reader=mock_mongo,
        minio_reader=mock_minio,
    )
    orch.run(sku_ids=["SKU001"])
    mock_catalog.add_sku.assert_called_once()
    called_sku = mock_catalog.add_sku.call_args[0][0]
    assert called_sku.sku_id == "SKU001"
