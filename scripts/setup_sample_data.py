import json
from pathlib import Path

import cv2
import numpy as np


def create_sample_image(text: str, filename: Path, color: tuple[int, int, int]):
    """Generates a dummy product image with a solid color and text overlay."""
    # Create a 400x400 BGR image
    img = np.zeros((400, 400, 3), dtype=np.uint8)
    # Fill with solid color
    img[:] = color

    # Draw a inner white border
    cv2.rectangle(img, (20, 20), (380, 380), (255, 255, 255), 3)

    # Draw product label
    font = cv2.FONT_HERSHEY_SIMPLEX
    text_size = cv2.getTextSize(text, font, 0.6, 2)[0]
    text_x = (400 - text_size[0]) // 2
    text_y = (400 - text_size[1]) // 2

    cv2.putText(img, text, (text_x, text_y), font, 0.6, (255, 255, 255), 2, cv2.LINE_AA)

    # Save image
    cv2.imwrite(str(filename), img)


def main():
    print("Setting up sample bootstrap dataset...")

    # Define SKU list
    skus = [
        {
            "sku_id": "SKU001",
            "name": "Coca Cola 390ml",
            "price": 10000.0,
            "weight_grams": 400.0,
            "category": "beverage",
            "color": (255, 56, 56),  # BGR Red
        },
        {
            "sku_id": "SKU002",
            "name": "Hao Hao Noodles",
            "price": 4500.0,
            "weight_grams": 75.0,
            "category": "food",
            "color": (255, 157, 151),  # Soft Pink
        },
        {
            "sku_id": "SKU003",
            "name": "Aquafina 500ml",
            "price": 5000.0,
            "weight_grams": 520.0,
            "category": "beverage",
            "color": (112, 161, 255),  # Light Blue
        },
        {
            "sku_id": "SKU004",
            "name": "Lays Chips 100g",
            "price": 18000.0,
            "weight_grams": 100.0,
            "category": "food",
            "color": (255, 178, 29),  # Yellow
        },
        {
            "sku_id": "SKU005",
            "name": "Oishi Corn Snack",
            "price": 6000.0,
            "weight_grams": 45.0,
            "category": "food",
            "color": (36, 179, 83),  # Green
        },
    ]

    # Create directories
    base_dir = Path(".")
    catalog_dir = base_dir / "data" / "catalog"
    training_dir = base_dir / "data" / "training"
    test_dir = base_dir / "data" / "test"
    models_dir = base_dir / "models"

    catalog_dir.mkdir(parents=True, exist_ok=True)
    training_dir.mkdir(parents=True, exist_ok=True)
    test_dir.mkdir(parents=True, exist_ok=True)
    models_dir.mkdir(parents=True, exist_ok=True)

    # Create sku_metadata.json
    metadata_path = base_dir / "data" / "sku_metadata.json"

    # Strip BGR color from JSON metadata
    json_metadata = []
    for s in skus:
        item = s.copy()
        item.pop("color")
        json_metadata.append(item)

    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(json_metadata, f, indent=2, ensure_ascii=False)
    print(f"Created metadata catalog file: {metadata_path}")

    # Generate mock catalog images (3 views per SKU)
    for s in skus:
        sku_id = s["sku_id"]
        sku_name = s["name"]
        color = s["color"]

        sku_cat_dir = catalog_dir / sku_id
        sku_cat_dir.mkdir(exist_ok=True)

        # Write views
        create_sample_image(f"{sku_name} (front)", sku_cat_dir / "front.jpg", color)
        create_sample_image(f"{sku_name} (back)", sku_cat_dir / "back.jpg", color)
        create_sample_image(f"{sku_name} (top)", sku_cat_dir / "top.jpg", color)

        # Also create training set
        sku_train_dir = training_dir / sku_id
        sku_train_dir.mkdir(exist_ok=True)
        create_sample_image(f"{sku_name} (train1)", sku_train_dir / "train1.jpg", color)
        create_sample_image(f"{sku_name} (train2)", sku_train_dir / "train2.jpg", color)

        # Also create test set
        sku_test_dir = test_dir / sku_id
        sku_test_dir.mkdir(exist_ok=True)
        create_sample_image(f"{sku_name} (test1)", sku_test_dir / "test1.jpg", color)

    print("Sample dataset bootstrapping completed successfully.")


if __name__ == "__main__":
    main()
