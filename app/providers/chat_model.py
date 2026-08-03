"""聊天模型 Provider。

本文件只负责把已校验的 Settings 转换为 LangChain BaseChatModel 实例，
不包含任何业务路由或节点逻辑。
"""

from typing import Any, cast

from langchain_core.language_models.chat_models import BaseChatModel

from app.config import Settings

# 采用延迟导入：测试环境不需要预先安装具体 Provider 依赖，也能通过 monkeypatch 验证构造参数。
ChatOpenAI: Any | None = None


def build_chat_model(settings: Settings) -> BaseChatModel:
    """构建聊天模型实例。

    参数：
        settings: 已通过校验的应用配置。

    返回：
        LangChain 抽象层的 BaseChatModel 实例。

    异常：
        ValueError: 当 MODEL_PROVIDER 不是 openai_compatible 时抛出。
    """

    if settings.model_provider != "openai_compatible":
        raise ValueError(f"Unsupported MODEL_PROVIDER: {settings.model_provider}")

    chat_openai_cls = ChatOpenAI
    if chat_openai_cls is None:
        try:
            from langchain_openai import ChatOpenAI as imported_chat_openai
        except ImportError as exc:
            raise RuntimeError("langchain_openai is required to build the chat model") from exc
        chat_openai_cls = imported_chat_openai

    return cast(BaseChatModel, chat_openai_cls(
        base_url=settings.base_url,
        model=settings.model_name,
        api_key=settings.api_key,
        timeout=30,
        # 百炼 Qwen3 的 thinking mode 不接受 LangChain 结构化输出附带的 tool_choice=required。
        # 现有 Agent 依赖 Schema 门卫，因此文本链路统一关闭 thinking，保留工具式结构化输出。
        extra_body={"enable_thinking": False},
        # Provider 层禁用透明重试，避免与第⑤步节点级结构化输出重试叠加，导致调用次数失控。
        max_retries=0,
    ))
