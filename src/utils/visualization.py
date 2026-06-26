import cv2
import numpy as np

from src.core.data_models import CheckoutResult, DetectionResult


def draw_detections(frame: np.ndarray, detections: list[DetectionResult]) -> np.ndarray:
    """
    Draws bounding boxes and transparent mask overlays for all detections on a copy of the frame.
    """
    vis_frame = frame.copy()
    h, w = frame.shape[:2]

    # Pre-defined harmonious color palette for overlays (BGR)
    colors = [
        (255, 56, 56),  # Coral Red
        (255, 157, 151),  # Soft Pink
        (255, 112, 31),  # Orange
        (255, 178, 29),  # Yellow
        (36, 179, 83),  # Green
        (112, 224, 220),  # Turquoise
        (112, 161, 255),  # Light Blue
        (165, 94, 234),  # Purple
    ]

    for idx, det in enumerate(detections):
        color = colors[idx % len(colors)]

        # 1. Bounding box
        x1, y1, x2, y2 = map(int, det.bbox)
        cv2.rectangle(vis_frame, (x1, y1), (x2, y2), color, 2)

        # 2. Binarized mask overlay
        mask = det.mask
        if mask.shape != (h, w):
            mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)

        colored_mask = np.zeros_like(vis_frame)
        colored_mask[mask > 0.5] = color

        # Blend mask with alpha transparency
        cv2.addWeighted(vis_frame, 1.0, colored_mask, 0.35, 0, dst=vis_frame)

        # Label
        label = f"Obj {idx}: {det.confidence:.2f}"
        cv2.putText(vis_frame, label, (x1, max(y1 - 10, 15)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)

    return vis_frame


def draw_checkout_result(frame: np.ndarray, result: CheckoutResult, detections: list[DetectionResult]) -> np.ndarray:
    """
    Overlays final checkout product assignments, prices, quantities, and totals onto the frame.
    """
    vis_frame = frame.copy()
    h, w = frame.shape[:2]

    # 1. Draw Bboxes and Masks for assigned boxes
    for assign in result.items:
        box_idx = assign.box_index
        if box_idx >= len(detections):
            continue

        det = detections[box_idx]
        x1, y1, x2, y2 = map(int, det.bbox)

        # Use Green for recognized items, Red/Orange for UNKNOWN
        if assign.sku_id == "UNKNOWN":
            color = (0, 100, 255)  # Orange-red
        else:
            color = (36, 179, 83)  # Harmonious Green

        # Draw Bbox
        cv2.rectangle(vis_frame, (x1, y1), (x2, y2), color, 2)

        # Draw Mask
        mask = det.mask
        if mask.shape != (h, w):
            mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)

        colored_mask = np.zeros_like(vis_frame)
        colored_mask[mask > 0.5] = color
        cv2.addWeighted(vis_frame, 1.0, colored_mask, 0.25, 0, dst=vis_frame)

        # Draw Label Text on top of box
        label_text = f"{assign.sku_name} (x{assign.quantity})"
        price_text = f"{assign.unit_price * assign.quantity:,.0f} VND"

        # Draw text background for legibility
        text_w1, text_h1 = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)[0]
        text_w2, text_h2 = cv2.getTextSize(price_text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)[0]
        max_w = max(text_w1, text_w2)

        # Draw background bubble
        cv2.rectangle(vis_frame, (x1, max(y1 - 35, 0)), (x1 + max_w + 10, max(y1 - 5, 0)), color, cv2.FILLED)

        # Write white text
        cv2.putText(
            vis_frame,
            label_text,
            (x1 + 5, max(y1 - 22, 12)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            vis_frame,
            price_text,
            (x1 + 5, max(y1 - 10, 24)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )

    # 2. Draw Total Summary Board in the top-left corner
    summary_bg = np.zeros((140, 280, 3), dtype=np.uint8)
    summary_bg[:] = (30, 30, 30)  # Dark grey board

    # Border
    cv2.rectangle(summary_bg, (0, 0), (279, 139), (70, 70, 70), 2)

    # Text headers and values
    cv2.putText(summary_bg, "SMART CHECKOUT", (15, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)

    # Total Price
    price_val = f"{result.total_price:,.0f} VND"
    cv2.putText(
        summary_bg, f"Tong Tien: {price_val}", (15, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 255), 1, cv2.LINE_AA
    )

    # Scale Weight
    weight_color = (0, 255, 0) if result.weight_match else (0, 100, 255)
    weight_val = f"{result.scale_weight:.1f}g"
    cv2.putText(
        summary_bg, f"Can nang: {weight_val}", (15, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.5, weight_color, 1, cv2.LINE_AA
    )

    # Match Indicator
    match_status = "CAN KHOP" if result.weight_match else "SAI LECH CAN"
    cv2.putText(
        summary_bg,
        f"Trang thai: {match_status}",
        (15, 105),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        weight_color,
        1,
        cv2.LINE_AA,
    )

    # Blend summary board into top-left of visual frame
    # Board size: 140x280. Make sure frame is large enough
    f_h, f_w = vis_frame.shape[:2]
    if f_h >= 160 and f_w >= 300:
        # Add transparent overlay for summary board
        roi = vis_frame[10:150, 10:290]
        cv2.addWeighted(roi, 0.15, summary_bg, 0.85, 0, dst=roi)

    return vis_frame
