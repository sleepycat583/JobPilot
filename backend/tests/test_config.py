import pytest

from app.core.config import Settings, validate_startup_settings


def test_startup_validation_requires_openai_key() -> None:
    settings = Settings(_env_file=None, llm_mode="openai", openai_api_key=" ")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        validate_startup_settings(settings)


def test_startup_validation_accepts_default_local_stub() -> None:
    validate_startup_settings(Settings(_env_file=None, llm_mode="stub"))
