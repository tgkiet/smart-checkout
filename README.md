# Smart Checkout

AI-powered checkout pipeline for product segmentation, visual retrieval, and weight-based fusion.

## Production Shape

```text
config/                 Runtime configuration
src/                    Application source code
  api/                  FastAPI app and web demo
  detection/            YOLO segmentation adapter
  embedding/            SigLIP encoder and ArcFace training code
  database/             Milvus and SKU catalog adapters
  fusion/               Candidate ranking and weight fusion
  pipeline/             Checkout orchestration
  scale/                Scale hardware abstraction
scripts/                Operational CLIs for ingest, training, evaluation, demos
tests/                  Automated tests
docs/                   Architecture and operations notes
assets/demo/            Non-production demo images
data/                   Runtime catalog/training/test data, ignored except metadata
models/                 Runtime model files, ignored
volumes/                Local Docker volumes, ignored
```

Large runtime assets (`models/`, `data/catalog/`, `data/training/`, `data/test/`, `volumes/`, `venv/`) are intentionally not source code.

## Quick Start

```bash
make setup-dev
make infra-up
make sample-data
make ingest
make serve-dev
```

Open:

```text
http://localhost:8000
http://localhost:8000/docs
```

## Tests

Stable unit/integration tests that do not require live model downloads or API `TestClient`:

```bash
make test
```

API tests:

```bash
make test-api
```

Coverage:

```bash
make test-cov
```

## Operations

Start only local infrastructure:

```bash
make infra-up
```

Run API in production-style mode:

```bash
make serve
```

Build the API container:

```bash
make build
```

Ingest catalog images into Milvus:

```bash
make ingest
```

## Model Notes

- YOLO segmentation is used only to isolate product crops.
- SigLIP embeddings plus Milvus retrieve SKU candidates.
- Weight fusion resolves ambiguous visual matches and stacked products.
- Fine-tuned checkpoints should be placed under `models/` and referenced from `config/settings.yaml`.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/PRODUCTION.md](docs/PRODUCTION.md) for details.
