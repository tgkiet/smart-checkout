import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from src.core.config import load_config
from src.core.logger import get_logger, setup_logger
from src.database.milvus_client import MilvusProductDB
from src.embedding.siglip_encoder import SigLIPEncoder
from src.utils.image_utils import load_image

app = typer.Typer()
logger = get_logger(__name__)
console = Console()


@app.command()
def evaluate(
    config_path: str = typer.Option("config/settings.yaml", "--config", "-c", help="Path to config yaml file"),
    test_dir: str = typer.Option("data/test", "--test-dir", "-t", help="Path to evaluation test dataset"),
):
    """
    CLI Script to evaluate retrieval accuracy (Top-1, Top-5 recall and latency)
    on test set images.
    """
    config = load_config(config_path)
    setup_logger(log_level=config.server.log_level)

    logger.info("Initializing Evaluation Engine...")

    try:
        # Load components
        encoder = SigLIPEncoder(config.embedding)
        milvus = MilvusProductDB(config.milvus)

        test_path = Path(test_dir)
        if not test_path.exists():
            logger.error("Test data directory does not exist", path=str(test_path))
            raise typer.Exit(code=1)

        # Discover test classes
        classes = sorted([d.name for d in test_path.iterdir() if d.is_dir() and not d.name.startswith(".")])
        if not classes:
            logger.error("No class subdirectories found in test dataset directory", path=str(test_path))
            raise typer.Exit(code=1)

        extensions = [".jpg", ".jpeg", ".png", ".webp", ".bmp"]
        test_samples = []
        for cls_name in classes:
            cls_dir = test_path / cls_name
            for p in cls_dir.iterdir():
                if p.suffix.lower() in extensions:
                    test_samples.append((p, cls_name))

        if not test_samples:
            logger.error("No test images found in class subdirectories.")
            raise typer.Exit(code=1)

        logger.info("Starting test evaluation", total_images=len(test_samples), num_classes=len(classes))

        # Track accuracy
        top1_correct = 0
        top5_correct = 0
        total_queries = 0
        latencies = []

        class_stats = {cls_name: {"total": 0, "top1": 0, "top5": 0} for cls_name in classes}

        for path, ground_truth_sku in test_samples:
            try:
                # Load and encode
                img = load_image(path)

                t_start = time.perf_counter()
                emb = encoder.encode(img)

                # Search database
                results = milvus.search_and_group(query_vector=emb, top_k_raw=15, top_k_grouped=5)

                query_time_ms = (time.perf_counter() - t_start) * 1000
                latencies.append(query_time_ms)

                total_queries += 1
                class_stats[ground_truth_sku]["total"] += 1

                # Verify match
                sku_rankings = [res.sku_id for res in results]

                # Check Top-1
                if sku_rankings and sku_rankings[0] == ground_truth_sku:
                    top1_correct += 1
                    class_stats[ground_truth_sku]["top1"] += 1

                # Check Top-5
                if ground_truth_sku in sku_rankings:
                    top5_correct += 1
                    class_stats[ground_truth_sku]["top5"] += 1

            except Exception as e:
                logger.error("Failed to evaluate test sample", path=str(path), error=str(e))

        # Generate summary tables
        if total_queries == 0:
            logger.error("No queries completed successfully.")
            return

        top1_acc = (top1_correct / total_queries) * 100
        top5_acc = (top5_correct / total_queries) * 100
        avg_latency = sum(latencies) / len(latencies)

        # Display overall metrics
        console.print("\n[bold cyan]=== EVALUATION METRIC SUMMARY ===[/bold cyan]")
        console.print(f"Total Test Images evaluated: [bold]{total_queries}[/bold]")
        console.print(f"Top-1 Accuracy: [bold green]{top1_acc:.2f}%[/bold green]")
        console.print(f"Top-5 Accuracy: [bold green]{top5_acc:.2f}%[/bold green]")
        console.print(f"Average Inference + DB Search Latency: [bold yellow]{avg_latency:.2f}ms[/bold yellow]\n")

        # Display breakdown table
        table = Table(title="SKU Accuracy Breakdown")
        table.add_column("SKU ID", justify="left", style="cyan", no_wrap=True)
        table.add_column("Total Samples", justify="right", style="magenta")
        table.add_column("Top-1 Match", justify="right", style="green")
        table.add_column("Top-1 Acc %", justify="right", style="green")
        table.add_column("Top-5 Match", justify="right", style="green")
        table.add_column("Top-5 Acc %", justify="right", style="green")

        for sku_id, stats in class_stats.items():
            tot = stats["total"]
            if tot == 0:
                continue
            t1 = stats["top1"]
            t5 = stats["top5"]
            t1_pct = (t1 / tot) * 100
            t5_pct = (t5 / tot) * 100
            table.add_row(sku_id, str(tot), str(t1), f"{t1_pct:.1f}%", str(t5), f"{t5_pct:.1f}%")

        console.print(table)

    except Exception as e:
        logger.error("Failed to execute Evaluation Engine CLI", error=str(e))
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
