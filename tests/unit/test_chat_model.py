"""聊天模型 Provider 测试。"""

import pytest

from app.config import Settings
from app.providers import chat_model


@pytest.mark.core_agent_tests
def test_build_chat_model_rejects_invalid_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """非法 MODEL_PROVIDER 应立即失败，且不构造底层模型。"""

    called = {"value": False}

    def fake_chat_openai(**_: object) -> object:
        called["value"] = True
        return object()

    monkeypatch.setattr(chat_model, "ChatOpenAI", fake_chat_openai)

    settings = Settings.model_construct(
        model_provider="unsupported",
        base_url="https://api.deepseek.com/v1",
        model_name="deepseek-chat",
        api_key="test-key",
        chroma_persist_dir="./data/chroma",
        embedding_device="cpu",
    )

    with pytest.raises(ValueError, match="Unsupported MODEL_PROVIDER"):
        chat_model.build_chat_model(settings)

    assert called["value"] is False


@pytest.mark.core_agent_tests
def test_build_chat_model_passes_expected_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """合法配置应把 timeout 和 max_retries 等参数正确传给 ChatOpenAI。"""

    captured: dict[str, object] = {}

    class FakeChatModel:
        pass

    def fake_chat_openai(**kwargs: object) -> FakeChatModel:
        captured.update(kwargs)
        return FakeChatModel()

    monkeypatch.setattr(chat_model, "ChatOpenAI", fake_chat_openai)

    settings = Settings.model_construct(
        model_provider="openai_compatible",
        base_url="https://api.deepseek.com/v1",
        model_name="deepseek-chat",
        api_key="test-key",
        chroma_persist_dir="./data/chroma",
        embedding_device="cpu",
    )

    model = chat_model.build_chat_model(settings)

    assert isinstance(model, FakeChatModel)
    assert captured["base_url"] == "https://api.deepseek.com/v1"
    assert captured["model"] == "deepseek-chat"
    assert captured["api_key"] == "test-key"
    assert captured["timeout"] == 30
    assert captured["max_retries"] == 0
