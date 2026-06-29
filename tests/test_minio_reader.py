"""Unit tests for MinIOImageReader.

All MinIO SDK calls are mocked – no live MinIO required.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from src.core.config import MinIOCatalogConfig
from src.database.readers.minio_reader import MinIOImageReader

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jpeg_bytes(width: int = 32, height: int = 32) -> bytes:
    """Return the binary content of a minimal JPEG image."""
    img = Image.fromarray(np.zeros((height, width, 3), dtype=np.uint8))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def cfg() -> MinIOCatalogConfig:
    return MinIOCatalogConfig(
        endpoint="localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        secure=False,
        bucket="products-images",
    )


# ---------------------------------------------------------------------------
# Tests: connection management
# ---------------------------------------------------------------------------


def test_connect_success(cfg: MinIOCatalogConfig) -> None:
    mock_minio = MagicMock()
    mock_minio.bucket_exists.return_value = True

    with patch("src.database.readers.minio_reader.Minio", return_value=mock_minio):
        reader = MinIOImageReader(cfg)
        reader.connect()
        assert reader._client is not None
        mock_minio.bucket_exists.assert_called_once_with("products-images")


def test_connect_bucket_missing_raises(cfg: MinIOCatalogConfig) -> None:
    mock_minio = MagicMock()
    mock_minio.bucket_exists.return_value = False

    with patch("src.database.readers.minio_reader.Minio", return_value=mock_minio):
        reader = MinIOImageReader(cfg)
        with pytest.raises(RuntimeError, match="does not exist"):
            reader.connect()


def test_context_manager(cfg: MinIOCatalogConfig) -> None:
    mock_minio = MagicMock()
    mock_minio.bucket_exists.return_value = True

    with patch("src.database.readers.minio_reader.Minio", return_value=mock_minio):
        with MinIOImageReader(cfg) as reader:
            assert reader._client is not None
        assert reader._client is None


# ---------------------------------------------------------------------------
# Tests: list_all_sku_ids
# ---------------------------------------------------------------------------


def test_list_all_sku_ids(cfg: MinIOCatalogConfig) -> None:
    mock_minio = MagicMock()
    mock_minio.bucket_exists.return_value = True

    # Each object represents a "directory" prefix returned with delimiter="/"
    prefix_obj_1 = MagicMock()
    prefix_obj_1.object_name = "SKU001/"
    prefix_obj_2 = MagicMock()
    prefix_obj_2.object_name = "SKU002/"
    mock_minio.list_objects.return_value = [prefix_obj_1, prefix_obj_2]

    with patch("src.database.readers.minio_reader.Minio", return_value=mock_minio):
        reader = MinIOImageReader(cfg)
        reader.connect()
        ids = reader.list_all_sku_ids()

    assert ids == ["SKU001", "SKU002"]


def test_list_all_sku_ids_empty(cfg: MinIOCatalogConfig) -> None:
    mock_minio = MagicMock()
    mock_minio.bucket_exists.return_value = True
    mock_minio.list_objects.return_value = []

    with patch("src.database.readers.minio_reader.Minio", return_value=mock_minio):
        reader = MinIOImageReader(cfg)
        reader.connect()
        ids = reader.list_all_sku_ids()

    assert ids == []


# ---------------------------------------------------------------------------
# Tests: list_views
# ---------------------------------------------------------------------------


def test_list_views(cfg: MinIOCatalogConfig) -> None:
    mock_minio = MagicMock()
    mock_minio.bucket_exists.return_value = True

    obj_front = MagicMock()
    obj_front.object_name = "SKU001/front.jpg"
    obj_side = MagicMock()
    obj_side.object_name = "SKU001/side.png"
    obj_metadata = MagicMock()
    obj_metadata.object_name = "SKU001/metadata.json"  # should be excluded
    mock_minio.list_objects.return_value = [obj_front, obj_side, obj_metadata]

    with patch("src.database.readers.minio_reader.Minio", return_value=mock_minio):
        reader = MinIOImageReader(cfg)
        reader.connect()
        views = reader.list_views("SKU001")

    assert set(views) == {"front", "side"}


# ---------------------------------------------------------------------------
# Tests: download_image
# ---------------------------------------------------------------------------


def test_download_image_success(cfg: MinIOCatalogConfig) -> None:
    mock_minio = MagicMock()
    mock_minio.bucket_exists.return_value = True

    jpeg_bytes = _make_jpeg_bytes()
    mock_response = MagicMock()
    mock_response.read.return_value = jpeg_bytes
    mock_minio.get_object.return_value = mock_response

    with patch("src.database.readers.minio_reader.Minio", return_value=mock_minio):
        reader = MinIOImageReader(cfg)
        reader.connect()
        result = reader.download_image("SKU001", "front")

    assert isinstance(result, np.ndarray)
    assert result.ndim == 3
    assert result.shape[2] == 3  # RGB


def test_download_image_not_found_returns_none(cfg: MinIOCatalogConfig) -> None:
    from minio.error import S3Error

    mock_minio = MagicMock()
    mock_minio.bucket_exists.return_value = True

    # All extension attempts raise NoSuchKey
    no_such_key_err = S3Error("NoSuchKey", "key not found", "resource", "req_id", "host_id", MagicMock())
    mock_minio.get_object.side_effect = no_such_key_err

    with patch("src.database.readers.minio_reader.Minio", return_value=mock_minio):
        reader = MinIOImageReader(cfg)
        reader.connect()
        result = reader.download_image("SKU001", "nonexistent_view")

    assert result is None


# ---------------------------------------------------------------------------
# Tests: iter_sku_images
# ---------------------------------------------------------------------------


def test_iter_sku_images_yields_rgb_arrays(cfg: MinIOCatalogConfig) -> None:
    mock_minio = MagicMock()
    mock_minio.bucket_exists.return_value = True

    jpeg_bytes = _make_jpeg_bytes()

    obj = MagicMock()
    obj.object_name = "SKU001/front.jpg"
    mock_minio.list_objects.return_value = [obj]

    mock_response = MagicMock()
    mock_response.read.return_value = jpeg_bytes
    mock_minio.get_object.return_value = mock_response

    with patch("src.database.readers.minio_reader.Minio", return_value=mock_minio):
        reader = MinIOImageReader(cfg)
        reader.connect()
        pairs = list(reader.iter_sku_images("SKU001"))

    assert len(pairs) == 1
    view_name, arr = pairs[0]
    assert view_name == "front"
    assert isinstance(arr, np.ndarray)


def test_iter_sku_images_skips_non_image(cfg: MinIOCatalogConfig) -> None:
    mock_minio = MagicMock()
    mock_minio.bucket_exists.return_value = True

    obj_json = MagicMock()
    obj_json.object_name = "SKU001/metadata.json"
    mock_minio.list_objects.return_value = [obj_json]

    with patch("src.database.readers.minio_reader.Minio", return_value=mock_minio):
        reader = MinIOImageReader(cfg)
        reader.connect()
        pairs = list(reader.iter_sku_images("SKU001"))

    assert pairs == []


# ---------------------------------------------------------------------------
# Tests: static helpers
# ---------------------------------------------------------------------------


def test_is_valid_image() -> None:
    assert MinIOImageReader._is_valid_image("SKU001/front.jpg")
    assert MinIOImageReader._is_valid_image("SKU001/image.PNG")
    assert not MinIOImageReader._is_valid_image("SKU001/metadata.json")
    assert not MinIOImageReader._is_valid_image("SKU001/README.md")


def test_stem() -> None:
    assert MinIOImageReader._stem("SKU001/front.jpg") == "front"
    assert MinIOImageReader._stem("SKU001/side.png") == "side"
    assert MinIOImageReader._stem("no_extension") == "no_extension"
