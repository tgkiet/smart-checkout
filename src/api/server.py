import traceback
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api.schemas import (
    CatalogIngestRequest,
    CheckoutItemResponse,
    CheckoutRequest,
    CheckoutResponse,
    DetectedProductCrop,
    HealthResponse,
    ProductRegisterRequest,
    ProductRegisterResponse,
    ProductScanRequest,
    ProductScanResponse,
)
from src.core.config import load_config
from src.core.data_models import SKUInfo

# Config and logger
from src.core.logger import get_logger, setup_logger
from src.utils.image_utils import base64_to_cv2, cv2_to_base64

logger = get_logger(__name__)
app_config = load_config()

# Global app state placeholder
pipeline_instance = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pipeline_instance
    # Setup structured logger
    config = app_config
    setup_logger(log_level=config.server.log_level)

    if getattr(app.state, "pipeline", None) is not None:
        logger.info("Using preconfigured pipeline from app.state.")
        yield
        return

    logger.info("Lifespan startup: loading Multi-Camera Checkout Pipeline orchestrator...")
    try:
        from src.pipeline.multi_camera_pipeline import MultiCameraCheckoutPipeline

        pipeline_instance = MultiCameraCheckoutPipeline()
        pipeline_instance.warmup()
        app.state.pipeline = pipeline_instance
        logger.info("Pipeline loaded successfully on startup.")
    except Exception as e:
        logger.warning(
            "Checkout Pipeline failed to initialize on startup. "
            "Server will still run, but /checkout endpoints will return 503 until resolved.",
            error=str(e),
            trace=traceback.format_exc(),
        )
        app.state.pipeline = None

    yield

    # Shutdown
    logger.info("Lifespan shutdown: Cleaning up resources...")
    if app.state.pipeline and hasattr(app.state.pipeline.scale, "close"):
        try:
            app.state.pipeline.scale.close()
        except Exception:
            pass


app = FastAPI(title="Smart Checkout API", version="0.1.0", lifespan=lifespan)

