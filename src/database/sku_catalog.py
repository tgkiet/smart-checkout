import json
from pathlib import Path

from src.core.data_models import SKUInfo
from src.core.logger import get_logger

logger = get_logger(__name__)


class SKUCatalog:
    def __init__(self, metadata_path: str | Path):
        self.metadata_path = Path(metadata_path)
        self.skus: dict[str, SKUInfo] = {}
        self.load()

    def load(self) -> None:
        """Loads catalog from metadata JSON file."""
        if not self.metadata_path.exists():
            logger.warning("Metadata JSON file does not exist. Creating empty catalog.", path=str(self.metadata_path))
            # Ensure parent directory exists
            self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
            self.save()
            return

        try:
            with open(self.metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.skus = {}
            for item in data:
                sku = SKUInfo(
                    sku_id=item["sku_id"],
                    name=item["name"],
                    price=float(item["price"]),
                    weight_grams=float(item["weight_grams"]),
                    category=item.get("category", ""),
                )
                self.skus[sku.sku_id] = sku

            logger.info("Loaded SKU catalog metadata", path=str(self.metadata_path), num_skus=len(self.skus))
        except Exception as e:
            logger.error("Failed to load SKU catalog metadata", path=str(self.metadata_path), error=str(e))
            raise e

    def save(self) -> None:
        """Saves current catalog back to metadata JSON file."""
        try:
            data = []
            for sku in self.skus.values():
                data.append(
                    {
                        "sku_id": sku.sku_id,
                        "name": sku.name,
                        "price": sku.price,
                        "weight_grams": sku.weight_grams,
                        "category": sku.category,
                    }
                )

            with open(self.metadata_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            logger.info("Saved SKU catalog metadata", path=str(self.metadata_path), num_skus=len(self.skus))
        except Exception as e:
            logger.error("Failed to save SKU catalog metadata", path=str(self.metadata_path), error=str(e))
            raise e

    def get_sku(self, sku_id: str) -> SKUInfo | None:
        """Returns the SKUInfo for a given SKU ID or None if not found."""
        return self.skus.get(sku_id)

    def get_weight(self, sku_id: str) -> float:
        """Returns the theoretical weight in grams for a SKU, or 0.0 if not found."""
        sku = self.get_sku(sku_id)
        return sku.weight_grams if sku else 0.0

    def get_price(self, sku_id: str) -> float:
        """Returns the unit price for a SKU, or 0.0 if not found."""
        sku = self.get_sku(sku_id)
        return sku.price if sku else 0.0

    def list_all(self) -> list[SKUInfo]:
        """Returns a list of all products in the catalog."""
        return list(self.skus.values())

    def search_by_name(self, query: str) -> list[SKUInfo]:
        """Searches products case-insensitively by name."""
        query_lower = query.lower()
        return [sku for sku in self.skus.values() if query_lower in sku.name.lower()]

    def add_sku(self, sku: SKUInfo) -> None:
        """Adds or updates a SKU in the catalog and persists changes."""
        self.skus[sku.sku_id] = sku
        self.save()

    def delete_sku(self, sku_id: str) -> None:
        """Removes a SKU from the catalog and persists changes."""
        if sku_id in self.skus:
            del self.skus[sku_id]
            self.save()
            logger.info("Deleted SKU from catalog", sku_id=sku_id)
        else:
            logger.warning("Attempted to delete non-existent SKU", sku_id=sku_id)
