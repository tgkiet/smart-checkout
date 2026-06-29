import pytest

from src.core.data_models import BoxAssignment, FusionResult, GroupedSearchResult
from src.database.sku_catalog import SKUCatalog
from src.fusion.candidate_ranker import CandidateRanker
from src.fusion.knapsack_solver import KnapsackFusionSolver


@pytest.fixture
def mock_catalog(temp_metadata_file):
    return SKUCatalog(temp_metadata_file)


def test_knapsack_solver_tolerance(mock_config, mock_catalog):
    solver = KnapsackFusionSolver(mock_config.fusion, mock_catalog)
    # Under 300g, 5% is less than 15g, so should return 15g
    assert solver._compute_tolerance(100.0) == 15.0
    assert solver._compute_tolerance(300.0) == 15.0
    # Over 300g, 5% is greater than 15g, so should return 5%
    assert solver._compute_tolerance(400.0) == 20.0
    assert solver._compute_tolerance(1000.0) == 50.0


def test_knapsack_solver_exact_solve(mock_config, mock_catalog):
    solver = KnapsackFusionSolver(mock_config.fusion, mock_catalog)

    # Candidates for box 0 and box 1
    # SKU001: Coca Cola 390ml (400g)
    # SKU002: Hao Hao Instant Noodles (75g)
    # SKU003: Aquafina 500ml (520g)
    candidates = [
        [
            GroupedSearchResult(sku_id="SKU001", best_similarity=0.95),
            GroupedSearchResult(sku_id="SKU002", best_similarity=0.4),
        ],
        [
            GroupedSearchResult(sku_id="SKU002", best_similarity=0.9),
            GroupedSearchResult(sku_id="SKU003", best_similarity=0.3),
        ],
    ]

    # Scale weight matches SKU001 (400g) + SKU002 (75g) = 475g
    result = solver.solve(candidates, scale_weight_grams=475.0)

    assert len(result.assignments) == 2
    assert result.assignments[0].sku_id == "SKU001"
    assert result.assignments[1].sku_id == "SKU002"
    assert result.total_theoretical_weight == 475.0
    assert result.weight_delta_grams == 0.0
    assert len(result.warnings) == 0


def test_knapsack_solver_mismatch_warning(mock_config, mock_catalog):
    solver = KnapsackFusionSolver(mock_config.fusion, mock_catalog)

    candidates = [[GroupedSearchResult(sku_id="SKU001", best_similarity=0.9)]]

    # Target is 400g. Scale says 800g.
    result = solver.solve(candidates, scale_weight_grams=800.0)

    # Since we only have 1 vision candidate and weight deficit is large,
    # the solver will try to duplicate SKU001 to resolve stacked items!
    # SKU001 is 400g, 2 units = 800g.
    # So it should resolve quantity=2!
    assert result.assignments[0].sku_id == "SKU001"
    assert result.assignments[0].quantity == 2
    assert result.total_theoretical_weight == 800.0
    assert result.weight_delta_grams == 0.0


def test_knapsack_solver_stacked_item_resolution(mock_config, mock_catalog):
    solver = KnapsackFusionSolver(mock_config.fusion, mock_catalog)

    candidates = [
        [GroupedSearchResult(sku_id="SKU001", best_similarity=0.9)],
        [GroupedSearchResult(sku_id="SKU002", best_similarity=0.85)],
    ]

    # Vision detects 1 Coke (400g) and 1 Noodles (75g) = 475g.
    # Scale says 875g (400g + 75g + 400g extra - e.g. another Coke is stacked underneath).
    # Solver should increment Coke quantity to 2.
    result = solver.solve(candidates, scale_weight_grams=875.0)

    coke_assign = next(a for a in result.assignments if a.sku_id == "SKU001")
    noodles_assign = next(a for a in result.assignments if a.sku_id == "SKU002")

    assert coke_assign.quantity == 2
    assert noodles_assign.quantity == 1
    assert result.total_theoretical_weight == 875.0
    assert result.weight_delta_grams == 0.0


def test_knapsack_solver_beam_solve(mock_config, mock_catalog):
    # Set knapsack_max_boxes_exact to 1 to force Beam Search for 2 boxes
    mock_config.fusion.knapsack_max_boxes_exact = 1
    solver = KnapsackFusionSolver(mock_config.fusion, mock_catalog)

    candidates = [
        [
            GroupedSearchResult(sku_id="SKU001", best_similarity=0.95),
            GroupedSearchResult(sku_id="SKU002", best_similarity=0.4),
        ],
        [
            GroupedSearchResult(sku_id="SKU002", best_similarity=0.9),
            GroupedSearchResult(sku_id="SKU003", best_similarity=0.3),
        ],
    ]

    result = solver.solve(candidates, scale_weight_grams=475.0)

    assert len(result.assignments) == 2
    assert result.assignments[0].sku_id == "SKU001"
    assert result.assignments[1].sku_id == "SKU002"


def test_candidate_ranker_prefilter(mock_config):
    ranker = CandidateRanker(mock_config.fusion)

    candidates = [
        [
            GroupedSearchResult(sku_id="SKU001", best_similarity=0.8),
            GroupedSearchResult(sku_id="SKU002", best_similarity=0.1),  # below threshold 0.2
        ]
    ]

    filtered = ranker.prefilter(candidates)
    assert len(filtered[0]) == 1
    assert filtered[0][0].sku_id == "SKU001"


def test_candidate_ranker_auto_assign(mock_config):
    ranker = CandidateRanker(mock_config.fusion)

    # confident threshold is 0.95
    candidates = [
        [GroupedSearchResult(sku_id="SKU001", best_similarity=0.98)],  # Auto-assign
        [GroupedSearchResult(sku_id="SKU002", best_similarity=0.7)],  # Knapsack
    ]

    auto_assigned, remaining, remaining_idx, rem_weight = ranker.auto_assign_confident(candidates, scale_weight=500.0)

    assert len(auto_assigned) == 1
    assert 0 in auto_assigned
    assert auto_assigned[0].sku_id == "SKU001"

    assert len(remaining) == 1
    assert remaining[0][0].sku_id == "SKU002"
    assert remaining_idx == [1]


def test_candidate_ranker_anomalies(mock_config):
    ranker = CandidateRanker(mock_config.fusion)

    # 1. Mismatch weight warning
    res_mismatch = FusionResult(
        assignments=[BoxAssignment(0, "SKU001", "Coke", 0.9, 400.0, 10000.0, 1)],
        total_vision_score=0.9,
        total_theoretical_weight=400.0,
        actual_scale_weight=500.0,
        weight_delta_grams=100.0,
        confidence=0.7,
        warnings=["Chênh lệch khối lượng vượt quá sai số cho phép"],
    )
    warnings = ranker.detect_anomalies(res_mismatch)
    assert any("Chênh lệch khối lượng" in w for w in warnings)

    # 2. Unknown items warning
    res_unknown = FusionResult(
        assignments=[BoxAssignment(0, "UNKNOWN", "Sản phẩm chưa có trong CSDL", 0.0, 0.0, 0.0, 1)],
        total_vision_score=0.0,
        total_theoretical_weight=0.0,
        actual_scale_weight=0.0,
        weight_delta_grams=0.0,
        confidence=0.0,
        warnings=[],
    )
    warnings = ranker.detect_anomalies(res_unknown)
    assert any("chưa rõ danh tính" in w for w in warnings)
