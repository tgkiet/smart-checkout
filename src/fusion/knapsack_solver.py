import itertools

from src.core.config import FusionConfig
from src.core.data_models import BoxAssignment, FusionResult, GroupedSearchResult
from src.core.logger import get_logger
from src.database.sku_catalog import SKUCatalog

logger = get_logger(__name__)


class KnapsackFusionSolver:
    def __init__(self, config: FusionConfig, catalog: SKUCatalog):
        self.config = config
        self.catalog = catalog

    def _compute_tolerance(self, theoretical_weight: float) -> float:
        """
        Computes dynamic weight tolerance.
        Formula: max(15g, 5% * theoretical_weight)
        """
        return max(15.0, 0.05 * theoretical_weight)

    def _score_assignment(
        self, sku_ids: list[str], vision_scores: list[float], scale_weight: float | None
    ) -> tuple[float, float, float]:
        """
        Computes objective score for a given assignment.
        Objective: score = alpha * avg(vision_scores) - beta * (abs(W_theo - W_scale) / W_scale)

        Returns:
            tuple: (total_score, theoretical_weight, weight_penalty)
        """
        n = len(sku_ids)
        if n == 0:
            return 0.0, 0.0, 0.0

        avg_vision = sum(vision_scores) / n
        theoretical_weight = sum(self.catalog.get_weight(sku_id) for sku_id in sku_ids)

        # Calculate weight penalty
        if scale_weight is None:
            weight_penalty = 0.0
        elif scale_weight > 0:
            weight_diff_ratio = abs(theoretical_weight - scale_weight) / scale_weight
            weight_penalty = weight_diff_ratio
        else:
            # If scale is zero but we assigned items, penalize heavily
            weight_penalty = theoretical_weight

        if scale_weight is None:
            total_score = avg_vision
        else:
            total_score = self.config.alpha * avg_vision - self.config.beta * weight_penalty

        return total_score, theoretical_weight, weight_penalty

    def solve(
        self, candidates_per_box: list[list[GroupedSearchResult]], scale_weight_grams: float | None
    ) -> FusionResult:
        """
        Fuses vision candidate lists with physical scale weight to find the optimal SKU assignment.

        Args:
            candidates_per_box: list (for each box) of lists of GroupedSearchResult candidates
            scale_weight_grams: weight measured from the scale (or None if scale is disabled)

        Returns:
            FusionResult: best assignment mapping each box to a SKU
        """
        n_boxes = len(candidates_per_box)
        if n_boxes == 0:
            return FusionResult(
                assignments=[],
                total_vision_score=0.0,
                total_theoretical_weight=0.0,
                actual_scale_weight=scale_weight_grams if scale_weight_grams is not None else 0.0,
                weight_delta_grams=-(scale_weight_grams if scale_weight_grams is not None else 0.0),
                confidence=0.0,
                warnings=["No items detected"],
            )

        # Standardize candidates: Ensure every box has at least one candidate.
        # If a box is empty, add a fallback "UNKNOWN" candidate.
        # If a box is not empty, also append "UNKNOWN" so the solver has the option to reject bad matches.
        cleaned_candidates = []
        for i, box_candidates in enumerate(candidates_per_box):
            if not box_candidates:
                cleaned_candidates.append([GroupedSearchResult(sku_id="UNKNOWN", best_similarity=0.0)])
            else:
                cands = list(box_candidates)
                if not any(c.sku_id == "UNKNOWN" for c in cands):
                    cands.append(GroupedSearchResult(sku_id="UNKNOWN", best_similarity=0.0))
                cleaned_candidates.append(cands)

        # Fast path: when no scale weight is available (vision-only mode),
        # the objective function collapses to avg(vision_scores) which is
        # maximized by independently selecting the top candidate for each box.
        # This avoids the O(K^N) combinatorial search entirely.
        if scale_weight_grams is None:
            best_sku_ids, best_vision_scores = self._solve_greedy_top1(cleaned_candidates)
        # Choose solver based on complexity when scale weight is available
        elif n_boxes <= self.config.knapsack_max_boxes_exact:
            best_sku_ids, best_vision_scores = self._solve_exact(cleaned_candidates, scale_weight_grams)
        else:
            best_sku_ids, best_vision_scores = self._solve_beam(cleaned_candidates, scale_weight_grams)

        # Post-process for stacked items (quantities > 1)
        # If theoretical weight is significantly lighter than the scale weight,
        # it is highly likely that some items are stacked on top of each other (occluded).
        # We greedily add duplicate items of the already detected SKUs to minimize the weight delta.
        assignments = []
        for idx, (sku_id, vis_score) in enumerate(zip(best_sku_ids, best_vision_scores)):
            sku_info = self.catalog.get_sku(sku_id)
            if sku_id == "UNKNOWN" or sku_info is None:
                assignments.append(
                    BoxAssignment(
                        box_index=idx,
                        sku_id="UNKNOWN",
                        sku_name="Sản phẩm chưa có trong CSDL",
                        vision_score=vis_score,
                        unit_weight=0.0,
                        unit_price=0.0,
                        quantity=1,
                    )
                )
            else:
                assignments.append(
                    BoxAssignment(
                        box_index=idx,
                        sku_id=sku_id,
                        sku_name=sku_info.name,
                        vision_score=vis_score,
                        unit_weight=sku_info.weight_grams,
                        unit_price=sku_info.price,
                        quantity=1,
                    )
                )

        total_theoretical_weight = sum(a.unit_weight * a.quantity for a in assignments)

        warnings = []

        # Greedily search for stacked items if we are below scale weight by more than tolerance
        if scale_weight_grams is not None:
            tolerance = self._compute_tolerance(total_theoretical_weight)
            weight_deficit = scale_weight_grams - total_theoretical_weight

            if weight_deficit > tolerance:
                logger.info(
                    "Weight deficit exceeds tolerance, attempting stacked item resolution", deficit=weight_deficit
                )
                # Find valid candidates for duplication (only SKUs that are already assigned)
                valid_assignments = [a for a in assignments if a.sku_id != "UNKNOWN"]

                while weight_deficit > 10.0 and valid_assignments:
                    # Find the assignment that, if duplicated, gets us closest to the scale weight without overshooting too much
                    best_add_idx = -1
                    best_add_diff = float("inf")

                    for idx, assign in enumerate(valid_assignments):
                        new_diff = abs(weight_deficit - assign.unit_weight)
                        if new_diff < best_add_diff:
                            best_add_diff = new_diff
                            best_add_idx = idx

                    if best_add_idx != -1:
                        target_assign = valid_assignments[best_add_idx]
                        # If adding this weight improves or doesn't overshoot by too much, perform it
                        if target_assign.unit_weight <= weight_deficit + tolerance:
                            target_assign.quantity += 1
                            weight_deficit -= target_assign.unit_weight
                            logger.info(
                                "Resolved stacked item", sku_id=target_assign.sku_id, new_qty=target_assign.quantity
                            )
                        else:
                            break  # Cannot add any more without overshooting
                    else:
                        break

        total_theoretical_weight = sum(a.unit_weight * a.quantity for a in assignments)

        # Compute weight matching metrics
        if scale_weight_grams is not None:
            weight_delta = scale_weight_grams - total_theoretical_weight
            tolerance = self._compute_tolerance(total_theoretical_weight)
            if abs(weight_delta) > tolerance:
                warnings.append(
                    f"Chênh lệch khối lượng vượt quá sai số cho phép: {weight_delta:.1f}g (Cho phép: {tolerance:.1f}g)"
                )

            # Compute overall confidence combining vision and weight match
            avg_vision_score = sum(a.vision_score for a in assignments) / len(assignments) if assignments else 0.0
            weight_factor = max(0.0, 1.0 - abs(weight_delta) / max(100.0, scale_weight_grams))
            overall_confidence = 0.7 * avg_vision_score + 0.3 * weight_factor
            actual_scale_val = scale_weight_grams
        else:
            weight_delta = 0.0
            avg_vision_score = sum(a.vision_score for a in assignments) / len(assignments) if assignments else 0.0
            overall_confidence = avg_vision_score
            actual_scale_val = 0.0

        return FusionResult(
            assignments=assignments,
            total_vision_score=sum(a.vision_score for a in assignments),
            total_theoretical_weight=total_theoretical_weight,
            actual_scale_weight=actual_scale_val,
            weight_delta_grams=weight_delta,
            confidence=overall_confidence,
            warnings=warnings,
        )

    def _solve_exact(
        self, candidates_per_box: list[list[GroupedSearchResult]], scale_weight: float
    ) -> tuple[list[str], list[float]]:
        """Solves via exact brute-force search over all candidate combinations."""
        # Get list of index ranges for product
        box_candidate_ranges = [range(len(candidates)) for candidates in candidates_per_box]

        best_score = -float("inf")
        best_comb = None

        # Iterate over all combinations of candidate indices
        for comb in itertools.product(*box_candidate_ranges):
            sku_ids = [candidates_per_box[i][comb[i]].sku_id for i in range(len(comb))]
            vision_scores = [candidates_per_box[i][comb[i]].best_similarity for i in range(len(comb))]

            score, _, _ = self._score_assignment(sku_ids, vision_scores, scale_weight)
            if score > best_score:
                best_score = score
                best_comb = comb

        # Map back to best selections
        best_sku_ids = [candidates_per_box[i][best_comb[i]].sku_id for i in range(len(best_comb))]
        best_vision_scores = [candidates_per_box[i][best_comb[i]].best_similarity for i in range(len(best_comb))]

        return best_sku_ids, best_vision_scores

    def _solve_beam(
        self, candidates_per_box: list[list[GroupedSearchResult]], scale_weight: float
    ) -> tuple[list[str], list[float]]:
        """Solves via Beam Search for higher complexity cases (>8 boxes)."""
        beam_width = self.config.beam_width
        # A state in the beam: (score, list_of_sku_ids, list_of_vision_scores)
        # Initialize with empty prefix
        beam = [(0.0, [], [])]

        for box_candidates in candidates_per_box:
            next_beam = []
            for score, sku_ids, vision_scores in beam:
                for cand in box_candidates:
                    new_sku_ids = sku_ids + [cand.sku_id]
                    new_vision_scores = vision_scores + [cand.best_similarity]

                    # Compute intermediate score for this prefix
                    new_score, _, _ = self._score_assignment(new_sku_ids, new_vision_scores, scale_weight)
                    next_beam.append((new_score, new_sku_ids, new_vision_scores))

            # Sort next beam descending and keep top beam_width
            next_beam.sort(key=lambda x: x[0], reverse=True)
            beam = next_beam[:beam_width]

        # Return the best final state in the beam
        best_state = beam[0]
        return best_state[1], best_state[2]

    def _solve_greedy_top1(
        self, candidates_per_box: list[list[GroupedSearchResult]]
    ) -> tuple[list[str], list[float]]:
        """
        O(N) greedy solver for vision-only mode (no scale weight).

        When scale_weight is None, the objective score = avg(vision_scores), which is
        maximized by independently selecting the top candidate for each box.
        This is mathematically equivalent to the brute-force result but runs in O(N)
        instead of O(K^N).
        """
        best_sku_ids = []
        best_vision_scores = []
        for box_candidates in candidates_per_box:
            # Candidates are already sorted by best_similarity descending
            top = box_candidates[0]
            best_sku_ids.append(top.sku_id)
            best_vision_scores.append(top.best_similarity)
        return best_sku_ids, best_vision_scores
