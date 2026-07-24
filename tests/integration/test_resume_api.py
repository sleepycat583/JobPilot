"""简历上传 HTTP API 集成测试。

测试通过临时 SQLite、临时原始文件目录和 Fake Chroma/Embedding 验证上传契约，
不访问真实模型、Chroma 服务或网络。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.api import AppDependencies, create_app
from app.db import Base, build_session_factory, create_sqlalchemy_engine
from app.services.resume_storage import ResumeStorage


class FakeGraph:
    """简历库路由不调用 Graph，保留最小依赖占位。"""


class FakeEmbeddingModel:
    """避免测试加载真实 embedding 模型。"""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[float(index)] for index, _ in enumerate(texts, start=1)]


class FakeResumeStore:
    """记录先删后写顺序的 Chroma 替身。"""

    def __init__(self, *, fail_upsert: bool = False) -> None:
        self.operations: list[str] = []
        self.fail_upsert = fail_upsert

    def delete_resume_chunks(self, resume_id: str) -> None:
        self.operations.append(f"delete:{resume_id}")

    def upsert_chunks(self, chunks, embeddings) -> None:
        self.operations.append("upsert")
        if self.fail_upsert:
            raise RuntimeError("Chroma unavailable")


def _client(tmp_path: Path, *, fail_upsert: bool = False) -> tuple[TestClient, FakeResumeStore]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    engine = create_sqlalchemy_engine(f"sqlite:///{(tmp_path / 'app.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    store = FakeResumeStore(fail_upsert=fail_upsert)
    dependencies = AppDependencies(
        graph=FakeGraph(),
        session_factory=build_session_factory(engine),
        resume_store=store,
        embedding_model=FakeEmbeddingModel(),
        resume_storage=ResumeStorage(tmp_path / "resumes"),
        close=engine.dispose,
    )
    return TestClient(create_app(dependencies=dependencies)), store


def _upload(client: TestClient, *, key: str, content: bytes = b"Profile\nPython\n", name: str = "resume.txt"):
    """以冻结 multipart 契约上传一份测试简历。"""

    return client.post(
        "/v1/resumes",
        headers={"Idempotency-Key": key},
        files={"file": (name, content, "text/plain")},
    )


def test_upload_indexes_resume_then_lists_and_reads_status(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    with client:
        response = _upload(client, key="00000000-0000-4000-8000-000000000101")
        assert response.status_code == 202
        payload = response.json()
        assert payload["display_version"] == 1
        assert payload["index_status"] == "pending"

        status = client.get(f"/v1/resumes/{payload['resume_id']}")
        assert status.status_code == 200
        assert status.json()["index_status"] == "indexed"
        assert store.operations == [f"delete:{payload['resume_id']}", "upsert"]

        listing = client.get("/v1/resumes")
        assert listing.status_code == 200
        assert [item["resume_id"] for item in listing.json()["resumes"]] == [payload["resume_id"]]


def test_upload_replays_same_key_and_rejects_different_file(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    key = "00000000-0000-4000-8000-000000000101"
    with client:
        initial = _upload(client, key=key)
        replay = _upload(client, key=key)
        conflict = _upload(client, key=key, content=b"Different\n", name="other.txt")

        assert replay.status_code == 202
        assert replay.json()["resume_id"] == initial.json()["resume_id"]
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "RESUME_UPLOAD_IDEMPOTENCY_KEY_REUSED"


def test_upload_rejects_invalid_key_and_file_rules(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        invalid_key = _upload(client, key="not-a-uuid")
        unsupported = _upload(client, key="00000000-0000-4000-8000-000000000101", name="resume.pdf")
        blank = _upload(client, key="00000000-0000-4000-8000-000000000102", content=b"   \n")

        assert invalid_key.json()["error"]["code"] == "IDEMPOTENCY_KEY_INVALID"
        assert unsupported.json()["error"]["code"] == "RESUME_FILE_TYPE_UNSUPPORTED"
        assert blank.json()["error"]["code"] == "RESUME_TEXT_EMPTY"


def test_failed_index_can_be_retried_and_indexed_resume_rejects_retry(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, fail_upsert=True)
    with client:
        uploaded = _upload(client, key="00000000-0000-4000-8000-000000000101")
        resume_id = uploaded.json()["resume_id"]
        assert client.get(f"/v1/resumes/{resume_id}").json()["index_status"] == "failed"

        retry = client.post(f"/v1/resumes/{resume_id}/retry")
        assert retry.status_code == 202

    successful_client, _ = _client(tmp_path / "successful")
    with successful_client:
        indexed = _upload(successful_client, key="00000000-0000-4000-8000-000000000102")
        conflict = successful_client.post(f"/v1/resumes/{indexed.json()['resume_id']}/retry")

        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "RESUME_INDEX_CONFLICT"