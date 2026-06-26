from dataclasses import dataclass, field

import numpy as np


@dataclass
class DetectionResult:
    bbox: list[float]  # [x1, y1, x2, y2]
    mask: np.ndarray  # Binary mask (H, W) of boolean/uint8 values
    confidence: float
    class_id: int


@dataclass
class SKUInfo:
    sku_id: str
    name: str
    price: float  # VND
    weight_grams: float
    category: str


@dataclass
class SearchResult:
    sku_id: str
    similarity: float  # Cosine similarity [0, 1]
    view_type: str


@dataclass
class GroupedSearchResult:
    sku_id: str
    best_similarity: float
    matched_views: list[str] = field(default_factory=list)


@dataclass
class BoxAssignment:
    box_index: int
    sku_id: str
    sku_name: str
    vision_score: float
    unit_weight: float
    unit_price: float
    quantity: int = 1  # Quantity, >= 1 (e.g. stacked items)
    bbox: list[float] | None = None


@dataclass
class FusionResult:
    assignments: list[BoxAssignment]
    total_vision_score: float
    total_theoretical_weight: float
    actual_scale_weight: float
    weight_delta_grams: float
    confidence: float
    warnings: list[str] = field(default_factory=list)


@dataclass
class CheckoutResult:
    items: list[BoxAssignment]
    total_price: float
    scale_weight: float
    weight_match: bool
    confidence: float
    processing_time_ms: float
