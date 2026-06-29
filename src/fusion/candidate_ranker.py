from src.core.config import FusionConfig
from src.core.data_models import FusionResult, GroupedSearchResult
from src.core.logger import get_logger

logger = get_logger(__name__)


class CandidateRanker:
    def __init__(self, config: FusionConfig):
        self.config = config

    def prefilter(self, candidates_per_box: list[list[GroupedSearchResult]]) -> list[list[GroupedSearchResult]]:
        """
        Removes candidates that have a similarity score below the min_similarity_threshold.

        Args:
            candidates_per_box: list of lists of GroupedSearchResult candidates

        Returns:
            list[list[GroupedSearchResult]]: filtered candidate lists
        """
        filtered_candidates = []
        for box_candidates in candidates_per_box:
            filtered = [cand for cand in box_candidates if cand.best_similarity >= self.config.min_similarity_threshold]
            filtered_candidates.append(filtered)

        return filtered_candidates

    def auto_assign_confident(
        self, candidates_per_box: list[list[GroupedSearchResult]], scale_weight: float | None
    ) -> tuple[dict[int, GroupedSearchResult], list[list[GroupedSearchResult]], list[int], float | None]:
        """
        Identifies detections with extremely high confidence (similarity > confident_similarity_threshold)
        and auto-assigns them. This reduces the search space for the knapsack solver.

        Args:
            candidates_per_box: list of lists of GroupedSearchResult candidates
            scale_weight: total physical scale weight

        Returns:
            tuple:
                - dict[int, GroupedSearchResult]: auto-assignments mapping box_index to the selected candidate
                - list[list[GroupedSearchResult]]: remaining candidate lists for the knapsack solver
                - list[int]: mapping of indices in the remaining list back to the original box_index
                - float: remaining scale weight after subtracting auto-assigned product weights
        """
        auto_assignments = {}
        remaining_candidates = []
        remaining_box_indices = []
        remaining_weight = scale_weight

        for idx, box_candidates in enumerate(candidates_per_box):
            if not box_candidates:
                # No candidates at all -> let Knapsack resolve it as UNKNOWN
                remaining_candidates.append([])
                remaining_box_indices.append(idx)
                continue

            top_cand = box_candidates[0]
            # If the top candidate is extremely confident (e.g. > 0.98), auto-assign it
            if top_cand.best_similarity >= self.config.confident_similarity_threshold:
                auto_assignments[idx] = top_cand
                logger.info(
                    "Auto-assigned confident candidate",
                    box_index=idx,
                    sku_id=top_cand.sku_id,
                    similarity=top_cand.best_similarity,
                )
            else:
                remaining_candidates.append(box_candidates)
                remaining_box_indices.append(idx)

        return auto_assignments, remaining_candidates, remaining_box_indices, remaining_weight

    def detect_anomalies(self, result: FusionResult) -> list[str]:
        """
        Analyzes the fusion result and returns a list of warnings or flags for human intervention.
        """
        warnings = list(result.warnings)  # Start with solver warnings

        # 1. Look for unassigned/unknown items
        unknown_count = sum(1 for a in result.assignments if a.sku_id == "UNKNOWN")
        if unknown_count > 0:
            warnings.append(f"Phát hiện {unknown_count} sản phẩm chưa rõ danh tính (không khớp cơ sở dữ liệu)")

        # 2. Look for low vision score assignments
        low_confidence_count = sum(
            1
            for a in result.assignments
            if a.vision_score < self.config.min_similarity_threshold and a.sku_id != "UNKNOWN"
        )
        if low_confidence_count > 0:
            warnings.append(f"Phát hiện {low_confidence_count} sản phẩm khớp với độ tin cậy thấp")

        # 3. Mismatch in quantity vs boxes
        box_count = len(result.assignments)
        item_qty_sum = sum(a.quantity for a in result.assignments)
        if item_qty_sum > box_count:
            warnings.append(
                f"Cảnh báo: Có sản phẩm xếp chồng (Phát hiện {box_count} hình ảnh nhưng tính toán {item_qty_sum} sản phẩm dựa trên cân nặng)"
            )

        return warnings
