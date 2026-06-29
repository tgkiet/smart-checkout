"""MinIO image reader for catalog product images.

Downloads images from a MinIO (S3-compatible) bucket whose layout is:

    ``{bucket}/{sku_id}/{view_name}.{ext}``

For example::

    products-images/
    ├── SKU001/
    │   ├── front.jpg
    │   └── side.jpg
    └── SKU002/
        └── front.jpg

The bucket name and credentials are supplied via ``MinIOCatalogConfig``.
"""

from __future__ import annotations

import io
from typing import Generator

import numpy as np
from minio import Minio
from minio.error import S3Error
from PIL import Image

from src.core.config import MinIOCatalogConfig
from src.core.logger import get_logger

logger = get_logger(__name__)

# Image file extensions considered valid for ingestion
_VALID_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
)


class MinIOImageReader:
    """Read-only image accessor for the MinIO catalog bucket.

    The client is lazily initialised on first use.  Call :meth:`connect` to
    verify credentials up-front.

    Parameters
    ----------
    config:
        ``MinIOCatalogConfig`` instance with endpoint and credential settings.
    """

    def __init__(self, config: MinIOCatalogConfig) -> None:
        self._config = config
        self._client: Minio | None = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Instantiate the MinIO client and verify bucket accessibility."""
        if self._client is not None:
            return
        self._client = Minio(
            self._config.endpoint,
            access_key=self._config.access_key,
            secret_key=self._config.secret_key,
            secure=self._config.secure,
        )
        bucket = self._config.bucket
        try:
            found = self._client.bucket_exists(bucket)
            if not found:
                logger.error("MinIO bucket does not exist", bucket=bucket)
                raise RuntimeError(f"MinIO bucket '{bucket}' does not exist.")
            logger.info(
                "Connected to MinIO catalog bucket",
                endpoint=self._config.endpoint,
                bucket=bucket,
            )
        except S3Error as exc:
            self._client = None
            logger.error("Failed to verify MinIO bucket", bucket=bucket, error=str(exc))
            raise

    def disconnect(self) -> None:
        """Release the MinIO client (no-op; MinIO SDK has no explicit close)."""
        self._client = None
        logger.info("Disconnected from MinIO")

    def __enter__(self) -> "MinIOImageReader":
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.disconnect()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> Minio:
        if self._client is None:
            self.connect()
        return self._client

    @staticmethod
    def _is_valid_image(object_name: str) -> bool:
        """Return True when the object has a supported image extension."""
        suffix = object_name.rsplit(".", 1)[-1] if "." in object_name else ""
        return f".{suffix.lower()}" in _VALID_EXTENSIONS

    @staticmethod
    def _stem(object_name: str) -> str:
        """Return the filename stem (no directory prefix, no extension).

        E.g. ``SKU001/front.jpg`` → ``front``.
        """
        base = object_name.split("/")[-1]
        return base.rsplit(".", 1)[0] if "." in base else base

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_all_sku_ids(self) -> list[str]:
        """Return all unique SKU IDs (i.e. top-level "directories") in the bucket.

        Uses MinIO's object listing with ``/`` as the delimiter so that only
        the common prefixes (pseudo-folders) are returned rather than every
        individual object.
        """
        client = self._get_client()
        sku_ids: list[str] = []
        try:
            objects = client.list_objects(self._config.bucket, delimiter="/")
            for obj in objects:
                prefix = obj.object_name  # e.g. "SKU001/"
                if prefix:
                    sku_ids.append(prefix.rstrip("/"))
        except S3Error as exc:
            logger.error("Failed to list SKU prefixes from MinIO", error=str(exc))
            raise
        logger.info("Listed SKU IDs from MinIO bucket", count=len(sku_ids))
        return sku_ids

    def list_views(self, sku_id: str) -> list[str]:
        """Return the view names (stems) of all images for a given SKU.

        Parameters
        ----------
        sku_id:
            The SKU identifier, used as a MinIO prefix (``{sku_id}/``).

        Returns
        -------
        List of view name strings (e.g. ``["front", "side", "top"]``).
        """
        client = self._get_client()
        prefix = f"{sku_id}/"
        views: list[str] = []
        try:
            objects = client.list_objects(self._config.bucket, prefix=prefix)
            for obj in objects:
                name = obj.object_name  # e.g. "SKU001/front.jpg"
                if self._is_valid_image(name):
                    views.append(self._stem(name))
        except S3Error as exc:
            logger.warning(
                "Failed to list views for SKU in MinIO",
                sku_id=sku_id,
                error=str(exc),
            )
        return views

    def download_image(self, sku_id: str, view_name: str) -> np.ndarray | None:
        """Download a single catalog image and return it as an RGB ``np.ndarray``.

        Attempts common extensions in order: ``.jpg``, ``.jpeg``, ``.png``,
        ``.webp``, ``.bmp``.  Returns ``None`` when no matching object is found.

        Parameters
        ----------
        sku_id:
            The SKU identifier (directory prefix in MinIO).
        view_name:
            The image stem (e.g. ``"front"``).

        Returns
        -------
        ``np.ndarray`` with shape ``(H, W, 3)`` in RGB order, or ``None``.
        """
        client = self._get_client()
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
            object_name = f"{sku_id}/{view_name}{ext}"
            try:
                response = client.get_object(self._config.bucket, object_name)
                data = response.read()
                response.close()
                response.release_conn()
                image = Image.open(io.BytesIO(data)).convert("RGB")
                return np.array(image)
            except S3Error as exc:
                if exc.code == "NoSuchKey":
                    continue  # Try next extension
                logger.warning(
                    "Failed to download image from MinIO",
                    object_name=object_name,
                    error=str(exc),
                )
                return None
        logger.warning(
            "No image found for SKU view in MinIO",
            sku_id=sku_id,
            view_name=view_name,
        )
        return None

    def iter_sku_images(
        self, sku_id: str
    ) -> Generator[tuple[str, np.ndarray], None, None]:
        """Yield ``(view_name, rgb_array)`` pairs for all images of a SKU.

        Skips objects that cannot be decoded as images.

        Parameters
        ----------
        sku_id:
            The SKU identifier used as the MinIO directory prefix.
        """
        client = self._get_client()
        prefix = f"{sku_id}/"
        try:
            objects = list(client.list_objects(self._config.bucket, prefix=prefix))
        except S3Error as exc:
            logger.warning(
                "Failed to list objects for SKU", sku_id=sku_id, error=str(exc)
            )
            return

        for obj in objects:
            object_name = obj.object_name
            if not self._is_valid_image(object_name):
                continue
            view_name = self._stem(object_name)
            try:
                response = client.get_object(self._config.bucket, object_name)
                data = response.read()
                response.close()
                response.release_conn()
                image = Image.open(io.BytesIO(data)).convert("RGB")
                yield view_name, np.array(image)
            except Exception as exc:
                logger.warning(
                    "Failed to download or decode image",
                    object_name=object_name,
                    error=str(exc),
                )
