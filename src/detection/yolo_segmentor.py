import numpy as np
import torch
from ultralytics import YOLO

from src.core.config import DetectionConfig
from src.core.data_models import DetectionResult
from src.core.logger import get_logger

logger = get_logger(__name__)


class YOLOSegmentor:
    def __init__(self, config: DetectionConfig):
        self.config = config
        self.device = config.device

        # Fallback to CPU if CUDA is requested but not available
        if self.device == "cuda" and not torch.cuda.is_available():
            logger.warning("CUDA requested but not available. Falling back to CPU.")
            self.device = "cpu"

        logger.info("Loading YOLO segmentation model", model_path=config.model_path, device=self.device)
        self.model = YOLO(config.model_path)
        # Move model to device (YOLO handles this automatically during predict, but good to set)
        self.model.to(self.device)

    def detect(self, frame: np.ndarray) -> list[DetectionResult]:
        """
        Runs YOLOv11-seg inference on a BGR image (numpy array) and extracts predictions.

        Args:
            frame: input image in BGR format (numpy array, shape HxWxC)

        Returns:
            list[DetectionResult]: list of detected objects with bboxes and binary masks.
        """
        # Run inference
        results = self.model.predict(
            source=frame,
            conf=self.config.confidence_threshold,
            iou=self.config.iou_threshold,
            max_det=50,
            device=self.device,
            retina_masks=True,
            verbose=False,
        )

        detection_results = []
        if not results:
            return detection_results

        result = results[0]  # Single image batch

        if result.boxes is None or len(result.boxes) == 0:
            return detection_results

        # Boxes and class/conf metadata
        boxes_data = result.boxes.xyxy.cpu().numpy()  # [N, 4] -> [x1, y1, x2, y2]
        confs = result.boxes.conf.cpu().numpy()  # [N]
        classes = result.boxes.cls.cpu().numpy().astype(int)  # [N]

        # If no masks are available, instance segmentation cannot proceed.
        # Fallback: create virtual bounding-box masks if masks is None but boxes exist.
        has_masks = result.masks is not None and result.masks.data is not None and len(result.masks.data) > 0

        frame_h, frame_w = frame.shape[:2]
        if has_masks:
            # masks.data is of shape (N, H, W)
            masks_data = result.masks.data.cpu().numpy()
        else:
            # Create dummy binary masks corresponding to the bounding box region
            masks_data = []
            for box in boxes_data:
                dummy_mask = np.zeros((frame_h, frame_w), dtype=np.uint8)
                x1, y1, x2, y2 = map(int, box)
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(frame_w, x2), min(frame_h, y2)
                dummy_mask[y1:y2, x1:x2] = 1
                masks_data.append(dummy_mask)
            masks_data = np.stack(masks_data)

        for i in range(len(boxes_data)):
            # Normalize mask to binary uint8 (0 or 1)
            mask_binary = (masks_data[i] > 0.5).astype(np.uint8)

            # The mask returned by YOLO might be resized to the network size rather than original frame size.
            # Double check shape consistency and resize if needed.
            import cv2

            if mask_binary.shape != (frame_h, frame_w):
                # Resize using nearest neighbor to preserve binary values
                mask_binary = cv2.resize(mask_binary, (frame_w, frame_h), interpolation=cv2.INTER_NEAREST)

            # Compute tight bounding box directly from the mask pixels (more accurate than YOLO's predicted box)
            rows = np.any(mask_binary, axis=1)
            cols = np.any(mask_binary, axis=0)
            if rows.any() and cols.any():
                y_min = int(np.where(rows)[0][0])
                y_max = int(np.where(rows)[0][-1]) + 1
                x_min = int(np.where(cols)[0][0])
                x_max = int(np.where(cols)[0][-1]) + 1
                tight_bbox = [float(x_min), float(y_min), float(x_max), float(y_max)]
            else:
                tight_bbox = boxes_data[i].tolist()

            detection_results.append(
                DetectionResult(bbox=tight_bbox, mask=mask_binary, confidence=float(confs[i]), class_id=int(classes[i]))
            )

        return detection_results

    def detect_batch(self, frames: list[np.ndarray]) -> list[list[DetectionResult]]:
        """
        Runs YOLOv11-seg inference on a batch of BGR images and extracts predictions.

        Args:
            frames: list of input images in BGR format

        Returns:
            list[list[DetectionResult]]: list of detected objects per input frame
        """
        if not frames:
            return []

        # Run inference on the entire batch
        results = self.model.predict(
            source=frames,
            conf=self.config.confidence_threshold,
            iou=self.config.iou_threshold,
            max_det=50,
            device=self.device,
            retina_masks=True,
            verbose=False,
        )

        import cv2

        batch_detection_results = []
        for idx, result in enumerate(results):
            detection_results = []
            if result.boxes is None or len(result.boxes) == 0:
                batch_detection_results.append(detection_results)
                continue

            # Boxes and class/conf metadata
            boxes_data = result.boxes.xyxy.cpu().numpy()  # [N, 4] -> [x1, y1, x2, y2]
            confs = result.boxes.conf.cpu().numpy()  # [N]
            classes = result.boxes.cls.cpu().numpy().astype(int)  # [N]

            # If no masks are available, instance segmentation cannot proceed.
            has_masks = result.masks is not None and result.masks.data is not None and len(result.masks.data) > 0

            frame_h, frame_w = frames[idx].shape[:2]
            if has_masks:
                masks_data = result.masks.data.cpu().numpy()
            else:
                # Create dummy binary masks corresponding to the bounding box region
                masks_data = []
                for box in boxes_data:
                    dummy_mask = np.zeros((frame_h, frame_w), dtype=np.uint8)
                    x1, y1, x2, y2 = map(int, box)
                    x1, y1 = max(0, x1), max(0, y1)
                    x2, y2 = min(frame_w, x2), min(frame_h, y2)
                    dummy_mask[y1:y2, x1:x2] = 1
                    masks_data.append(dummy_mask)
                masks_data = np.stack(masks_data)

            for i in range(len(boxes_data)):
                # Normalize mask to binary uint8 (0 or 1)
                mask_binary = (masks_data[i] > 0.5).astype(np.uint8)

                # Resize if shape is inconsistent
                if mask_binary.shape != (frame_h, frame_w):
                    mask_binary = cv2.resize(mask_binary, (frame_w, frame_h), interpolation=cv2.INTER_NEAREST)

                # Compute tight bounding box directly from the mask pixels
                rows = np.any(mask_binary, axis=1)
                cols = np.any(mask_binary, axis=0)
                if rows.any() and cols.any():
                    y_min = int(np.where(rows)[0][0])
                    y_max = int(np.where(rows)[0][-1]) + 1
                    x_min = int(np.where(cols)[0][0])
                    x_max = int(np.where(cols)[0][-1]) + 1
                    tight_bbox = [float(x_min), float(y_min), float(x_max), float(y_max)]
                else:
                    tight_bbox = boxes_data[i].tolist()

                detection_results.append(
                    DetectionResult(
                        bbox=tight_bbox, mask=mask_binary, confidence=float(confs[i]), class_id=int(classes[i])
                    )
                )
            batch_detection_results.append(detection_results)

        return batch_detection_results

    def warmup(self) -> None:
        """Runs a dummy inference on a blank image to warm up GPU/CPU caching."""
        logger.info("Warming up YOLO model...")
        dummy_frame = np.zeros((640, 640, 3), dtype=np.uint8)
        self.detect(dummy_frame)
        logger.info("YOLO model warmup complete.")
