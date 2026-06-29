from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from src.core.data_models import SKUInfo
from src.core.logger import get_logger
from src.database.milvus_client import MilvusProductDB
from src.database.sku_catalog import SKUCatalog
from src.embedding.siglip_encoder import SigLIPEncoder
from src.utils.image_utils import load_image

logger = get_logger(__name__)


class IngestPipeline:
    def __init__(self, encoder: SigLIPEncoder, milvus: MilvusProductDB):
        self.encoder = encoder
        self.milvus = milvus

    def ingest_sku(self, sku_id: str, image_dir: Path) -> int:
        """
        Scans all image files in image_dir, generates embedding vectors using SigLIP,
        and inserts them into Milvus with their filename as the view_type.

        Args:
            sku_id: unique identifier for the SKU
            image_dir: directory path containing SKU images

        Returns:
            int: number of images successfully ingested
        """
        if not image_dir.exists() or not image_dir.is_dir():
            logger.warning("SKU image directory does not exist", sku_id=sku_id, path=str(image_dir))
            return 0

        # Supported image extensions
        extensions = [".jpg", ".jpeg", ".png", ".webp", ".bmp"]
        image_paths = []
        for ext in extensions:
            image_paths.extend(list(image_dir.glob(f"*{ext}")))
            image_paths.extend(list(image_dir.glob(f"*{ext.upper()}")))

        # De-duplicate paths
        image_paths = list(set(image_paths))

        if not image_paths:
            logger.warning("No images found for SKU", sku_id=sku_id, path=str(image_dir))
            return 0

        images = []
        view_types = []

        for path in image_paths:
            try:
                img = load_image(path)
                images.append(img)
                # Use filename without extension as view type
                view_types.append(path.stem)
            except Exception as e:
                logger.error("Failed to load image for ingestion", path=str(path), error=str(e))

        if not images:
            return 0

        # Generate embeddings in batch
        logger.info("Encoding images for SKU", sku_id=sku_id, count=len(images))
        embeddings = self.encoder.encode_batch(images)

        # Insert into Milvus
        self.milvus.insert_sku_vectors(sku_id, embeddings, view_types)

        logger.info("Successfully ingested SKU", sku_id=sku_id, num_views=len(view_types))
        return len(view_types)

    def ingest_all(self, catalog_dir: Path) -> dict[str, int]:
        """
        Scans catalog_dir, finds SKU subdirectories, and ingests all of them.

        Args:
            catalog_dir: Path to data/catalog

        Returns:
            dict[str, int]: mapping of sku_id to number of ingested vectors
        """
        catalog_path = Path(catalog_dir)
        if not catalog_path.exists():
            logger.error("Catalog directory does not exist", path=str(catalog_path))
            return {}

        stats = {}
        # Iterate over subdirectories
        for subdir in catalog_path.iterdir():
            if not subdir.is_dir():
                continue

            sku_id = subdir.name
            # Skip hidden folders or special files
            if sku_id.startswith("."):
                continue

            logger.info("Processing catalog subdirectory", sku_id=sku_id)
            num_ingested = self.ingest_sku(sku_id, subdir)
            stats[sku_id] = num_ingested

        logger.info("Ingestion pipeline complete", stats=stats, total_vectors=sum(stats.values()))
        return stats


# ---------------------------------------------------------------------------
# Bulk Ingestion from MongoDB + MinIO
# ---------------------------------------------------------------------------


@dataclass
class BulkIngestStats:
    """Aggregated result of a :class:`BulkIngestOrchestrator` run.

    Attributes
    ----------
    total_skus_processed:
        Number of SKUs for which ingestion was attempted.
    total_vectors_ingested:
        Total embedding vectors inserted into Milvus.
    skipped_skus:
        SKUs that had no images in MinIO and were therefore skipped.
    failed_skus:
        Mapping of ``sku_id`` → error message for SKUs that raised an
        unexpected exception.
    duration_seconds:
        Wall-clock time of the entire run.
    """

    total_skus_processed: int = 0
    total_vectors_ingested: int = 0
    skipped_skus: list[str] = field(default_factory=list)
    failed_skus: dict[str, str] = field(default_factory=dict)
    duration_seconds: float = 0.0

    @property
    def success_count(self) -> int:
        """SKUs that produced at least one vector."""
        return self.total_skus_processed - len(self.skipped_skus) - len(self.failed_skus)

    def summary(self) -> dict:
        return {
            "total_skus_processed": self.total_skus_processed,
            "success": self.success_count,
            "skipped": len(self.skipped_skus),
            "failed": len(self.failed_skus),
            "total_vectors_ingested": self.total_vectors_ingested,
            "duration_seconds": round(self.duration_seconds, 2),
        }


