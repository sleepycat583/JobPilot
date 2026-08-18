from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    database_path = (tmp_path / "test.db").as_posix()
    upload_path = (tmp_path / "uploads").as_posix()
    graph_checkpoint_path = (tmp_path / "langgraph.db").as_posix()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    monkeypatch.setenv("UPLOAD_DIR", upload_path)
    monkeypatch.setenv("GRAPH_CHECKPOINT_PATH", graph_checkpoint_path)
    monkeypatch.setenv("LLM_MODE", "stub")
    monkeypatch.setenv("AUTO_CREATE_SCHEMA", "true")
    monkeypatch.setenv("MOCK_TASK_DELAY_SECONDS", "0.01")
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()
