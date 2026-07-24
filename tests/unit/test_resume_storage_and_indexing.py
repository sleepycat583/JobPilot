"""简历原始文件保存与索引编排服务测试。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db import Base, build_session_factory, create_sqlalchemy_engine
from app.repositories.resume_versions import ResumeVersionRepository
from app.services.resume_indexing import ResumeIndexService
from app.services.resume_storage import (
    MAX_RESUME_FILE_SIZE_BYTES,
    ResumeFileValidationError,
    ResumeStorage,
)


class FakeEmbeddingModel:
    """避免测试下载真实模型的确定性 embedding 替身。"""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[float(index)] for index, _ in enumerate(texts, start=1)]


class FakeResumeStore:
    """记录索引顺序的 Chroma 替身。"""

    def __init__(self, *, fail_upsert: bool = False) -> None:
        self.operations: list[tuple[str, object]] = []
        self.fail_upsert = fail_upsert

    def delete_resume_chunks(self, resume_id: str) -> None:
        self.operations.append(("delete", resume_id))

    def upsert_chunks(self, chunks, embeddings) -> None:
        self.operations.append(("upsert", (chunks, embeddings)))
        if self.fail_upsert:
            raise RuntimeError("Chroma unavailable")


def _repository(tmp_path):
    engine = create_sqlalchemy_engine(f"sqlite:///{(tmp_path / 'business.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    return engine, build_session_factory(engine)


def _create_version(repository: ResumeVersionRepository, storage: ResumeStorage, *, resume_id: str) -> None:
    """建立索引服务测试所需的已保存 pending 简历版本。"""

    content = "个人信息\n姓名：测试\n专业技能\nPython\n".encode("utf-8")
    repository.create_version(
        resume_id=resume_id,
        file_name="resume.txt",
        file_size=len(content),
        storage_path=storage.save(resume_id=resume_id, content=content),
        idempotency_key="00000000-0000-4000-8000-000000000101",
        request_fingerprint="a" * 64,
        now=datetime(2026, 7, 24, tzinfo=timezone.utc),
    )


def test_storage_validates_frozen_txt_rules_and_saves_original_content(tmp_path) -> None:
    storage = ResumeStorage(tmp_path / "resumes")

    validated = storage.validate(file_name="../resume.TXT", content="简历内容".encode("utf-8"))
    path = storage.save(resume_id="00000000-0000-4000-8000-000000000001", content=validated.content)

    assert validated.file_name == "resume.TXT"
    assert storage.read_text(path) == "简历内容"
    with pytest.raises(ResumeFileValidationError) as unsupported:
        storage.validate(file_name="resume.pdf", content=b"x")
    assert unsupported.value.code == "RESUME_FILE_TYPE_UNSUPPORTED"
    with pytest.raises(ResumeFileValidationError) as too_large:
        storage.validate(file_name="resume.txt", content=b"x" * (MAX_RESUME_FILE_SIZE_BYTES + 1))
    assert too_large.value.code == "RESUME_FILE_TOO_LARGE"


def test_index_service_deletes_old_chunks_before_upsert_and_marks_version_indexed(tmp_path) -> None:
    engine, factory = _repository(tmp_path)
    try:
        with factory() as session:
            storage = ResumeStorage(tmp_path / "resumes")
            repository = ResumeVersionRepository(session)
            resume_id = "00000000-0000-4000-8000-000000000001"
            _create_version(repository, storage, resume_id=resume_id)
            store = FakeResumeStore()

            ResumeIndexService(
                repository=repository, storage=storage, store=store, embedding_model=FakeEmbeddingModel()
            ).index(resume_id=resume_id)

            assert [operation[0] for operation in store.operations] == ["delete", "upsert"]
            assert store.operations[0][1] == resume_id
            assert repository.get(resume_id=resume_id).index_status == "indexed"
    finally:
        engine.dispose()


def test_index_service_marks_version_failed_when_chroma_write_fails(tmp_path) -> None:
    engine, factory = _repository(tmp_path)
    try:
        with factory() as session:
            storage = ResumeStorage(tmp_path / "resumes")
            repository = ResumeVersionRepository(session)
            resume_id = "00000000-0000-4000-8000-000000000001"
            _create_version(repository, storage, resume_id=resume_id)

            ResumeIndexService(
                repository=repository,
                storage=storage,
                store=FakeResumeStore(fail_upsert=True),
                embedding_model=FakeEmbeddingModel(),
            ).index(resume_id=resume_id)

            failed = repository.get(resume_id=resume_id)
            assert failed.index_status == "failed"
            assert failed.error_code == "RESUME_INDEX_FAILED"
    finally:
        engine.dispose()