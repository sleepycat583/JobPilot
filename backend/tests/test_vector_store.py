import asyncio
import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.db import Base, build_engine, build_session_factory
from app.models import JobRecord, ResumeRecord
from app.services.task_runtime import TaskRuntime
from app.services.vector_store import AlibabaEmbeddingProvider, EmbeddingError, VectorStore


class FakeEmbedding:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), 1.0, 0.0] for text in texts]


def test_chroma_index_is_idempotent_and_telemetry_is_disabled(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, chroma_persist_directory=tmp_path / "chroma", llm_mode="stub")
    store = VectorStore(settings)
    store._embedding = FakeEmbedding()  # type: ignore[assignment]

    assert store._client.get_settings().anonymized_telemetry is False
    assert store.index_resume("resume-1", "sha-a", ["Java 服务治理", "Redis 缓存优化"]) == 2
    assert store.index_resume("resume-1", "sha-a", ["Java 服务治理", "Redis 缓存优化"]) == 2
    assert store._collection.count() == 2
    rows = store._collection.get(where={"resume_id": "resume-1"})
    assert sorted(rows["ids"]) == ["resume:resume-1:chunk:0", "resume:resume-1:chunk:1"]
    assert store.query_resume("Redis", "resume-1", 2)
    store.close()
    store.close()


def test_embedding_dimension_is_rejected() -> None:
    provider = AlibabaEmbeddingProvider.__new__(AlibabaEmbeddingProvider)
    provider.dimension = 3
    with pytest.raises(EmbeddingError):
        provider._validate_dimensions([[1.0, 2.0]], 1)


def test_parsed_resume_restarts_at_vector_index(tmp_path: Path) -> None:
    upload = tmp_path / "resume.txt"
    upload.write_text("Java 后端开发，负责 Redis 服务治理。", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{(tmp_path / 'app.db').as_posix()}",
        upload_dir=tmp_path / "uploads",
        mock_task_delay_seconds=0.01,
        llm_mode="openai",
    )
    engine = build_engine(settings)
    Base.metadata.create_all(engine)
    session_factory = build_session_factory(engine)
    resume_id = "resume-parsed"
    job_id = "job-index"
    with session_factory() as session:
        session.add(
            ResumeRecord(
                id=resume_id,
                version=1,
                display_name="resume",
                file_name="resume.txt",
                content_type="text/plain",
                size_bytes=upload.stat().st_size,
                file_sha256="sha-a",
                status="parsed",
                structured_json=json.dumps({"profile": "已有结构化结果"}),
            )
        )
        session.add(JobRecord(id=job_id, kind="resume_parse", status="queued", payload_json=json.dumps({"resume_id": resume_id, "upload_path": str(upload)})))
        session.commit()

    class Service:
        def parse_resume(self, *_args, **_kwargs):
            raise AssertionError("parsed resumes must not call the LLM again")

    class Store:
        def __init__(self) -> None:
            self.calls = 0

        def index_resume(self, *_args):
            self.calls += 1
            return 1

        def has_resume_index(self, _resume_id: str) -> bool:
            return False

    store = Store()
    runtime = TaskRuntime(session_factory, settings, vector_store=store)  # type: ignore[arg-type]
    runtime.task_service = Service()  # type: ignore[assignment]
    asyncio.run(runtime.process_resume(job_id))

    with session_factory() as session:
        resume = session.get(ResumeRecord, resume_id)
        job = session.get(JobRecord, job_id)
        assert resume is not None and resume.status == "indexed"
        assert job is not None and job.status == "completed"
    assert store.calls == 1
    engine.dispose()
