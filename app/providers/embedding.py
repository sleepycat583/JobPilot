"""Embedding Provider。

本文件只负责创建固定使用 BAAI/bge-m3 的 Embedding 实例，
不暴露可切换模型名的接口，避免破坏冻结架构中的向量空间一致性。
"""

from typing import Any

from app.config import Settings
from app.constants import EMBEDDING_MODEL

# 采用延迟导入：测试环境不需要真实下载模型权重，也能通过 monkeypatch 验证固定模型名与 device。
SentenceTransformer: Any | None = None


def build_embedding_model(settings: Settings) -> Any:
    """构建固定模型名的 Embedding 实例。

    参数：
        settings: 已通过校验的应用配置，仅使用其中的 embedding_device。

    返回：
        固定使用 BAAI/bge-m3 的 SentenceTransformer 实例。
    """

    sentence_transformer_cls = SentenceTransformer
    if sentence_transformer_cls is None:
        try:
            from sentence_transformers import SentenceTransformer as imported_sentence_transformer
        except ImportError as exc:
            raise RuntimeError("sentence_transformers is required to build the embedding model") from exc
        sentence_transformer_cls = imported_sentence_transformer

    return sentence_transformer_cls(EMBEDDING_MODEL, device=settings.embedding_device)
