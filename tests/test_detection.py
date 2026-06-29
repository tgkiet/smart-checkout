from unittest.mock import MagicMock, patch

import numpy as np

from src.core.data_models import DetectionResult
from src.detection.mask_processor import MaskProcessor
from src.detection.yolo_segmentor import YOLOSegmentor


@patch("src.detection.yolo_segmentor.YOLO")
def test_yolo_segmentor_initialization(mock_yolo, mock_config):
    segmentor = YOLOSegmentor(mock_config.detection)
    mock_yolo.assert_called_once_with("models/yolo11n-seg.pt")
    assert segmentor.device == "cpu"


@patch("src.detection.yolo_segmentor.YOLO")
def test_yolo_segmentor_detect(mock_yolo, mock_config, sample_image):
    mock_model_instance = MagicMock()
    mock_yolo.return_value = mock_model_instance

    mock_box = MagicMock()
    mock_box.__len__.return_value = 1
    mock_box.xyxy = MagicMock()
    mock_box.xyxy.cpu.return_value.numpy.return_value = np.array([[10, 20, 100, 200]])
    mock_box.conf.cpu.return_value.numpy.return_value = np.array([0.9])
    mock_box.cls.cpu.return_value.numpy.return_value = np.array([2])

    mock_mask = MagicMock()
    mock_mask.data = MagicMock()
    mock_mask.data.cpu.return_value.numpy.return_value = np.ones((1, 480, 640))

    mock_result = MagicMock()
    mock_result.boxes = mock_box
    mock_result.masks = mock_mask

    mock_model_instance.predict.return_value = [mock_result]

    segmentor = YOLOSegmentor(mock_config.detection)
    detections = segmentor.detect(sample_image)

    assert len(detections) == 1
    assert detections[0].bbox == [10, 20, 100, 200]
    assert detections[0].confidence == 0.9
    assert detections[0].class_id == 2
    assert detections[0].mask.shape == (480, 640)


def test_mask_processor_extract_crop(sample_image):
    processor = MaskProcessor()

    h, w = sample_image.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[100:300, 200:400] = 1

    detection = DetectionResult(bbox=[200.0, 100.0, 400.0, 300.0], mask=mask, confidence=0.95, class_id=0)

    crop = processor.extract_clean_crop(sample_image, detection, target_size=(224, 224), bg_color=(0, 0, 0))

    assert crop.shape == (224, 224, 3)
    # The middle region of the crop should contain BGR red [0, 0, 255]
    # Check that there is red in the image
    assert np.any(crop == [0, 0, 255])

    # Check that padding/outside mask is black (we masked everything outside the center square, but center square itself is inside the mask)
    # Wait, the mask is 1 inside the bbox, and 0 outside. So the entire bbox region is red and inside the mask,
    # meaning the crop should be fully red. Let's make sure it contains red.
    assert np.mean(crop[:, :, 2] > 200) > 0.9  # mostly red

    # Now let's try a mask that only covers half of the bbox
    mask_half = np.zeros((h, w), dtype=np.uint8)
    mask_half[100:300, 200:300] = 1  # Left half of bbox is masked, right half is not

    detection_half = DetectionResult(bbox=[200.0, 100.0, 400.0, 300.0], mask=mask_half, confidence=0.95, class_id=0)

    crop_half = processor.extract_clean_crop(sample_image, detection_half, target_size=(224, 224), bg_color=(0, 0, 0))
    # Left half should be red, right half should be black background
    assert np.any(crop_half[:, :100] == [0, 0, 255])
    assert np.all(crop_half[:, 200:] == [0, 0, 0])


def test_mask_processor_extract_batch(sample_image):
    processor = MaskProcessor()

    h, w = sample_image.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[100:300, 200:400] = 1

    detections = [
        DetectionResult(bbox=[200.0, 100.0, 400.0, 300.0], mask=mask, confidence=0.9, class_id=0),
        DetectionResult(bbox=[200.0, 100.0, 400.0, 300.0], mask=mask, confidence=0.8, class_id=1),
    ]

    crops = processor.extract_batch(sample_image, detections, target_size=(224, 224))

    assert len(crops) == 2
    assert crops[0].shape == (224, 224, 3)
    assert crops[1].shape == (224, 224, 3)
