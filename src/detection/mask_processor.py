import numpy as np

from src.core.data_models import DetectionResult
from src.utils.image_utils import resize_with_pad


class MaskProcessor:
    def extract_clean_crop(
        self,
        frame: np.ndarray,
        detection: DetectionResult,
        target_size: tuple[int, int] = (224, 224),
        bg_color: tuple[int, int, int] = (0, 0, 0),
    ) -> np.ndarray:
        """
        Applies a binary mask to the input frame, replacing the background with bg_color,
        crops the bounding box of the detection, and resizes the crop with padding.

        Args:
            frame: input frame in BGR format (shape HxWxC)
            detection: DetectionResult containing the bbox and mask
            target_size: target output size (height, width)
            bg_color: BGR color value for the background

        Returns:
            np.ndarray: Clean cropped object of target_size
        """
        h, w = frame.shape[:2]
        bbox = detection.bbox
        mask = detection.mask

        # Clamp bbox coordinates to image dimensions
        x1 = max(0, int(round(bbox[0])))
        y1 = max(0, int(round(bbox[1])))
        x2 = min(w, int(round(bbox[2])))
        y2 = min(h, int(round(bbox[3])))

        # Guard against degenerate bounding boxes
        if x2 <= x1 or y2 <= y1:
            return np.zeros((target_size[0], target_size[1], 3), dtype=np.uint8)

        # Ensure mask matches frame shape
        if mask.shape != (h, w):
            import cv2

            mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)

        # Mask the frame: keep object pixels, replace background with bg_color
        # mask is 2D binary (H, W), we expand it to 3D (H, W, 1) for broadcasting
        mask_3d = mask[:, :, np.newaxis]

        # Convert bg_color to numpy array of same type
        bg_arr = np.array(bg_color, dtype=np.uint8)

        # Broadcast and combine
        masked_frame = frame * mask_3d + bg_arr * (1 - mask_3d)

        # Crop the bounding box area from the masked frame
        crop = masked_frame[y1:y2, x1:x2]

        # Resize with padding to preserve aspect ratio
        clean_crop = resize_with_pad(crop, target_size, bg_color)

        return clean_crop

    def extract_batch(
        self,
        frame: np.ndarray,
        detections: list[DetectionResult],
        target_size: tuple[int, int] = (224, 224),
        bg_color: tuple[int, int, int] = (0, 0, 0),
    ) -> list[np.ndarray]:
        """
        Extracts clean crops for a list of detections from the same frame.

        Args:
            frame: input frame in BGR format
            detections: list of DetectionResult objects
            target_size: target output size
            bg_color: BGR color value for background

        Returns:
            list[np.ndarray]: list of clean crops (224x224 BGR images)
        """
        return [self.extract_clean_crop(frame, det, target_size, bg_color) for det in detections]
