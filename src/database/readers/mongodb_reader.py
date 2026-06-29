"""MongoDB reader for product catalog metadata.

Reads SKU metadata from the MongoDB ``products`` collection that was populated
by the data-collection pipeline (collection/object_scrape.py).  The reader is
intentionally read-only and stateless beyond the connection pool held by the
underlying ``MongoClient``.

Expected document schema (field names are configurable via ``MongoDBConfig``):

.. code-block:: json

    {
        "product_id": "SKU001",
        "name": "Mì tôm Hảo Hảo",
        "price": 5000,
        "weight_grams": 75.0,
        "category": "Bách Hóa Online - Thực Phẩm"
    }
"""

from __future__ import annotations

from typing import Generator

from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure

from src.core.config import MongoDBConfig
from src.core.data_models import SKUInfo
from src.core.logger import get_logger

logger = get_logger(__name__)


class MongoDBCatalogReader:
    """Read-only accessor for the SKU metadata collection in MongoDB.

    Parameters
    ----------
    config:
        ``MongoDBConfig`` instance carrying connection URI, database / collection
        names and field-name mappings.
    """

    def __init__(self, config: MongoDBConfig) -> None:
        self._config = config
        self._client: MongoClient | None = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open the MongoDB connection.  Safe to call multiple times."""
        if self._client is not None:
            return
        try:
            self._client = MongoClient(
                self._config.uri,
                connectTimeoutMS=self._config.connect_timeout_ms,
                serverSelectionTimeoutMS=self._config.server_selection_timeout_ms,
            )
            # Trigger an immediate connection check.
            self._client.admin.command("ping")
            logger.info(
                "Connected to MongoDB",
                uri=self._config.uri,
                database=self._config.database,
                collection=self._config.collection,
            )
        except (ConnectionFailure, OperationFailure) as exc:
            self._client = None
            logger.error("Failed to connect to MongoDB", uri=self._config.uri, error=str(exc))
            raise

    def disconnect(self) -> None:
        """Close the MongoDB connection."""
        if self._client is not None:
            self._client.close()
            self._client = None
            logger.info("Disconnected from MongoDB")

    def __enter__(self) -> "MongoDBCatalogReader":
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.disconnect()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_collection(self):
        if self._client is None:
            self.connect()
        return self._client[self._config.database][self._config.collection]

    def _doc_to_sku(self, doc: dict) -> SKUInfo | None:
        """Convert a raw MongoDB document to a ``SKUInfo`` dataclass.

        Returns ``None`` when required fields are missing or invalid.
        """
        cfg = self._config
        try:
            sku_id = str(doc[cfg.field_sku_id])
            name = str(doc.get(cfg.field_name, ""))
            price = float(doc.get(cfg.field_price, 0.0))
            weight = float(doc.get(cfg.field_weight, 0.0))
            category = str(doc.get(cfg.field_category, ""))
            return SKUInfo(
                sku_id=sku_id,
                name=name,
                price=price,
                weight_grams=weight,
                category=category,
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning(
                "Skipping malformed MongoDB document",
                doc_id=str(doc.get("_id", "unknown")),
                error=str(exc),
            )
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def count(self) -> int:
        """Return the total number of documents in the products collection."""
        return self._get_collection().count_documents({})

    def list_sku_ids(self) -> list[str]:
        """Return a list of all SKU IDs present in the collection.

        Only documents that contain the configured ``field_sku_id`` are
        included.
        """
        cfg = self._config
        cursor = self._get_collection().find(
            {cfg.field_sku_id: {"$exists": True}},
            {cfg.field_sku_id: 1, "_id": 0},
        )
        ids: list[str] = []
        for doc in cursor:
            raw = doc.get(cfg.field_sku_id)
            if raw is not None:
                ids.append(str(raw))
        logger.info("Listed SKU IDs from MongoDB", count=len(ids))
        return ids

    def get_sku(self, sku_id: str) -> SKUInfo | None:
        """Fetch a single SKU document by its ID.

        Parameters
        ----------
        sku_id:
            The value stored in the ``field_sku_id`` field.

        Returns
        -------
        ``SKUInfo`` on success, ``None`` if the document is not found.
        """
        doc = self._get_collection().find_one({self._config.field_sku_id: sku_id})
        if doc is None:
            logger.warning("SKU not found in MongoDB", sku_id=sku_id)
            return None
        return self._doc_to_sku(doc)

    def list_all_skus(self) -> list[SKUInfo]:
        """Return all valid SKUs from the collection as ``SKUInfo`` objects.

        Documents with missing / invalid required fields are silently skipped
        and logged at WARNING level.
        """
        cursor = self._get_collection().find(
            {self._config.field_sku_id: {"$exists": True}}
        )
        skus: list[SKUInfo] = []
        for doc in cursor:
            sku = self._doc_to_sku(doc)
            if sku is not None:
                skus.append(sku)
        logger.info("Fetched all SKUs from MongoDB", total=len(skus))
        return skus

    def iter_all_skus(self) -> Generator[SKUInfo, None, None]:
        """Yield SKUs one-by-one for memory-efficient iteration over large collections."""
        cursor = self._get_collection().find(
            {self._config.field_sku_id: {"$exists": True}}
        )
        for doc in cursor:
            sku = self._doc_to_sku(doc)
            if sku is not None:
                yield sku

    def list_by_category(self, category: str) -> list[SKUInfo]:
        """Return all SKUs belonging to a specific category (exact match)."""
        cfg = self._config
        cursor = self._get_collection().find({cfg.field_category: category})
        skus: list[SKUInfo] = []
        for doc in cursor:
            sku = self._doc_to_sku(doc)
            if sku is not None:
                skus.append(sku)
        logger.info("Listed SKUs by category", category=category, count=len(skus))
        return skus
