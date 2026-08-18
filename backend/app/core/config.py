from functools import lru_cache
import os
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "Career Agent Workbench API"
    app_env: str = "development"
    api_prefix: str = "/api"
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'app.db').as_posix()}"
    upload_dir: Path = BACKEND_DIR / "data" / "uploads"
    frontend_origin: str = "http://127.0.0.1:5173"
    sse_heartbeat_seconds: int = Field(default=15, ge=5, le=60)
    mock_task_delay_seconds: float = Field(default=0.15, ge=0.01, le=3.0)
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    graph_checkpoint_path: Path = BACKEND_DIR / "data" / "langgraph.db"
    llm_mode: Literal["stub", "openai"] = "stub"
    openai_model: str = "gpt-4.1-mini"
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    langsmith_tracing: bool = False
    langsmith_project: str = "career-agent-workbench"
    langsmith_api_key: SecretStr | None = None
    auto_create_schema: bool = False

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def configure_langsmith(settings: Settings) -> None:
    """Expose tracing settings to LangChain without ever logging credential values."""

    if not settings.langsmith_tracing:
        return
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    if settings.langsmith_api_key is not None:
        api_key = settings.langsmith_api_key.get_secret_value().strip()
        if api_key:
            os.environ["LANGSMITH_API_KEY"] = api_key
