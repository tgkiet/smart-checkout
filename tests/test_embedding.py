from unittest.mock import MagicMock, patch

import numpy as np
import torch

from src.embedding.arcface_head import ArcFaceHead, ProductEmbeddingModel
from src.embedding.siglip_encoder import SigLIPEncoder


@patch("src.embedding.siglip_encoder.AutoModel")
@patch("src.embedding.siglip_encoder.AutoProcessor")
def test_siglip_encoder_initialization(mock_processor, mock_model, mock_config):
    mock_model_instance = MagicMock()
    mock_model.from_pretrained.return_value = mock_model_instance

    encoder = SigLIPEncoder(mock_config.embedding)

    mock_processor.from_pretrained.assert_called_once_with("google/siglip2-base-patch16-224")
    mock_model.from_pretrained.assert_called_once_with("google/siglip2-base-patch16-224")
    assert encoder.device == "cpu"


@patch("src.embedding.siglip_encoder.AutoModel")
@patch("src.embedding.siglip_encoder.AutoProcessor")
def test_siglip_encoder_encode(mock_processor, mock_model, mock_config, sample_image):
    mock_model_instance = MagicMock()
    mock_model.from_pretrained.return_value = mock_model_instance

    # Mock processor return
    mock_processor_instance = MagicMock()
    mock_processor.from_pretrained.return_value = mock_processor_instance
    mock_processor_instance.return_value = {"pixel_values": torch.zeros((1, 3, 224, 224))}

    # Mock model features output
    mock_features = torch.randn((1, 768))
    # Standard transformers model output structure:
    # get_image_features(pixel_values=...) -> Tensor
    mock_model_instance.get_image_features.return_value = mock_features
    mock_model_instance.hasattr.return_value = True

    encoder = SigLIPEncoder(mock_config.embedding)
    emb = encoder.encode(sample_image)

    assert emb.shape == (768,)
    # Check that it's L2-normalized: sum of squares ≈ 1
    assert np.allclose(np.linalg.norm(emb), 1.0, atol=1e-5)


def test_arcface_head_logits():
    # Setup ArcFaceHead
    batch_size = 4
    embedding_dim = 16
    num_classes = 3
    margin = 0.50
    scale = 16.0

    head = ArcFaceHead(embedding_dim=embedding_dim, num_classes=num_classes, margin=margin, scale=scale)

    # Generate mock inputs
    embeddings = torch.randn(batch_size, embedding_dim)
    # L2 normalize embeddings
    embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=-1)

    labels = torch.tensor([0, 2, 1, 0], dtype=torch.long)

    logits = head(embeddings, labels)

    assert logits.shape == (batch_size, num_classes)
    # Logits should not contain NaNs
    assert not torch.isnan(logits).any()


def test_product_embedding_model_forward():
    # Setup mock backbone
    torch.nn.Linear(10, 16)  # Mocking vision tower outputs

    # Let's mock the pooler output by creating a custom callable class
    class MockBackbone(torch.nn.Module):
        def __init__(self):
            super().__init__()
            # Return standard object with pooler_output attribute

        def forward(self, pixel_values):
            class Output:
                pooler_output = torch.randn(pixel_values.size(0), 16)

            return Output()

    mock_backbone = MockBackbone()

    model = ProductEmbeddingModel(base_model=mock_backbone, embedding_dim=16, num_classes=5, margin=0.5, scale=32.0)

    pixel_values = torch.randn(2, 3, 224, 224)
    labels = torch.tensor([1, 4], dtype=torch.long)

    logits = model(pixel_values, labels)
    assert logits.shape == (2, 5)

    embeddings = model.get_embedding(pixel_values)
    assert embeddings.shape == (2, 16)
    # Norm along embedding dim should be 1
    assert torch.allclose(torch.norm(embeddings, p=2, dim=-1), torch.ones(2), atol=1e-5)
