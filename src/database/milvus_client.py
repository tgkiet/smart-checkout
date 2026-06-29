import numpy as np
from pymilvus import DataType, MilvusClient

from src.core.config import MilvusConfig
from src.core.data_models import GroupedSearchResult
from src.core.logger import get_logger

logger = get_logger(__name__)


class MilvusProductDB:
    def __init__(self, config: MilvusConfig):
        self.config = config
        self.collection_name = config.collection_name
        self.uri = f"http://{config.host}:{config.port}"

        logger.info("Connecting to Milvus server", uri=self.uri)
        try:
            self.client = MilvusClient(uri=self.uri)
        except Exception as e:
            logger.error("Failed to connect to Milvus", error=str(e))
            raise e

        self._setup_collection()

    def _setup_collection(self) -> None:
        """Creates collection and indexes if they do not exist."""
        try:
            if self.client.has_collection(self.collection_name):
                logger.debug("Milvus collection already exists", name=self.collection_name)
                try:
                    # Load the collection to memory so it can be searched
                    self.client.load_collection(self.collection_name)
                    return
                except Exception as e:
                    logger.error("Milvus collection exists but failed to load", error=str(e))
                    raise

            logger.info("Creating Milvus collection", name=self.collection_name)

            # 1. Define schema
            schema = self.client.create_schema(auto_id=True, enable_dynamic_field=False)
            schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
            schema.add_field(field_name="sku_id", datatype=DataType.VARCHAR, max_length=64)
            schema.add_field(field_name="view_type", datatype=DataType.VARCHAR, max_length=64)
            schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=self.config.vector_dim)

            # 2. Define index params
            index_params = self.client.prepare_index_params()

            # Use appropriate index building parameters based on the index type
            build_params = {}
            if self.config.index_type == "IVF_FLAT":
                build_params = {"nlist": 128}
            elif self.config.index_type == "HNSW":
                build_params = {"M": 16, "efConstruction": 64}

            index_params.add_index(
                field_name="embedding",
                index_type=self.config.index_type,
                metric_type=self.config.metric_type,
                params=build_params,
            )

            # 3. Create collection
            self.client.create_collection(
                collection_name=self.collection_name, schema=schema, index_params=index_params
            )

            # Load collection
            self.client.load_collection(self.collection_name)
            logger.info("Milvus collection created and loaded successfully", name=self.collection_name)

        except Exception as e:
            logger.error("Error setting up Milvus collection", error=str(e))
            raise e

    def insert_sku_vectors(
        self, sku_id: str, embeddings: list[list[float]] | np.ndarray, view_types: list[str]
    ) -> None:
        """
        Inserts multiple vectors for a single SKU.

        Args:
            sku_id: unique identifier for the product
            embeddings: list or numpy array of 768-dim embedding vectors
            view_types: list of view names (e.g. ['front', 'back', 'top']) matching embeddings list length
        """
        if len(embeddings) != len(view_types):
            raise ValueError("Size mismatch: embeddings and view_types must have the same length.")

        data = []
        for emb, view in zip(embeddings, view_types):
            # Ensure embedding is a list of floats
            emb_list = emb.tolist() if isinstance(emb, np.ndarray) else [float(x) for x in emb]
            data.append({"sku_id": sku_id, "view_type": view, "embedding": emb_list})

        logger.info("Inserting SKU vectors into Milvus", sku_id=sku_id, num_vectors=len(data))
        self.client.insert(collection_name=self.collection_name, data=data)
        # Flush to make sure data is written (Milvus client auto-flushes but good practice)
        self.client.flush(self.collection_name)

    def search_and_group(
        self, query_vector: np.ndarray, top_k_raw: int = 8, top_k_grouped: int = 3, min_similarity: float | None = None
    ) -> list[GroupedSearchResult]:
        """
        Searches the Milvus database for similar vectors, then aggregates (groups)
        the raw matches by SKU ID, picking the highest similarity per SKU.
        Filters out matches that fall below the minimum similarity threshold.

        Args:
            query_vector: 768-dim query vector
            top_k_raw: number of raw vector matches to retrieve
            top_k_grouped: number of grouped SKU IDs to return
            min_similarity: optional override for the minimum similarity threshold

        Returns:
            list[GroupedSearchResult]: sorted list of best-matching SKUs
        """
        emb_list = query_vector.tolist() if isinstance(query_vector, np.ndarray) else [float(x) for x in query_vector]

        try:
            search_results = self.client.search(
                collection_name=self.collection_name,
                data=[emb_list],
                limit=top_k_raw,
                output_fields=["sku_id", "view_type"],
                search_params=self.config.search_params,
            )
        except Exception as e:
            logger.warning("Milvus search query failed (possibly empty collection or connection issue)", error=str(e))
            return []

        if not search_results or len(search_results[0]) == 0:
            return []

        # Parse search results
        raw_matches = search_results[0]

        # Group by sku_id and find the maximum similarity score (distance)
        sku_groups = {}
        for match in raw_matches:
            # pymilvus MilvusClient search returns list of dicts with:
            # 'id', 'distance', 'entity' (which contains output_fields)
            entity = match.get("entity", {})
            sku_id = entity.get("sku_id")
            view_type = entity.get("view_type")
            similarity = float(match.get("distance", 0.0))

            if not sku_id:
                continue

            if sku_id not in sku_groups:
                sku_groups[sku_id] = {"best_similarity": similarity, "matched_views": [view_type]}
            else:
                # Update best similarity if the current one is higher
                if similarity > sku_groups[sku_id]["best_similarity"]:
                    sku_groups[sku_id]["best_similarity"] = similarity
                # Record the view type if not already recorded
                if view_type not in sku_groups[sku_id]["matched_views"]:
                    sku_groups[sku_id]["matched_views"].append(view_type)

        # Determine the threshold to filter low similarity results
        threshold = (
            min_similarity if min_similarity is not None else getattr(self.config, "min_similarity_threshold", 0.5)
        )

        # Convert to GroupedSearchResult and sort by best_similarity descending
        # Only retain candidates with similarity score >= threshold
        grouped_results = [
            GroupedSearchResult(
                sku_id=sku_id, best_similarity=info["best_similarity"], matched_views=info["matched_views"]
            )
            for sku_id, info in sku_groups.items()
            if info["best_similarity"] >= threshold
        ]

        grouped_results.sort(key=lambda x: x.best_similarity, reverse=True)

        return grouped_results[:top_k_grouped]

    def delete_sku(self, sku_id: str) -> None:
        """Deletes all vector embeddings associated with a SKU ID."""
        logger.info("Deleting SKU vectors from Milvus", sku_id=sku_id)
        safe_sku_id = sku_id.replace("\\", "\\\\").replace("'", "\\'")
        # Using boolean expression to delete
        self.client.delete(collection_name=self.collection_name, filter=f"sku_id == '{safe_sku_id}'")

    def delete_sku_vectors(self, sku_id: str) -> None:
        """Alias for :meth:`delete_sku`.  Used by :class:`BulkIngestOrchestrator`."""
        self.delete_sku(sku_id)


    def get_collection_stats(self) -> dict:
        """Returns statistics about the collection (e.g. number of rows)."""
        try:
            stats = self.client.describe_collection(collection_name=self.collection_name)
            # Standard stats does not include row count directly, we use query / count:
            count_res = self.client.query(collection_name=self.collection_name, filter="", output_fields=["count(*)"])
            row_count = count_res[0].get("count(*)", 0) if count_res else 0
            return {
                "collection_name": self.collection_name,
                "row_count": row_count,
                "status": stats.get("status", "unknown"),
            }
        except Exception as e:
            logger.error("Error getting collection stats", error=str(e))
            return {"error": str(e)}

    def drop_collection(self) -> None:
        """Drops the entire collection."""
        logger.warning("Dropping Milvus collection", name=self.collection_name)
        if self.client.has_collection(self.collection_name):
            self.client.drop_collection(self.collection_name)
