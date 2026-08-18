from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any

import httpx

from app.core.config import Settings


class EmbeddingError(RuntimeError):
    """An embedding request failed or returned an invalid shape."""


class VectorIndexNotFound(LookupError):
    """The requested resume has not completed vector indexing."""


@dataclass(frozen=True)
class RetrievedChunk:
    id: str
    text: str
    distance: float | None
    metadata: dict[str, Any]


class AlibabaEmbeddingProvider:
    """Small provider for DashScope's native text embedding endpoint.

    The configured OpenAI-compatible endpoint is intentionally not reused here:
    DashScope's embedding API expects ``input.texts`` rather than ``input``.
    """

    def __init__(self, settings: Settings) -> None:
        self.model = settings.embedding_model
        self.dimension = settings.embedding_dimension
        self.batch_size = settings.embedding_batch_size
        self.api_key = settings.openai_api_key.get_secret_value().strip() if settings.openai_api_key else ""
        base_url = (settings.openai_base_url or "").rstrip("/")
        suffix = "/compatible-mode/v1"
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)]
        self.endpoint = f"{base_url}/api/v1/services/embeddings/text-embedding/text-embedding"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.api_key or not self.endpoint.startswith(("http://", "https://")):
            raise EmbeddingError("Embedding provider is not configured")
        vectors: list[list[float]] = []
        try:
            with httpx.Client(timeout=60.0) as client:
                for start in range(0, len(texts), self.batch_size):
                    batch = texts[start : start + self.batch_size]
                    response = client.post(
                        self.endpoint,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json={"model": self.model, "input": {"texts": batch}},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    vectors.extend(self._parse_vectors(payload, expected_count=len(batch)))
        except EmbeddingError:
            raise
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            raise EmbeddingError("Embedding request failed") from exc
        self._validate_dimensions(vectors, len(texts))
        return vectors

    def _parse_vectors(self, payload: Any, *, expected_count: int) -> list[list[float]]:
        output = payload.get("output") if isinstance(payload, dict) else None
        values = output.get("embeddings") if isinstance(output, dict) else None
        if values is None and isinstance(output, dict):
            values = output.get("data")
        if values is None and isinstance(payload, dict):
            values = payload.get("data")
        if not isinstance(values, list) or len(values) != expected_count:
            raise EmbeddingError("Embedding response count is invalid")
        if values and isinstance(values[0], dict):
            values = sorted(values, key=lambda item: item.get("text_index", item.get("index", 0)))
            values = [item.get("embedding") for item in values]
        if not all(isinstance(item, list) for item in values):
            raise EmbeddingError("Embedding response shape is invalid")
        return [[float(value) for value in item] for item in values]

    def _validate_dimensions(self, vectors: list[list[float]], expected_count: int) -> None:
        if len(vectors) != expected_count or any(len(vector) != self.dimension for vector in vectors):
            raise EmbeddingError("Embedding dimension is invalid")


class VectorStore:
    def __init__(self, settings: Settings) -> None:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        if settings.chroma_backend == "local":
            settings.chroma_persist_directory.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=str(settings.chroma_persist_directory),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        else:
            if not settings.chroma_host:
                raise RuntimeError("CHROMA_HOST is required when CHROMA_BACKEND=http")
            headers = None
            if settings.chroma_api_key is not None:
                api_key = settings.chroma_api_key.get_secret_value().strip()
                if api_key:
                    headers = {"Authorization": f"Bearer {api_key}"}
            self._client = chromadb.HttpClient(
                host=settings.chroma_host,
                port=settings.chroma_port,
                ssl=settings.chroma_ssl,
                headers=headers,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        self._collection = self._client.get_or_create_collection(
            name=settings.chroma_collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        self._embedding = AlibabaEmbeddingProvider(settings)
        self._lock = threading.RLock()

    def index_resume(self, resume_id: str, file_sha256: str, chunks: list[str]) -> int:
        if not chunks:
            raise ValueError("Resume has no indexable text")
        documents = [chunk.strip() for chunk in chunks if chunk and chunk.strip()]
        if not documents:
            raise ValueError("Resume has no indexable text")
        ids = [f"resume:{resume_id}:chunk:{index}" for index in range(len(documents))]
        metadatas = [
            {"resume_id": resume_id, "chunk_index": index, "file_sha256": file_sha256}
            for index in range(len(documents))
        ]
        embeddings = self._embedding.embed_documents(documents)
        with self._lock:
            self._collection.delete(where={"resume_id": resume_id})
            self._collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=embeddings,
            )
        return len(documents)

    def has_resume_index(self, resume_id: str) -> bool:
        with self._lock:
            result = self._collection.get(where={"resume_id": resume_id}, limit=1)
        return bool(result.get("ids"))

    def query_resume(self, query_text: str, resume_id: str, top_k: int = 8) -> list[RetrievedChunk]:
        if not self.has_resume_index(resume_id):
            raise VectorIndexNotFound(resume_id)
        query = query_text.strip()
        if not query:
            return []
        query_embedding = self._embedding.embed_documents([query])[0]
        with self._lock:
            result = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=max(1, top_k),
                where={"resume_id": resume_id},
                include=["documents", "metadatas", "distances"],
            )
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        return [
            RetrievedChunk(
                id=str(chunk_id),
                text=str(documents[index]),
                distance=float(distances[index]) if index < len(distances) and distances[index] is not None else None,
                metadata=dict(metadatas[index] or {}) if index < len(metadatas) else {},
            )
            for index, chunk_id in enumerate(ids)
            if index < len(documents)
        ]
