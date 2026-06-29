# Architecture

Smart Checkout is split into small runtime adapters and one orchestration layer.

## Request Flow

1. `src/api/server.py` receives checkout images and optional scale weight.
2. `src/pipeline/multi_camera_pipeline.py` normalizes single or multi-camera frames.
3. `src/detection/yolo_segmentor.py` detects product instances.
4. `src/detection/mask_processor.py` extracts clean crops.
5. `src/embedding/siglip_encoder.py` encodes crops into normalized vectors.
6. `src/database/milvus_client.py` searches visual candidates.
7. `src/fusion/knapsack_solver.py` combines visual scores and weight constraints.
8. API returns checkout items, total price, weight match, and confidence.

## Boundaries

- `api/` should remain thin: validation, serialization, and HTTP/WebSocket concerns.
- `pipeline/` owns the end-to-end workflow.
- `detection/`, `embedding/`, `database/`, `scale/` are replaceable adapters.
- `fusion/` contains deterministic business logic and should stay highly tested.
- `scripts/` are operational entrypoints, not imported by the API server.

## Runtime Assets

These are intentionally outside source ownership:

- `models/`: YOLO weights and fine-tuned SigLIP checkpoints.
- `data/catalog/`: product view images.
- `data/training/`: training images.
- `data/test/`: evaluation images.
- `volumes/`: local Docker state for Milvus, MinIO, and etcd.

Only lightweight metadata such as `data/sku_metadata.json` belongs in source control.
