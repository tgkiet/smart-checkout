import base64
from pathlib import Path

import cv2
import numpy as np


def load_image(path: str | Path) -> np.ndarray:
    """Loads an image from path and returns it in BGR format."""
    path_str = str(path)
    # Using cv2.imdecode to support non-ASCII paths if any
    image = cv2.imread(path_str)
    if image is None:
        raise FileNotFoundError(f"Could not load image from path: {path_str}")
    return image


def resize_with_pad(
    image: np.ndarray, target_size: tuple[int, int] = (224, 224), pad_color: tuple[int, int, int] = (0, 0, 0)
) -> np.ndarray:
    """
    Resizes an image preserving aspect ratio and pads it to target_size.

    Args:
        image: input BGR numpy array
        target_size: tuple of (target_height, target_width)
        pad_color: tuple of BGR values for padding

    Returns:
        np.ndarray: padded and resized image of target_size
    """
    target_h, target_w = target_size
    h, w = image.shape[:2]

    if h == 0 or w == 0:
        return np.zeros((target_h, target_w, 3), dtype=np.uint8)

    # Scale factor
    scale = min(target_w / w, target_h / h)
    new_w = int(w * scale)
    new_h = int(h * scale)

    # Resize
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

    # Pad
    pad_top = (target_h - new_h) // 2
    pad_bottom = target_h - new_h - pad_top
    pad_left = (target_w - new_w) // 2
    pad_right = target_w - new_w - pad_left

    padded = cv2.copyMakeBorder(resized, pad_top, pad_bottom, pad_left, pad_right, cv2.BORDER_CONSTANT, value=pad_color)

    return padded


def base64_to_cv2(b64_string: str) -> np.ndarray:
    """Converts base64 image string to OpenCV BGR image."""
    if "," in b64_string:
        # Strip off metadata prefix like 'data:image/jpeg;base64,'
        b64_string = b64_string.split(",")[1]

    image_bytes = base64.b64decode(b64_string)
    np_arr = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Decoded image is None. Invalid base64 string.")
    return image


def cv2_to_base64(image: np.ndarray, format_str: str = ".jpg") -> str:
    """Converts OpenCV BGR image to base64 string."""
    success, encoded_img = cv2.imencode(format_str, image)
    if not success:
        raise ValueError("Could not encode image to base64.")
    return base64.b64encode(encoded_img).decode("utf-8")
