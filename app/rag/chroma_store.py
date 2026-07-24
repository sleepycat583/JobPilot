"""Chroma 存储与查询适配层。

本文件负责初始化固定 collection、维护冻结 metadata，并提供查询与批量写入接口。
它被 indexing.py 和后续简历匹配流程调用。

已知限制：当前只做 collection metadata 层的模型名/维度一致性校验，
未做运行时实际向量长度校验；若后续需要更严格的一致性保证，应在写入和查询时额外校验
embedding 向量长度是否等于 `EMBEDDING_DIMENSION`。
"""

from __future__ import annotations

from typing import Any, TypedDict

from app.config import Settings
from app.constants import (
    CHROMA_COLLECTION_NAME,
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    RAG_RELEVANCE_THRESHOLD,
    RAG_TOP_K,
)
from app.rag.chunking import ResumeChunk

PersistentClient: Any | None = None


class ChromaQueryResult(TypedDict):
    """检索返回结果。"""

    chunk_id: str
    quote: str
    relevance: float


class ResumeNotFoundError(ValueError):
    """指定简历资源不存在时抛出的稳定错误。"""

    def __init__(self, resume_id: str) -> None:
        self.code = "RESUME_NOT_FOUND"
        self.resume_id = resume_id
        super().__init__(f"Resume not found: {resume_id}")


class ChromaResumeStore:
    """简历 Chroma 访问适配器。"""

    def __init__(self, settings: Settings, embedding_model: Any) -> None:
        """初始化 Chroma PersistentClient 与固定 collection。

        参数：
            settings: 应用配置，仅使用 `chroma_persist_dir`。
            embedding_model: 已创建的 embedding provider，需支持 `encode(list[str])`。
        """

        self._settings = settings
        self._embedding_model = embedding_model
        client_class = PersistentClient
        if client_class is None:
            try:
                from chromadb import PersistentClient as imported_client
            except ImportError as exc:
                raise RuntimeError("chromadb is required to use the Chroma resume store") from exc
            client_class = imported_client

        self._client = client_class(path=settings.chroma_persist_dir)
        self._collection = self._client.get_or_create_collection(
            name=CHROMA_COLLECTION_NAME,
            metadata={
                "embedding_model": EMBEDDING_MODEL,
                "embedding_dimension": EMBEDDING_DIMENSION,
                "hnsw:space": "cosine",
            },
        )
        self._validate_collection_metadata()

    def upsert_chunks(self, chunks: list[ResumeChunk], embeddings: list[list[float]]) -> None:
        """批量写入 chunk 向量。

        参数：
            chunks: 已切分好的简历 chunk。
            embeddings: 与 chunks 一一对应的向量列表。
        """

        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have the same length")
        if not chunks:
            return

        self._collection.upsert(
            ids=[chunk["chunk_id"] for chunk in chunks],
            documents=[chunk["source_text"] for chunk in chunks],
            embeddings=embeddings,
            metadatas=[dict(chunk) for chunk in chunks],
        )

    def query(self, query_text: str, resume_id: str) -> list[ChromaQueryResult]:
        """执行固定 top-k、版本隔离与阈值过滤的检索。

        参数：
            query_text: 查询文本。
            resume_id: 目标简历资源，仅允许在该资源内检索。

        返回：
            relevance 不低于阈值的证据列表，保留 chunk_id、quote 和 relevance。
        """

        self._validate_collection_metadata()
        self._ensure_resume_id_exists(resume_id)
        vectors = self._embedding_model.encode([query_text])
        result = self._collection.query(
            query_embeddings=vectors,
            n_results=RAG_TOP_K,
            include=["documents", "metadatas", "distances"],
            where={"resume_id": resume_id},
        )

        rows: list[ChromaQueryResult] = []
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]

        for document, metadata, distance in zip(documents, metadatas, distances, strict=False):
            relevance = _distance_to_relevance(distance)
            if relevance < RAG_RELEVANCE_THRESHOLD:
                continue
            rows.append(
                ChromaQueryResult(
                    chunk_id=str(metadata["chunk_id"]),
                    quote=str(document),
                    relevance=relevance,
                )
            )

        return rows

    def delete_resume_chunks(self, resume_id: str) -> None:
        """删除指定简历资源的全部 chunk。

        参数：
            resume_id: 需要清理的简历资源 UUIDv4。

        返回：
            无返回值。删除不存在的资源按 Chroma 幂等语义处理。

        重试索引必须先删除旧 chunk，防止文本变更后残留旧 chunk 被同一资源检索到。
        """

        self._collection.delete(where={"resume_id": resume_id})

    def _ensure_resume_id_exists(self, resume_id: str) -> None:
        """确认目标简历资源已存在，避免查询时无过滤回退到其他资源。

        这里先用 Chroma metadata 过滤做存在性检查；若不存在，立即抛出稳定错误码，
        由上层 Agent 转换成 LangGraph 的正常 state update。
        """

        lookup = self._collection.get(where={"resume_id": resume_id}, limit=1)
        ids = lookup.get("ids", [])
        if not ids:
            raise ResumeNotFoundError(resume_id)

    @property
    def collection_metadata(self) -> dict[str, Any]:
        """暴露 collection metadata，便于健康检查与测试验证。"""

        return dict(getattr(self._collection, "metadata", {}) or {})

    def _validate_collection_metadata(self) -> None:
        metadata = self.collection_metadata
        if metadata.get("embedding_model") != EMBEDDING_MODEL:
            raise RuntimeError("Chroma collection embedding model mismatch, rebuild index required")
        if metadata.get("embedding_dimension") != EMBEDDING_DIMENSION:
            raise RuntimeError("Chroma collection embedding dimension mismatch, rebuild index required")


def _distance_to_relevance(distance: float) -> float:
    """把 Chroma cosine distance 换算为 [0, 1] 相关度。

    这里依赖 collection metadata 中固定的 `hnsw:space=cosine`。Chroma 的 cosine distance
    等价于 `1 - cosine_similarity`，理论范围约为 [0, 2]：越小越相近。
    因此使用 `1 - distance` 恢复到“越大越相关”的分数，再夹紧到 [0, 1]。
    若未来改成 L2 / inner product 等距离度量，必须同步修改此换算逻辑与测试。
    """

    return max(0.0, min(1.0, 1.0 - float(distance)))