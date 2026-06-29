# Production Guide

## Configuration

Use `config/settings.yaml` for production defaults.

`config/settings.dev.yaml` is opt-in and loaded only when:

```bash
CONFIG_PROFILE=dev
```

Important environment overrides:

```bash
CONFIG_PATH=config/settings.yaml
MILVUS__HOST=standalone
SERVER__ADMIN_API_KEY=change-me
SERVER__CORS_ORIGINS=["https://your-frontend.example"]
```

## Services

Local compose includes:

- `api`: FastAPI application
- `standalone`: Milvus
- `minio`: Milvus object storage
- `etcd`: Milvus metadata store

For production, move secrets and persistent volumes into the platform you deploy on.

## Startup Order

1. Start Milvus dependencies.
2. Ensure model files exist under `models/`.
3. Start API.
4. Ingest catalog images.
5. Check `/api/v1/health`.

## Data Safety

Do not keep production product images only inside local Docker volumes. Back up:

- SKU metadata
- catalog images
- Milvus data or reproducible ingest source
- model checkpoints

## Model Deployment

The API currently loads PyTorch/Ultralytics model files. ONNX/TensorRT export can be added later behind the `detection/` and `embedding/` adapters without changing API contracts.

## Security

Set `SERVER__ADMIN_API_KEY` before exposing write endpoints:

- `/api/v1/catalog/ingest`
- `/api/v1/product/register`

Restrict CORS in production. The default `*` is for local demos only.
