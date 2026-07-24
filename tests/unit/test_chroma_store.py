"""Chroma 存储适配层测试。"""

import pytest

from app.config import Settings
from app.constants import (
    CHROMA_COLLECTION_NAME,
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    RAG_RELEVANCE_THRESHOLD,
    RAG_TOP_K,
)
from app.rag import chroma_store


class FakeCollection:
    def __init__(self, metadata: dict[str, object] | None = None) -> None:
        self.metadata = metadata or {
            "embedding_model": EMBEDDING_MODEL,
            "embedding_dimension": EMBEDDING_DIMENSION,
            "hnsw:space": "cosine",
        }
        self.query_calls: list[dict[str, object]] = []
        self.upsert_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []

    def query(self, **kwargs: object) -> dict[str, list[list[object]]]:
        self.query_calls.append(kwargs)
        return {
            "documents": [["strong evidence", "borderline evidence", "filtered evidence", "negative evidence"]],
            "metadatas": [[
                {"chunk_id": "project-001"},
                {"chunk_id": "experience-002"},
                {"chunk_id": "skill-001"},
                {"chunk_id": "profile-001"},
            ]],
            "distances": [[0.2, 0.65, 0.8, 1.2]],
        }

    def get(self, **kwargs: object) -> dict[str, list[str]]:
        self.get_calls.append(kwargs)
        where = kwargs.get("where", {})
        if where == {"resume_id": "missing-v1"}:
            return {"ids": []}
        return {"ids": ["project-001"]}

    def upsert(self, **kwargs: object) -> None:
        self.upsert_calls.append(kwargs)


class FakeClient:
    def __init__(self, *, path: str, collection: FakeCollection, recorder: dict[str, object]) -> None:
        recorder["path"] = path
        self._collection = collection
        self._recorder = recorder

    def get_or_create_collection(self, *, name: str, metadata: dict[str, object]) -> FakeCollection:
        self._recorder["name"] = name
        self._recorder["metadata"] = metadata
        return self._collection


class FakeEmbeddingModel:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[0.1, 0.2, 0.3] for _ in texts]


def build_settings() -> Settings:
    return Settings.model_construct(
        model_provider="openai_compatible",
        base_url="https://api.example.com/v1",
        model_name="deepseek-chat",
        api_key="test-key",
        chroma_persist_dir="./data/chroma",
        embedding_device="cpu",
    )


@pytest.mark.core_agent_tests
def test_chroma_store_initializes_persistent_client_with_frozen_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 Chroma client 路径、collection 名称与 metadata 固定值。"""

    recorder: dict[str, object] = {}
    collection = FakeCollection()

    def fake_persistent_client(*, path: str) -> FakeClient:
        return FakeClient(path=path, collection=collection, recorder=recorder)

    monkeypatch.setattr(chroma_store, "PersistentClient", fake_persistent_client)

    store = chroma_store.ChromaResumeStore(build_settings(), FakeEmbeddingModel())

    assert store.collection_metadata == {
        "embedding_model": EMBEDDING_MODEL,
        "embedding_dimension": EMBEDDING_DIMENSION,
        "hnsw:space": "cosine",
    }
    assert recorder == {
        "path": "./data/chroma",
        "name": CHROMA_COLLECTION_NAME,
        "metadata": {
            "embedding_model": EMBEDDING_MODEL,
            "embedding_dimension": EMBEDDING_DIMENSION,
            "hnsw:space": "cosine",
        },
    }


@pytest.mark.core_agent_tests
def test_chroma_store_query_applies_top_k_and_relevance_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 query 固定使用 top_k=5，并按 relevance>=0.35 过滤结果。"""

    recorder: dict[str, object] = {}
    collection = FakeCollection()
    embedding_model = FakeEmbeddingModel()

    def fake_persistent_client(*, path: str) -> FakeClient:
        return FakeClient(path=path, collection=collection, recorder=recorder)

    monkeypatch.setattr(chroma_store, "PersistentClient", fake_persistent_client)

    store = chroma_store.ChromaResumeStore(build_settings(), embedding_model)
    results = store.query("java spring boot", "2026-07-v1")

    assert embedding_model.calls == [["java spring boot"]]
    assert collection.get_calls == [{"where": {"resume_id": "2026-07-v1"}, "limit": 1}]
    assert collection.query_calls == [
        {
            "query_embeddings": [[0.1, 0.2, 0.3]],
            "n_results": RAG_TOP_K,
            "include": ["documents", "metadatas", "distances"],
            "where": {"resume_id": "2026-07-v1"},
        }
    ]
    assert results == [
        {"chunk_id": "project-001", "quote": "strong evidence", "relevance": 0.8},
        {"chunk_id": "experience-002", "quote": "borderline evidence", "relevance": RAG_RELEVANCE_THRESHOLD},
    ]


@pytest.mark.core_agent_tests
def test_chroma_store_missing_resume_id_fails_without_embedding_or_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证不存在的 resume_id 直接报稳定错误，不继续做向量检索。"""

    recorder: dict[str, object] = {}
    collection = FakeCollection()
    embedding_model = FakeEmbeddingModel()

    def fake_persistent_client(*, path: str) -> FakeClient:
        return FakeClient(path=path, collection=collection, recorder=recorder)

    monkeypatch.setattr(chroma_store, "PersistentClient", fake_persistent_client)

    store = chroma_store.ChromaResumeStore(build_settings(), embedding_model)

    with pytest.raises(chroma_store.ResumeNotFoundError) as exc_info:
        store.query("java spring boot", "missing-v1")

    assert exc_info.value.code == "RESUME_NOT_FOUND"
    assert embedding_model.calls == []
    assert collection.query_calls == []
    assert collection.get_calls == [{"where": {"resume_id": "missing-v1"}, "limit": 1}]


@pytest.mark.core_agent_tests
def test_chroma_store_rejects_mismatched_embedding_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 collection metadata 与冻结模型不一致时直接失败。"""

    collection = FakeCollection(metadata={"embedding_model": "other-model", "embedding_dimension": 768})

    def fake_persistent_client(*, path: str) -> FakeClient:
        return FakeClient(path=path, collection=collection, recorder={})

    monkeypatch.setattr(chroma_store, "PersistentClient", fake_persistent_client)

    with pytest.raises(RuntimeError, match="rebuild index required"):
        chroma_store.ChromaResumeStore(build_settings(), FakeEmbeddingModel())