from unittest.mock import MagicMock, patch

import numpy as np

from src.core.data_models import CheckoutResult
from src.pipeline.checkout_pipeline import CheckoutPipeline


@patch("src.detection.yolo_segmentor.YOLO")
@patch("src.embedding.siglip_encoder.AutoModel")
@patch("src.embedding.siglip_encoder.AutoProcessor")
@patch("src.database.milvus_client.MilvusClient")
def test_checkout_pipeline_empty(
    mock_milvus_client, mock_processor, mock_model, mock_yolo, mock_config, sample_image, temp_metadata_file
):
    # Setup mocks
    mock_config.data.sku_metadata_path = str(temp_metadata_file)

    mock_yolo_instance = MagicMock()
    mock_yolo.return_value = mock_yolo_instance
    mock_yolo_instance.predict.return_value = []  # Nothing detected

    mock_milvus_instance = MagicMock()
    mock_milvus_client.return_value = mock_milvus_instance
    mock_milvus_instance.has_collection.return_value = True

    # Initialize pipeline
    with patch("src.core.config.load_config", return_value=mock_config):
        pipeline = CheckoutPipeline()
        result = pipeline.process_frame(sample_image, weight_grams=0.0)

        assert isinstance(result, CheckoutResult)
        assert len(result.items) == 0
        assert result.total_price == 0.0
        assert result.scale_weight == 0.0
        assert result.weight_match is True


@patch("src.detection.yolo_segmentor.YOLO")
@patch("src.embedding.siglip_encoder.AutoModel")
@patch("src.embedding.siglip_encoder.AutoProcessor")
@patch("src.database.milvus_client.MilvusClient")
def test_checkout_pipeline_success(
    mock_milvus_client, mock_processor, mock_model, mock_yolo, mock_config, sample_image, temp_metadata_file
):
    mock_config.data.sku_metadata_path = str(temp_metadata_file)

    import torch

    # 1. Mock YOLO detector -> 1 detection
    mock_yolo_instance = MagicMock()
    mock_yolo.return_value = mock_yolo_instance

    mock_box = MagicMock()
    mock_box.__len__.return_value = 1
    mock_box.xyxy = MagicMock()
    mock_box.xyxy.cpu.return_value.numpy.return_value = np.array([[200, 100, 400, 300]])
    mock_box.conf.cpu.return_value.numpy.return_value = np.array([0.95])
    mock_box.cls.cpu.return_value.numpy.return_value = np.array([0])

    mock_mask = MagicMock()
    mock_mask.data = MagicMock()
    mock_mask.data.cpu.return_value.numpy.return_value = np.ones((1, 480, 640))

    mock_det_result = MagicMock()
    mock_det_result.boxes = mock_box
    mock_det_result.masks = mock_mask
    mock_yolo_instance.predict.return_value = [mock_det_result]

    # 2. Mock SigLIP Encoder
    mock_model_instance = MagicMock()
    mock_model.from_pretrained.return_value = mock_model_instance
    mock_processor_instance = MagicMock()
    mock_processor.from_pretrained.return_value = mock_processor_instance
    mock_processor_instance.return_value = {"pixel_values": torch.zeros((1, 3, 224, 224))}

    mock_features = torch.randn((1, 768))
    mock_model_instance.get_image_features.return_value = mock_features
    mock_model_instance.hasattr.return_value = True

    # 3. Mock Milvus db search -> matches SKU001 (best similarity 0.99)
    mock_milvus_instance = MagicMock()
    mock_milvus_client.return_value = mock_milvus_instance
    mock_milvus_instance.has_collection.return_value = True

    # SKU001: Coca Cola 390ml (400g)
    mock_milvus_instance.search.return_value = [
        [{"distance": 0.99, "entity": {"sku_id": "SKU001", "view_type": "front"}}]
    ]

    with patch("src.core.config.load_config", return_value=mock_config):
        pipeline = CheckoutPipeline()

        # 400g matches Coca Cola (400g)
        result = pipeline.process_frame(sample_image, weight_grams=400.0)

        assert isinstance(result, CheckoutResult)
        assert len(result.items) == 1
        assert result.items[0].sku_id == "SKU001"
        assert result.items[0].sku_name == "Coca Cola 390ml"
        assert result.items[0].quantity == 1
        assert result.total_price == 10000.0
        assert result.scale_weight == 400.0
        assert result.weight_match is True


@patch("src.detection.yolo_segmentor.YOLO")
@patch("src.embedding.siglip_encoder.AutoModel")
@patch("src.embedding.siglip_encoder.AutoProcessor")
@patch("src.database.milvus_client.MilvusClient")
def test_checkout_pipeline_bypass_scale(
    mock_milvus_client, mock_processor, mock_model, mock_yolo, mock_config, sample_image, temp_metadata_file
):
    mock_config.data.sku_metadata_path = str(temp_metadata_file)

    import torch

    mock_yolo_instance = MagicMock()
    mock_yolo.return_value = mock_yolo_instance

    mock_box = MagicMock()
    mock_box.__len__.return_value = 1
    mock_box.xyxy = MagicMock()
    mock_box.xyxy.cpu.return_value.numpy.return_value = np.array([[200, 100, 400, 300]])
    mock_box.conf.cpu.return_value.numpy.return_value = np.array([0.95])
    mock_box.cls.cpu.return_value.numpy.return_value = np.array([0])

    mock_mask = MagicMock()
    mock_mask.data = MagicMock()
    mock_mask.data.cpu.return_value.numpy.return_value = np.ones((1, 480, 640))

    mock_det_result = MagicMock()
    mock_det_result.boxes = mock_box
    mock_det_result.masks = mock_mask
    mock_yolo_instance.predict.return_value = [mock_det_result]

    mock_model_instance = MagicMock()
    mock_model.from_pretrained.return_value = mock_model_instance
    mock_processor_instance = MagicMock()
    mock_processor.from_pretrained.return_value = mock_processor_instance
    mock_processor_instance.return_value = {"pixel_values": torch.zeros((1, 3, 224, 224))}

    mock_features = torch.randn((1, 768))
    mock_model_instance.get_image_features.return_value = mock_features
    mock_model_instance.hasattr.return_value = True

    mock_milvus_instance = MagicMock()
    mock_milvus_client.return_value = mock_milvus_instance
    mock_milvus_instance.has_collection.return_value = True

    # Matches SKU001 (weight 400g) but we pass mismatched scale weight 999.0g and use_scale=False
    mock_milvus_instance.search.return_value = [
        [{"distance": 0.99, "entity": {"sku_id": "SKU001", "view_type": "front"}}]
    ]

    with patch("src.core.config.load_config", return_value=mock_config):
        pipeline = CheckoutPipeline()

        # When use_scale=False, the mismatched 999g should be ignored, and result.weight_match should be True
        result = pipeline.process_frame(sample_image, weight_grams=999.0, use_scale=False)

        assert isinstance(result, CheckoutResult)
        assert len(result.items) == 1
        assert result.items[0].sku_id == "SKU001"
        assert result.scale_weight == 0.0
        assert result.weight_match is True
