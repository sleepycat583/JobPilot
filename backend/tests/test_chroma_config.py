import chromadb
import pytest

from app.core.config import Settings
from app.services.vector_store import VectorStore


def test_http_chroma_requires_host() -> None:
    with pytest.raises(RuntimeError, match="CHROMA_HOST"):
        VectorStore(Settings(_env_file=None, chroma_backend="http", llm_mode="stub"))


def test_http_chroma_client_receives_shared_service_config(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeCollection:
        pass

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def get_or_create_collection(self, **_kwargs: object) -> FakeCollection:
            return FakeCollection()

    monkeypatch.setattr(chromadb, "HttpClient", FakeClient)
    VectorStore(
        Settings(
            _env_file=None,
            chroma_backend="http",
            chroma_host="chroma.internal",
            chroma_port=8443,
            chroma_ssl=True,
            llm_mode="stub",
        )
    )
    assert captured["host"] == "chroma.internal"
    assert captured["port"] == 8443
    assert captured["ssl"] is True
