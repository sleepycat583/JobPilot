"""视觉模型 Provider。

本模块由简历提取服务调用，构造独立于 LangGraph 文本节点的多模态 Chat Model。
"""

from typing import Any, cast

from langchain_core.language_models.chat_models import BaseChatModel

from app.config import Settings

ChatOpenAI: Any | None = None


def build_vision_model(settings: Settings) -> BaseChatModel | None:
    """根据可选的 VISION_* 配置构造视觉模型。

    返回：OpenAI-compatible 多模态模型。未设置 VISION_BASE_URL 或 VISION_API_KEY
    时复用文本模型的连接配置；部署方可通过 VISION_* 显式隔离视觉费用与权限。
    """

    chat_openai_cls = ChatOpenAI
    if chat_openai_cls is None:
        try:
            from langchain_openai import ChatOpenAI as imported_chat_openai
        except ImportError as exc:
            raise RuntimeError("langchain_openai is required to build the vision model") from exc
        chat_openai_cls = imported_chat_openai
    return cast(BaseChatModel, chat_openai_cls(
        # 业务规则：默认复用已验证的百炼文本账号；部署方可用 VISION_* 显式隔离费用或权限。
        base_url=settings.vision_base_url or settings.base_url,
        model=settings.vision_model_name,
        api_key=settings.vision_api_key or settings.api_key,
        timeout=60,
        max_retries=0,
    ))