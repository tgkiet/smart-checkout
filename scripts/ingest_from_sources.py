"""CLI for bulk ingestion from MongoDB (metadata) + MinIO (images).

Usage examples::

    # Ingest all SKUs (full overwrite)
    python -m scripts.ingest_from_sources

    # Ingest specific SKUs only
    python -m scripts.ingest_from_sources --sku-id SKU001 --sku-id SKU002

    # Use a custom config file
    python -m scripts.ingest_from_sources --config config/settings.dev.yaml

    # Dry-run: connect and list SKUs without actually ingesting
    python -m scripts.ingest_from_sources --dry-run
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from src.core.config import load_config
from src.core.logger import get_logger, setup_logger
from src.database.ingest_pipeline import BulkIngestOrchestrator
from src.database.milvus_client import MilvusProductDB
from src.database.readers import MinIOImageReader, MongoDBCatalogReader
from src.database.sku_catalog import SKUCatalog
from src.embedding.siglip_encoder import SigLIPEncoder

console = Console()
app = typer.Typer(help="Bulk ingest product catalog from MongoDB + MinIO into Milvus.")
logger = get_logger(__name__)


@app.command()
def ingest(
    config_path: str = typer.Option(
        "config/settings.yaml",
        "--config",
        "-c",
        help="Path to config YAML file.",
    ),
    sku_ids: Optional[list[str]] = typer.Option(
        None,
        "--sku-id",
        "-s",
        help="SKU IDs to ingest (repeat flag for multiple). Omit to ingest all.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Connect to sources, list SKUs, but do NOT write to Milvus.",
    ),
    no_overwrite: bool = typer.Option(
        False,
        "--no-overwrite",
        help="Skip deleting existing Milvus vectors before inserting (append mode).",
    ),
) -> None:
    """Ingest product catalog data from MongoDB + MinIO into the Milvus vector store.

    By default, this performs a **full overwrite**: existing vectors for each
    processed SKU are deleted and re-inserted from the latest data in
    MongoDB/MinIO.  Use ``--no-overwrite`` to append instead.
    """
    config = load_config(config_path)
    setup_logger(log_level=config.server.log_level)

    console.rule("[bold cyan]Smart Checkout – Bulk Ingest Pipeline[/bold cyan]")
    console.print(f"[dim]Config:[/dim] {Path(config_path).resolve()}")
    console.print(
        f"[dim]MongoDB:[/dim] {config.mongodb.uri}  →  {config.mongodb.database}.{config.mongodb.collection}"
    )
    console.print(
        f"[dim]MinIO:[/dim]   {config.minio_catalog.endpoint}  →  bucket:{config.minio_catalog.bucket}"
    )

    # ------------------------------------------------------------------ #
    # 1. Connect to data sources                                           #
    # ------------------------------------------------------------------ #
    console.print("\n[bold]Connecting to data sources…[/bold]")

    try:
        mongo_reader = MongoDBCatalogReader(config.mongodb)
        mongo_reader.connect()
        console.print("[green]✓[/green] MongoDB connected")
    except Exception as exc:
        console.print(f"[red]✗ MongoDB connection failed:[/red] {exc}")
        raise typer.Exit(code=1)

    try:
        minio_reader = MinIOImageReader(config.minio_catalog)
        minio_reader.connect()
        console.print("[green]✓[/green] MinIO connected")
    except Exception as exc:
        console.print(f"[red]✗ MinIO connection failed:[/red] {exc}")
        raise typer.Exit(code=1)

    # ------------------------------------------------------------------ #
    # 2. Resolve target SKU list                                           #
    # ------------------------------------------------------------------ #
    target_ids = list(sku_ids) if sku_ids else None  # None = all SKUs

    if target_ids is None:
        console.print("\n[bold]Fetching full SKU list from MongoDB…[/bold]")
        try:
            all_ids = mongo_reader.list_sku_ids()
        except Exception as exc:
            console.print(f"[red]Failed to list SKUs:[/red] {exc}")
            raise typer.Exit(code=1)
        console.print(f"[green]Found {len(all_ids)} SKUs in MongoDB[/green]")
        target_ids_display = all_ids
    else:
        target_ids_display = target_ids
        console.print(f"\n[bold]Targeted ingestion:[/bold] {len(target_ids)} SKU(s)")

    if dry_run:
        # Print a preview table and exit
        table = Table(title="Dry-run: SKUs to be ingested")
        table.add_column("#", style="dim", width=6)
        table.add_column("SKU ID", style="cyan")
        for i, sid in enumerate(target_ids_display[:50], 1):
            table.add_row(str(i), sid)
        if len(target_ids_display) > 50:
            table.add_row("…", f"(+{len(target_ids_display) - 50} more)")
        console.print(table)
        console.print("[yellow]Dry-run complete. No data was written.[/yellow]")
        return

    # ------------------------------------------------------------------ #
    # 3. Load models and database                                          #
    # ------------------------------------------------------------------ #
    console.print("\n[bold]Initialising models and database…[/bold]")

    try:
        encoder = SigLIPEncoder(config.embedding)
        console.print("[green]✓[/green] SigLIP encoder loaded")
    except Exception as exc:
        console.print(f"[red]✗ Failed to load SigLIP encoder:[/red] {exc}")
        raise typer.Exit(code=1)

    try:
        milvus = MilvusProductDB(config.milvus)
        console.print("[green]✓[/green] Milvus connected")
    except Exception as exc:
        console.print(f"[red]✗ Failed to connect to Milvus:[/red] {exc}")
        raise typer.Exit(code=1)

    catalog = SKUCatalog(config.data.sku_metadata_path)
    console.print(f"[green]✓[/green] SKU Catalog loaded ({len(catalog.list_all())} existing SKUs)")

    # ------------------------------------------------------------------ #
    # 4. Run bulk ingestion                                                #
    # ------------------------------------------------------------------ #
    overwrite = not no_overwrite
    console.print(
        f"\n[bold]Starting ingestion…[/bold]  "
        f"mode=[{'[yellow]overwrite[/yellow]' if overwrite else '[blue]append[/blue]'}]  "
        f"SKUs={len(target_ids_display)}"
    )

    orchestrator = BulkIngestOrchestrator(
        encoder=encoder,
        milvus=milvus,
        catalog=catalog,
        mongo_reader=mongo_reader,
        minio_reader=minio_reader,
    )

    stats = orchestrator.run(sku_ids=target_ids, overwrite=overwrite)

    # ------------------------------------------------------------------ #
    # 5. Print summary                                                     #
    # ------------------------------------------------------------------ #
    summary = stats.summary()
    table = Table(title="Ingestion Summary")
    table.add_column("Metric", style="bold")
    table.add_column("Value", style="cyan")

    table.add_row("Total SKUs processed", str(summary["total_skus_processed"]))
    table.add_row("Successful", f"[green]{summary['success']}[/green]")
    table.add_row("Skipped (no images)", f"[yellow]{summary['skipped']}[/yellow]")
    table.add_row("Failed (errors)", f"[red]{summary['failed']}[/red]")
    table.add_row("Total vectors ingested", str(summary["total_vectors_ingested"]))
    table.add_row("Duration", f"{summary['duration_seconds']} s")

    console.print()
    console.print(table)

    if stats.failed_skus:
        console.print("\n[bold red]Failed SKUs:[/bold red]")
        for sid, err in list(stats.failed_skus.items())[:20]:
            console.print(f"  [dim]{sid}[/dim]: {err}")

    if stats.failed_skus:
        raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
