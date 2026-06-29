from pathlib import Path

import typer

from src.core.config import load_config
from src.core.logger import get_logger, setup_logger
from src.database.ingest_pipeline import IngestPipeline
from src.database.milvus_client import MilvusProductDB
from src.embedding.siglip_encoder import SigLIPEncoder

app = typer.Typer()
logger = get_logger(__name__)


@app.command()
def ingest(
    config_path: str = typer.Option("config/settings.yaml", "--config", "-c", help="Path to config yaml file"),
    data_dir: str = typer.Option("data/catalog", "--data-dir", "-d", help="Path to catalog data directory"),
    sku_id: str = typer.Option(None, "--sku-id", "-s", help="Specific SKU ID to ingest (optional)"),
):
    """
    CLI Script to scan the catalog directory, generate SigLIP embeddings,
    and ingest them into Milvus.
    """
    config = load_config(config_path)
    setup_logger(log_level=config.server.log_level)

    logger.info("Initializing Ingestion Pipeline...")

    try:
        # Load embedding encoder and database connection
        encoder = SigLIPEncoder(config.embedding)
        milvus = MilvusProductDB(config.milvus)
        pipeline = IngestPipeline(encoder, milvus)

        catalog_path = Path(data_dir)
        if not catalog_path.exists():
            logger.error("Catalog data directory does not exist", path=str(catalog_path))
            raise typer.Exit(code=1)

        if sku_id:
            sku_path = catalog_path / sku_id
            if not sku_path.exists():
                logger.error("SKU directory does not exist", path=str(sku_path))
                raise typer.Exit(code=1)
            logger.info("Ingesting single SKU", sku_id=sku_id)
            count = pipeline.ingest_sku(sku_id, sku_path)
            logger.info("Single SKU Ingestion complete", sku_id=sku_id, vectors=count)
        else:
            logger.info("Ingesting all SKUs in catalog directory", path=str(catalog_path))
            stats = pipeline.ingest_all(catalog_path)
            logger.info("Full Ingestion complete", stats=stats, total=sum(stats.values()))

    except Exception as e:
        logger.error("Failed to execute Ingestion Pipeline CLI", error=str(e))
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