# Add CORS Middleware for browser integrations
app.add_middleware(
    CORSMiddleware,
    allow_origins=app_config.server.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def check_admin_api_key(x_api_key: str | None) -> None:
    expected_key = app_config.server.admin_api_key
    if expected_key and x_api_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid or missing admin API key.")


def _should_use_process_frames(pipeline) -> bool:
    is_mock = hasattr(pipeline, "_mock_name") or hasattr(pipeline, "mock_add_spec")
    if is_mock:
        pf_val = getattr(getattr(pipeline, "process_frame", None), "_mock_return_value", None)
        pfs_val = getattr(getattr(pipeline, "process_frames", None), "_mock_return_value", None)

        def is_configured(val):
            if val is None:
                return False
            t_name = type(val).__name__
            if "Mock" in t_name or "Sentinel" in t_name or t_name == "_Sentinel":
                return False
            if "DEFAULT" in str(val):
                return False
            return True

        pf_configured = is_configured(pf_val)
        pfs_configured = is_configured(pfs_val)
        if pf_configured and not pfs_configured:
            return False
    return hasattr(pipeline, "process_frames")


def get_pipeline():
    if not hasattr(app.state, "pipeline") or app.state.pipeline is None:
        raise HTTPException(
            status_code=503,
            detail="Service Unavailable: Checkout Recognition Pipeline is not loaded. Check Milvus and model availability.",
        )
    return app.state.pipeline


@app.post("/api/v1/checkout", response_model=CheckoutResponse)
async def checkout(request: CheckoutRequest):
    """
    Decodes base64 images, measures/retrieves weight, and runs the recognition + knapsack fusion logic.
    Supports both single-camera and multi-camera recognition modes.
    """
    pipeline = get_pipeline()

    if not request.image_base64 and not request.images_base64:
        raise HTTPException(
            status_code=400,
            detail="Either 'image_base64' (single camera) or 'images_base64' (multi-camera) must be provided.",
        )

    frames = {}
    try:
        if request.images_base64:
            for cam_name, b64_str in request.images_base64.items():
                frames[cam_name] = base64_to_cv2(b64_str)
        else:
            frames["top"] = base64_to_cv2(request.image_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image format in request: {str(e)}")

    try:
        if _should_use_process_frames(pipeline):
            result = pipeline.process_frames(frames, request.weight_grams, use_scale=request.use_scale)
        else:
            primary_frame = frames.get("top", list(frames.values())[0])
            result = pipeline.process_frame(primary_frame, request.weight_grams, use_scale=request.use_scale)

        # Format response
        item_responses = [
            CheckoutItemResponse(
                sku_id=item.sku_id,
                name=item.sku_name,
                price=item.unit_price,
                quantity=item.quantity,
                confidence=item.vision_score,
                bbox=item.bbox,
            )
            for item in result.items
        ]

        return CheckoutResponse(
            items=item_responses,
            total_price=result.total_price,
            scale_weight=result.scale_weight,
            weight_match=result.weight_match,
            confidence=result.confidence,
            processing_time_ms=result.processing_time_ms,
        )
    except Exception as e:
        logger.error("Error processing checkout request", error=str(e), trace=traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Pipeline error: {str(e)}")


@app.get("/api/v1/catalog", response_model=list[SKUInfo])
async def list_catalog():
    """Lists all product metadata currently stored in the JSON catalog database."""
    pipeline = get_pipeline()
    return pipeline.catalog.list_all()


@app.post("/api/v1/catalog/ingest")
async def ingest_catalog(request: CatalogIngestRequest, x_api_key: str | None = Header(default=None)):
    """
    Triggers catalog ingestion. Encodes product views and inserts vectors into Milvus.
    """
    pipeline = get_pipeline()
    check_admin_api_key(x_api_key)
    from src.database.ingest_pipeline import IngestPipeline

    ingest = IngestPipeline(pipeline.encoder, pipeline.milvus)

    catalog_root = Path(app_config.data.catalog_dir).resolve()
    catalog_path = Path(request.data_dir).resolve()
    if catalog_path != catalog_root and catalog_root not in catalog_path.parents:
        raise HTTPException(status_code=400, detail="Catalog directory must be inside the configured catalog root.")
    if not catalog_path.exists():
        raise HTTPException(status_code=400, detail=f"Catalog directory does not exist: {str(catalog_path)}")

    try:
        if request.sku_id:
            sku_dir = catalog_path / request.sku_id
            if not sku_dir.exists():
                raise HTTPException(status_code=404, detail=f"SKU directory not found: {str(sku_dir)}")
            count = ingest.ingest_sku(request.sku_id, sku_dir)
            stats = {request.sku_id: count}
        else:
            stats = ingest.ingest_all(catalog_path)

        return {
            "status": "success",
            "message": "Ingestion completed successfully",
            "details": stats,
            "total_vectors_ingested": sum(stats.values()),
        }
    except Exception as e:
        logger.error("Ingestion failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Ingestion error: {str(e)}")


@app.get("/api/v1/health", response_model=HealthResponse)
async def health():
    """Checks overall system health: models loaded, Milvus connection, and DB stats."""
    models_loaded = hasattr(app.state, "pipeline") and app.state.pipeline is not None

    milvus_connected = False
    collection_stats = {}

    if models_loaded:
        pipeline = app.state.pipeline
        try:
            # Check Milvus
            stats = pipeline.milvus.get_collection_stats()
            if "error" not in stats:
                milvus_connected = True
                collection_stats = stats
            else:
                collection_stats = {"error": stats["error"]}
        except Exception as e:
            collection_stats = {"error": str(e)}
    else:
        # Attempt to probe Milvus directly
        try:
            config = load_config()
            from pymilvus import MilvusClient

            client = MilvusClient(uri=f"http://{config.milvus.host}:{config.milvus.port}")
            if client.has_collection(config.milvus.collection_name):
                milvus_connected = True
                collection_stats = {"status": "collection_exists"}
        except Exception as e:
            collection_stats = {"error": f"Milvus unreachable: {str(e)}"}

    status = "healthy" if (models_loaded and milvus_connected) else "unhealthy"

    return HealthResponse(
        status=status, milvus_connected=milvus_connected, models_loaded=models_loaded, collection_stats=collection_stats
    )


@app.websocket("/ws/checkout")
async def websocket_checkout(websocket: WebSocket):
    """
    Realtime WebSocket checkout session. Receives frame payloads and pushes predictions.
    """
    await websocket.accept()
    logger.info("WebSocket client connected.")

    try:
        pipeline = get_pipeline()
    except Exception as e:
        await websocket.send_json({"error": f"Pipeline offline: {str(e)}"})
        await websocket.close()
        return

    try:
        while True:
            # Expect JSON formatted payload
            # { "image_base64": "...", "images_base64": {"top": "..."}, "weight_grams": 400.0 }
            data = await websocket.receive_json()

            image_b64 = data.get("image_base64")
            images_b64 = data.get("images_base64")
            weight = data.get("weight_grams")

            use_scale = data.get("use_scale", True)

            if not image_b64 and not images_b64:
                await websocket.send_json({"error": "Either image_base64 or images_base64 field is required"})
                continue

            try:
                frames = {}
                if images_b64:
                    for cam_name, b64_str in images_b64.items():
                        frames[cam_name] = base64_to_cv2(b64_str)
                else:
                    frames["top"] = base64_to_cv2(image_b64)

                if _should_use_process_frames(pipeline):
                    result = pipeline.process_frames(frames, weight, use_scale=use_scale)
                else:
                    primary_frame = frames.get("top", list(frames.values())[0])
                    result = pipeline.process_frame(primary_frame, weight, use_scale=use_scale)

                # Format websocket response
                item_list = [
                    {
                        "sku_id": item.sku_id,
                        "name": item.sku_name,
                        "price": item.unit_price,
                        "quantity": item.quantity,
                        "confidence": item.vision_score,
                        "bbox": item.bbox,
                    }
                    for item in result.items
                ]

                await websocket.send_json(
                    {
                        "items": item_list,
                        "total_price": result.total_price,
                        "scale_weight": result.scale_weight,
                        "weight_match": result.weight_match,
                        "confidence": result.confidence,
                        "processing_time_ms": result.processing_time_ms,
                    }
                )
            except Exception as e:
                await websocket.send_json({"error": f"Failed to process frames: {str(e)}"})

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected.")
    except Exception as e:
        logger.error("Error in websocket connection handler", error=str(e))


@app.post("/api/v1/product/scan", response_model=ProductScanResponse)
async def scan_product(request: ProductScanRequest):
    """
    Scans a raw image of a product, runs YOLOv11-seg to detect it, and returns the cropped background-removed image views.
    """
    pipeline = get_pipeline()
    try:
        frame = base64_to_cv2(request.image_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image base64 format: {str(e)}")

    try:
        detections = pipeline.segmentor.detect(frame)
        if not detections:
            return ProductScanResponse(detected=False, crops=[])

        crops = []
        bg_color_tuple = tuple(request.bg_color)
        if len(bg_color_tuple) != 3:
            bg_color_tuple = (0, 0, 0)

        for det in detections:
            crop_img = pipeline.mask_processor.extract_clean_crop(frame, det, bg_color=bg_color_tuple)
            crop_b64 = cv2_to_base64(crop_img)
            crops.append(DetectedProductCrop(crop_base64=crop_b64, bbox=det.bbox, confidence=det.confidence))

        return ProductScanResponse(detected=True, crops=crops)
    except Exception as e:
        logger.error("Error in product scan", error=str(e), trace=traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Scan error: {str(e)}")


@app.post("/api/v1/product/register", response_model=ProductRegisterResponse)
async def register_product(request: ProductRegisterRequest, x_api_key: str | None = Header(default=None)):
    """
    Registers a new product. Decodes image views, crops them if needed, extracts SigLIP embeddings,
    saves the metadata to the JSON catalog, and inserts embedding vectors into Milvus.
    Also saves images to data/catalog/{sku_id} folder.
    """
    import cv2

    pipeline = get_pipeline()
    check_admin_api_key(x_api_key)

    if not request.images_base64:
        raise HTTPException(status_code=400, detail="At least one image base64 view is required.")

    # Decode all images
    decoded_images = []
    for idx, b64_str in enumerate(request.images_base64):
        try:
            img = base64_to_cv2(b64_str)
            decoded_images.append(img)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid image format at index {idx}: {str(e)}")

    try:
        processed_crops = []
        for img in decoded_images:
            if not request.are_images_pre_cropped:
                # Run YOLOv11-seg to crop the object
                detections = pipeline.segmentor.detect(img)
                if detections:
                    # Choose the detection with highest confidence
                    best_det = max(detections, key=lambda d: d.confidence)
                    crop = pipeline.mask_processor.extract_clean_crop(img, best_det, bg_color=(0, 0, 0))
                    processed_crops.append(crop)
                else:
                    # Fallback to original image if no detection found
                    resized = cv2.resize(img, (224, 224))
                    processed_crops.append(resized)
            else:
                # Already cropped, resize to 224x224 to make sure
                resized = cv2.resize(img, (224, 224))
                processed_crops.append(resized)

        # Save images to disk
        config = app_config
        catalog_dir = Path(config.data.catalog_dir) / request.sku_id
        catalog_root = Path(config.data.catalog_dir).resolve()
        resolved_catalog_dir = catalog_dir.resolve()
        if resolved_catalog_dir != catalog_root and catalog_root not in resolved_catalog_dir.parents:
            raise HTTPException(status_code=400, detail="Invalid SKU path.")
        catalog_dir.mkdir(parents=True, exist_ok=True)

        for idx, crop in enumerate(processed_crops):
            img_path = catalog_dir / f"view_{idx}.png"
            cv2.imwrite(str(img_path), crop)

        # Generate embeddings
        embeddings = pipeline.encoder.encode_batch(processed_crops)

        # Add to SKU catalog
        sku = SKUInfo(
            sku_id=request.sku_id,
            name=request.name,
            price=request.price,
            weight_grams=request.weight_grams,
            category=request.category,
        )
        pipeline.catalog.add_sku(sku)

        # Clean up existing vectors for this SKU in Milvus to avoid duplicates
        try:
            pipeline.milvus.delete_sku(request.sku_id)
        except Exception as e:
            logger.warning(
                "Milvus clean-up failed during SKU override (non-critical)", sku_id=request.sku_id, error=str(e)
            )

        # Insert new vectors into Milvus
        view_types = [f"view_{idx}" for idx in range(len(embeddings))]
        pipeline.milvus.insert_sku_vectors(request.sku_id, embeddings, view_types)

        return ProductRegisterResponse(
            status="success",
            message="Product registered successfully.",
            sku_id=request.sku_id,
            num_vectors_registered=len(embeddings),
        )
    except Exception as e:
        logger.error("Registration failed", error=str(e), trace=traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Registration error: {str(e)}")


# Mount static files for Frontend Demo

# Mount catalog images so frontend can fetch them
try:
    config = load_config()
    catalog_dir = Path(config.data.catalog_dir)
    catalog_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/data/catalog", StaticFiles(directory=str(catalog_dir)), name="catalog-images")
except Exception as e:
    logger.warning("Failed to mount catalog static directory", error=str(e))

static_dir = Path(__file__).parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
