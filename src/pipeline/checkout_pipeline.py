import time

import numpy as np

from src.core.config import load_config
from src.core.data_models import BoxAssignment, CheckoutResult, FusionResult
from src.core.logger import get_logger
from src.database.milvus_client import MilvusProductDB
from src.database.sku_catalog import SKUCatalog
from src.detection.mask_processor import MaskProcessor

# Import modules
from src.detection.yolo_segmentor import YOLOSegmentor
from src.embedding.siglip_encoder import SigLIPEncoder
from src.fusion.candidate_ranker import CandidateRanker
from src.fusion.knapsack_solver import KnapsackFusionSolver

logger = get_logger(__name__)


class CheckoutPipeline:
    def __init__(self, config_path: str | None = None):
        logger.info("Initializing Checkout Orchestrator Pipeline...")
        self.config = load_config(config_path)

        # Initialize sub-components
        self.segmentor = YOLOSegmentor(self.config.detection)
        self.mask_processor = MaskProcessor()
        self.encoder = SigLIPEncoder(self.config.embedding)
        self.milvus = MilvusProductDB(self.config.milvus)
        self.catalog = SKUCatalog(self.config.data.sku_metadata_path)
        self.ranker = CandidateRanker(self.config.fusion)
        self.solver = KnapsackFusionSolver(self.config.fusion, self.catalog)
        
        if self.config.scale.enabled:
            from src.scale.factory import ScaleFactory
            self.scale = ScaleFactory.create(self.config.scale)
        else:
            self.scale = None

        logger.info("Checkout Orchestrator Pipeline initialized successfully.")

    def warmup(self) -> None:
        """Warms up the deep learning models (YOLO & SigLIP)."""
        logger.info("Starting pipeline model warmup...")
        self.segmentor.warmup()

        # Warmup SigLIP encoder
        dummy_crop = np.zeros((224, 224, 3), dtype=np.uint8)
        self.encoder.encode(dummy_crop)
        logger.info("Pipeline model warmup complete.")

    def process_frame(
        self, frame: np.ndarray, weight_grams: float | None = None, use_scale: bool = True
    ) -> CheckoutResult:
        """
        Executes the entire end-to-end checkout flow on a single camera frame.

        Args:
            frame: input frame in BGR format
            weight_grams: physical scale weight (optional, if None, reads from scale driver)

        Returns:
            CheckoutResult: final purchase list and validation results
        """
        start_time = time.perf_counter()

        # --- Stage 1: Detection ---
        t0 = time.perf_counter()
        detections = self.segmentor.detect(frame)
        t_detect = (time.perf_counter() - t0) * 1000

        # If no objects are detected, return empty checkout result immediately
        if not detections:
            # Read scale anyway to report current weight
            if use_scale and self.config.scale.enabled and self.scale is not None:
                actual_weight = weight_grams if weight_grams is not None else self.scale.read_weight()
                weight_match = actual_weight <= 15.0
            else:
                actual_weight = 0.0
                weight_match = True

            duration = (time.perf_counter() - start_time) * 1000
            logger.info(f"[PIPELINE] Completed in {duration:.1f}ms | Detect: {t_detect:.1f}ms | Objects: 0")
            return CheckoutResult(
                items=[],
                total_price=0.0,
                scale_weight=actual_weight,
                weight_match=weight_match,
                confidence=1.0,
                processing_time_ms=duration,
            )

        # --- Stage 2: Mask Processing & Background Removal ---
        t0 = time.perf_counter()
        crops = self.mask_processor.extract_batch(frame, detections)
        t_crop = (time.perf_counter() - t0) * 1000

        # --- Stage 3: Embedding Extraction ---
        t0 = time.perf_counter()
        embeddings = self.encoder.encode_batch(crops)
        t_embed = (time.perf_counter() - t0) * 1000

        # --- Stage 4: Multi-Vector Search & Group-by-SKU ---
        t0 = time.perf_counter()
        candidates_per_box = []
        for emb in embeddings:
            cands = self.milvus.search_and_group(query_vector=emb, top_k_raw=15, top_k_grouped=5)
            candidates_per_box.append(cands)
        t_search = (time.perf_counter() - t0) * 1000

        # --- Stage 5: Prefilter & Auto-Assign Confident ---
        t0 = time.perf_counter()

        # Determine actual scale weight to use
        if not use_scale:
            actual_weight = None
        elif weight_grams is not None:
            actual_weight = weight_grams
        elif self.config.scale.enabled and self.scale is not None:
            actual_weight = self.scale.read_weight()
            # If the scale reads 0 but we have detections, the scale is not being used
            if actual_weight is not None and actual_weight <= 0.0 and len(detections) > 0:
                actual_weight = None
        else:
            actual_weight = None

        # Prefilter low similarity candidates
        filtered_candidates = self.ranker.prefilter(candidates_per_box)

        # Extract extremely confident candidates directly
        auto_assignments, remaining_candidates, remaining_box_indices, _ = self.ranker.auto_assign_confident(
            filtered_candidates, actual_weight
        )

        # Calculate weight consumed by auto-assigned items
        auto_assigned_weight = 0.0
        combined_assignments = []

        for box_idx, cand in auto_assignments.items():
            sku_info = self.catalog.get_sku(cand.sku_id)
            if sku_info:
                weight = sku_info.weight_grams
                price = sku_info.price
                name = sku_info.name
            else:
                weight = 0.0
                price = 0.0
                name = "Sản phẩm chưa có trong CSDL"

            auto_assigned_weight += weight
            combined_assignments.append(
                BoxAssignment(
                    box_index=box_idx,
                    sku_id=cand.sku_id,
                    sku_name=name,
                    vision_score=cand.best_similarity,
                    unit_weight=weight,
                    unit_price=price,
                    quantity=1,
                    bbox=detections[box_idx].bbox,
                )
            )

        # --- Stage 6: Knapsack Fusion ---
        # Remaining weight for Knapsack solver
        if actual_weight is not None:
            remaining_scale_weight = max(0.0, actual_weight - auto_assigned_weight)
        else:
            remaining_scale_weight = None

        if remaining_candidates:
            # Solve remaining assignments using Knapsack & physical weight constraint
            fusion_result = self.solver.solve(remaining_candidates, remaining_scale_weight)

            # Map back local solver indices to original global box indices
            for assign in fusion_result.assignments:
                local_idx = assign.box_index
                global_idx = remaining_box_indices[local_idx]
                assign.box_index = global_idx
                assign.bbox = detections[global_idx].bbox
                combined_assignments.append(assign)

            fusion_warnings = fusion_result.warnings
        else:
            # All items were auto-assigned confidently
            fusion_warnings = []

        t_fuse = (time.perf_counter() - t0) * 1000

        # Sort combined assignments back into original detection order
        combined_assignments.sort(key=lambda x: x.box_index)

        # Compute final checkout stats
        total_price = sum(a.unit_price * a.quantity for a in combined_assignments)
        total_theoretical_weight = sum(a.unit_weight * a.quantity for a in combined_assignments)

        # Tolerance check
        if actual_weight is not None:
            tolerance = self.solver._compute_tolerance(total_theoretical_weight)
            weight_delta = actual_weight - total_theoretical_weight
            weight_match = abs(weight_delta) <= tolerance
            actual_scale_val = actual_weight
        else:
            weight_delta = 0.0
            weight_match = True
            actual_scale_val = 0.0

        # Log anomalies/warnings
        final_fusion_res = FusionResult(
            assignments=combined_assignments,
            total_vision_score=sum(a.vision_score for a in combined_assignments),
            total_theoretical_weight=total_theoretical_weight,
            actual_scale_weight=actual_scale_val,
            weight_delta_grams=weight_delta,
            confidence=0.0,  # placeholder
            warnings=fusion_warnings,
        )
        anomalies = self.ranker.detect_anomalies(final_fusion_res)
        for anomaly in anomalies:
            logger.warning("Pipeline Anomaly Detected", warning=anomaly)

        duration = (time.perf_counter() - start_time) * 1000

        # Compute final confidence
        avg_vision = (
            sum(a.vision_score for a in combined_assignments) / len(combined_assignments)
            if combined_assignments
            else 1.0
        )
        final_confidence = 0.8 * avg_vision + 0.2 * (1.0 if weight_match else 0.0)

        actual_weight_log = f"{actual_weight:.1f}g" if actual_weight is not None else "Disabled"
        weight_delta_log = f"{weight_delta:.1f}g" if actual_weight is not None else "Disabled"

        logger.info(
            f"[PIPELINE] Completed in {duration:.1f}ms | "
            f"Detect: {t_detect:.1f}ms | Crop: {t_crop:.1f}ms | Embed: {t_embed:.1f}ms | "
            f"Search: {t_search:.1f}ms | Fuse: {t_fuse:.1f}ms | "
            f"Weight: Scale={actual_weight_log} Theo={total_theoretical_weight:.1f}g Delta={weight_delta_log} Match={weight_match}"
        )

        return CheckoutResult(
            items=combined_assignments,
            total_price=total_price,
            scale_weight=actual_scale_val,
            weight_match=weight_match,
            confidence=final_confidence,
            processing_time_ms=duration,
        )
