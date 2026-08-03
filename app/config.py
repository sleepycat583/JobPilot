"""应用配置加载与启动自检。

本文件只负责读取第 2 阶段需要的环境配置，并在应用启动前尽早失败。
它不创建 Chat Model、Embedding Provider 或任何业务对象，避免越过当前步骤边界。
"""

from typing import Any

from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用运行配置。

    所有字段都对应文档 §2.4 和 §12.7 中当前阶段要求的环境变量。
    这些字段不提供默认值，确保缺项时在启动阶段立即报错。
    """

    model_provider: str
    base_url: str
    model_name: str
    api_key: str
    vision_model_name: str = "qwen3-vl-flash"
    vision_base_url: str = ""
    vision_api_key: str = ""
    chroma_persist_dir: str
    sqlalchemy_database_url: str = "sqlite:///./data/app.sqlite3"
    langgraph_checkpoint_path: str = "./data/checkpoints.sqlite3"
    embedding_device: str
    log_dir: str = "./logs"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    @field_validator(
        "model_provider",
        "base_url",
        "model_name",
        "api_key",
        "chroma_persist_dir",
        "sqlalchemy_database_url",
        "langgraph_checkpoint_path",
        "embedding_device",
        "log_dir",
        "log_level",
    )
    @classmethod
    def validate_not_blank(cls, value: str) -> str:
        """拒绝仅包含空白字符的配置值。

        这样做是为了避免环境变量“看起来存在，实际不可用”的情况延迟到运行时才暴露。
        """

        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


def load_settings(**overrides: Any) -> Settings:
    """加载并校验应用配置。

    参数：
        **overrides: 传递给 Pydantic Settings 的覆盖参数。
        主要用于测试阶段显式禁用 `.env` 文件读取，避免本机环境污染测试结果。

    返回：
        已通过 Pydantic 校验的 Settings 对象。

    异常：
        RuntimeError: 缺少必填配置项或配置值非法时抛出，错误信息中包含具体字段名。
    """

    try:
        return Settings(**overrides)
    except ValidationError as exc:
        details: list[str] = []
        for error in exc.errors():
            field_path = ".".join(str(part) for part in error["loc"])
            details.append(f"{field_path}: {error['msg']}")
        detail_text = "; ".join(details)
        raise RuntimeError(f"Invalid application settings: {detail_text}") from exc


def run_startup_self_check(settings: Settings) -> None:
    """执行当前阶段允许的启动自检。

    当前阶段只验证配置已完整加载且关键字符串不为空，不创建外部服务连接。

    参数：
        settings: 已加载的应用配置。

    异常：
        RuntimeError: 检查到空白配置字段时抛出，并指出具体字段名。
    """

    blank_fields = [
        field_name
        for field_name in (
            "model_provider",
            "base_url",
            "model_name",
            "api_key",
            "chroma_persist_dir",
            "sqlalchemy_database_url",
            "langgraph_checkpoint_path",
            "embedding_device",
            "log_dir",
            "log_level",
        )
        if not getattr(settings, field_name).strip()
    ]

    if blank_fields:
        joined_fields = ", ".join(blank_fields)
        raise RuntimeError(f"Startup self-check failed, blank settings: {joined_fields}")
