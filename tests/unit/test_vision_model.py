"""视觉模型 Provider 的配置隔离与默认复用测试。"""

from __future__ import annotations

from app.config import Settings
from app.providers import vision_model


class FakeChatOpenAI:
    """记录 Provider 参数，避免测试创建真实客户端。"""

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


def _settings(**overrides: str) -> Settings:
    return Settings(
        model_provider="openai_compatible",
        base_url="https://text.example/v1",
        model_name="qwen3.7-flash",
        api_key="text-key",
        chroma_persist_dir="./data/chroma",
        embedding_device="cpu",
        **overrides,
    )


def test_vision_model_reuses_text_connection_when_no_override_is_set(monkeypatch) -> None:
    monkeypatch.setattr(vision_model, "ChatOpenAI", FakeChatOpenAI)

    model = vision_model.build_vision_model(_settings())

    assert model.kwargs["base_url"] == "https://text.example/v1"
    assert model.kwargs["api_key"] == "text-key"
    assert model.kwargs["model"] == "qwen3-vl-flash"


def test_vision_model_prefers_dedicated_connection_overrides(monkeypatch) -> None:
    monkeypatch.setattr(vision_model, "ChatOpenAI", FakeChatOpenAI)

    model = vision_model.build_vision_model(_settings(vision_base_url="https://vision.example/v1", vision_api_key="vision-key"))

    assert model.kwargs["base_url"] == "https://vision.example/v1"
    assert model.kwargs["api_key"] == "vision-key"


def test_vision_model_uses_configured_timeout_and_disables_sdk_retries(monkeypatch) -> None:
    monkeypatch.setattr(vision_model, "ChatOpenAI", FakeChatOpenAI)

    model = vision_model.build_vision_model(_settings(vision_timeout_seconds=17))

    assert model.kwargs["timeout"] == 17
    assert model.kwargs["max_retries"] == 0