class BulkIngestOrchestrator:
    """Orchestrates bulk data ingestion from **MongoDB** (metadata) and
    **MinIO** (images) into the Milvus vector database.

    This is the automated counterpart to :class:`IngestPipeline` (which reads
    from the local file-system).  Use this class when seeding the system from
    the upstream data-collection service.

    High-level flow per SKU:

    1. Read ``SKUInfo`` from MongoDB using :class:`MongoDBCatalogReader`.
    2. Download each image from MinIO using :class:`MinIOImageReader`.
    3. Encode images with :class:`SigLIPEncoder`.
    4. (Optionally) delete existing vectors for the SKU in Milvus.
    5. Insert new embedding vectors into Milvus.
    6. Upsert the SKU metadata into the local :class:`SKUCatalog` JSON file.

    Parameters
    ----------
    encoder:
        Loaded :class:`SigLIPEncoder` instance.
    milvus:
        Connected :class:`MilvusProductDB` instance.
    catalog:
        :class:`SKUCatalog` instance for persisting metadata locally.
    mongo_reader:
        :class:`MongoDBCatalogReader` instance (must be connected).
    minio_reader:
        :class:`MinIOImageReader` instance (must be connected).
    """

    def __init__(
        self,
        encoder: SigLIPEncoder,
        milvus: MilvusProductDB,
        catalog: SKUCatalog,
        mongo_reader,  # MongoDBCatalogReader – imported lazily to avoid hard dep
        minio_reader,  # MinIOImageReader
    ) -> None:
        self.encoder = encoder
        self.milvus = milvus
        self.catalog = catalog
        self.mongo_reader = mongo_reader
        self.minio_reader = minio_reader

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        sku_ids: list[str] | None = None,
        overwrite: bool = True,
    ) -> BulkIngestStats:
        """Execute the bulk ingestion pipeline.

        Parameters
        ----------
        sku_ids:
            Optional explicit list of SKU IDs to ingest.  When ``None``, all
            SKUs found in MongoDB are processed.
        overwrite:
            When ``True``, existing Milvus vectors for each SKU are deleted
            before inserting new ones (full refresh).  When ``False``, the SKU
            is inserted on top of existing data (may cause duplicates).

        Returns
        -------
        :class:`BulkIngestStats` with a full summary of the run.
        """
        start = time.perf_counter()
        stats = BulkIngestStats()

        # Determine which SKUs to process
        if sku_ids is not None:
            target_ids = sku_ids
            logger.info("Starting targeted bulk ingestion", sku_count=len(target_ids))
        else:
            target_ids = self.mongo_reader.list_sku_ids()
            logger.info("Starting full bulk ingestion", sku_count=len(target_ids))

        for sku_id in target_ids:
            stats.total_skus_processed += 1
            try:
                num_vectors = self._ingest_single_sku(sku_id, overwrite=overwrite)
                if num_vectors == 0:
                    stats.skipped_skus.append(sku_id)
                else:
                    stats.total_vectors_ingested += num_vectors
            except Exception as exc:
                stats.failed_skus[sku_id] = str(exc)
                logger.error(
                    "Failed to ingest SKU",
                    sku_id=sku_id,
                    error=str(exc),
                    exc_info=True,
                )

        stats.duration_seconds = time.perf_counter() - start
        logger.info("Bulk ingestion complete", **stats.summary())
        return stats

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ingest_single_sku(self, sku_id: str, *, overwrite: bool) -> int:
        """Ingest a single SKU from MongoDB + MinIO.

        Returns the number of embedding vectors successfully inserted into
        Milvus (0 = no images found / skipped).
        """
        # 1. Fetch metadata from MongoDB
        sku_info: SKUInfo | None = self.mongo_reader.get_sku(sku_id)
        if sku_info is None:
            logger.warning("SKU metadata not found in MongoDB, skipping", sku_id=sku_id)
            return 0

        # 2. Collect images from MinIO
        images = []
        view_types = []
        for view_name, rgb_array in self.minio_reader.iter_sku_images(sku_id):
            images.append(rgb_array)
            view_types.append(view_name)

        if not images:
            logger.warning(
                "No images found in MinIO for SKU, skipping",
                sku_id=sku_id,
                bucket=self.minio_reader._config.bucket,
            )
            return 0

        # 3. Generate SigLIP embeddings
        logger.info("Encoding SKU images", sku_id=sku_id, num_images=len(images))
        embeddings = self.encoder.encode_batch(images)

        # 4. Optionally clear stale vectors
        if overwrite:
            try:
                self.milvus.delete_sku_vectors(sku_id)
                logger.debug("Deleted existing vectors for SKU", sku_id=sku_id)
            except Exception as exc:
                # Log but do not abort – the collection may simply not have this SKU yet
                logger.warning(
                    "Could not delete existing vectors for SKU",
                    sku_id=sku_id,
                    error=str(exc),
                )

        # 5. Insert into Milvus
        self.milvus.insert_sku_vectors(sku_id, embeddings, view_types)

        # 6. Upsert into local SKUCatalog JSON
        self.catalog.add_sku(sku_info)

        logger.info(
            "Ingested SKU successfully",
            sku_id=sku_id,
            num_vectors=len(embeddings),
        )
        return len(embeddings)
