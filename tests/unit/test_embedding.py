"""Embedding Provider 测试。"""

import inspect

import pytest

from app.config import Settings
from app.constants import EMBEDDING_MODEL
from app.providers import embedding


@pytest.mark.core_agent_tests
def test_build_embedding_model_uses_fixed_bge_m3(monkeypatch: pytest.MonkeyPatch) -> None:
    """Embedding Provider 必须固定使用 app.constants 中的模型名。"""

    captured: dict[str, object] = {}

    class FakeEmbeddingModel:
        pass

    def fake_sentence_transformer(model_name: str, *, device: str) -> FakeEmbeddingModel:
        captured["model_name"] = model_name
        captured["device"] = device
        return FakeEmbeddingModel()

    monkeypatch.setattr(embedding, "SentenceTransformer", fake_sentence_transformer)

    settings = Settings.model_construct(
        model_provider="openai_compatible",
        base_url="https://api.deepseek.com/v1",
        model_name="deepseek-chat",
        api_key="test-key",
        chroma_persist_dir="./data/chroma",
        embedding_device="cpu",
    )

    model = embedding.build_embedding_model(settings)

    assert isinstance(model, FakeEmbeddingModel)
    assert captured["model_name"] == EMBEDDING_MODEL
    assert captured["device"] == "cpu"


@pytest.mark.core_agent_tests
def test_build_embedding_model_does_not_expose_model_name_parameter() -> None:
    """接口层不应暴露可切换模型名参数。"""

    signature = inspect.signature(embedding.build_embedding_model)

    assert list(signature.parameters.keys()) == ["settings"]
    assert "model_name" not in signature.parameters
