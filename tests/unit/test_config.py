"""配置加载与启动自检测试。"""

from pathlib import Path

import pytest

from app.config import Settings, load_settings, run_startup_self_check


def _clear_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清理本测试关心的环境变量，避免真实本机环境污染结果。"""

    for env_name in (
        "MODEL_PROVIDER",
        "BASE_URL",
        "MODEL_NAME",
        "API_KEY",
        "CHROMA_PERSIST_DIR",
        "SQLALCHEMY_DATABASE_URL",
        "LANGGRAPH_CHECKPOINT_PATH",
        "EMBEDDING_DEVICE",
    ):
        monkeypatch.delenv(env_name, raising=False)


@pytest.mark.core_agent_tests
def test_load_settings_fails_when_required_field_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺少必填配置时，启动阶段应立即失败并指出具体缺失项。"""

    _clear_required_env(monkeypatch)
    monkeypatch.setenv("BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("MODEL_NAME", "deepseek-chat")
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("CHROMA_PERSIST_DIR", "./data/chroma")
    monkeypatch.setenv("EMBEDDING_DEVICE", "cpu")

    with pytest.raises(RuntimeError, match="model_provider") as exc_info:
        load_settings(_env_file=None)

    assert "model_provider" in str(exc_info.value)


@pytest.mark.core_agent_tests
def test_load_settings_succeeds_when_all_required_fields_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置齐全时应能正常加载。"""

    _clear_required_env(monkeypatch)
    monkeypatch.setenv("MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("MODEL_NAME", "deepseek-chat")
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("CHROMA_PERSIST_DIR", "./data/chroma")
    monkeypatch.setenv("EMBEDDING_DEVICE", "cpu")

    settings = load_settings(_env_file=None)

    assert settings.model_provider == "openai_compatible"
    assert settings.base_url == "https://api.deepseek.com/v1"
    assert settings.model_name == "deepseek-chat"
    assert settings.api_key == "test-key"
    assert settings.chroma_persist_dir == "./data/chroma"
    assert settings.sqlalchemy_database_url == "sqlite:///./data/app.sqlite3"
    assert settings.langgraph_checkpoint_path == "./data/checkpoints.sqlite3"
    assert settings.embedding_device == "cpu"
    assert settings.log_dir == "./logs"
    assert settings.log_level == "INFO"


@pytest.mark.core_agent_tests
def test_startup_self_check_fails_for_blank_values() -> None:
    """即使字段存在，只要是空白字符串，自检也应明确失败。"""

    settings = Settings.model_construct(
        model_provider="openai_compatible",
        base_url="https://api.deepseek.com/v1",
        model_name="deepseek-chat",
        api_key="   ",
        chroma_persist_dir=str(Path("./data/chroma")),
        sqlalchemy_database_url="sqlite:///./data/app.sqlite3",
        langgraph_checkpoint_path="./data/checkpoints.sqlite3",
        embedding_device="cpu",
    )

    with pytest.raises(RuntimeError, match="api_key") as exc_info:
        run_startup_self_check(settings)

    assert "api_key" in str(exc_info.value)


@pytest.mark.core_agent_tests
def test_startup_self_check_passes_for_loaded_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置完整且非空白时，自检应通过。"""

    _clear_required_env(monkeypatch)
    monkeypatch.setenv("MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("MODEL_NAME", "deepseek-chat")
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("CHROMA_PERSIST_DIR", "./data/chroma")
    monkeypatch.setenv("EMBEDDING_DEVICE", "cpu")

    settings = load_settings(_env_file=None)

    run_startup_self_check(settings)
