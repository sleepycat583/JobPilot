"""简历索引任务编排服务。

本模块被后续 FastAPI 后台任务调用：它读取已保存的原始文本，先清理同一 `resume_id`
的 Chroma chunk，再重新索引并更新 SQLite 状态；不解析 HTTP 请求或管理线程。
"""

from __future__ import annotations

from typing import Any

from app.rag.chunking import chunk_resume
from app.rag.indexing import index_resume_chunks
from app.repositories.resume_versions import ResumeVersionRepository
from app.services.resume_storage import ResumeFileValidationError, ResumeStorage


class ResumeIndexService:
    """将原始文件转换为可检索 Chroma chunk 的服务。"""

    def __init__(self, *, repository: ResumeVersionRepository, storage: ResumeStorage, store: Any, embedding_model: Any) -> None:
        self._repository = repository
        self._storage = storage
        self._store = store
        self._embedding_model = embedding_model

    def index(self, *, resume_id: str, mark_started: bool = True) -> None:
        """为指定简历重建向量索引并回写终态。

        参数：
            resume_id: 需要建立或重试索引的简历资源 UUIDv4。
            mark_started: 为 `True` 时将 `pending/failed` 切换到 `indexing`；重试 API
                已同步抢占状态时传 `False`，避免并发请求都被受理。
        返回：
            无返回值；最终状态由 Repository 持久化为 `indexed` 或 `failed`。

        Chroma 与 SQLite 没有跨库事务。为让失败重试最终收敛，先按资源删除旧 chunk，
        再写入完整新索引；任意阶段失败都会保留 `failed` 状态供用户重试。
        """

        version = (
            self._repository.mark_indexing(resume_id=resume_id)
            if mark_started
            else self._repository.get(resume_id=resume_id)
        )
        if version is None or version.index_status != "indexing":
            return
        try:
            text = self._storage.read_text(version.storage_path)
            chunks = chunk_resume(text, resume_id=resume_id, source_id=version.file_name)
            if not chunks:
                raise ResumeFileValidationError("RESUME_TEXT_EMPTY", "Resume text produced no indexable chunks")
            self._store.delete_resume_chunks(resume_id)
            index_resume_chunks(chunks, self._embedding_model, self._store)
        except ResumeFileValidationError as error:
            self._repository.mark_failed(
                resume_id=resume_id, error_code=error.code, error_message=str(error)
            )
            return
        except Exception as error:
            self._repository.mark_failed(
                resume_id=resume_id, error_code="RESUME_INDEX_FAILED", error_message=str(error)
            )
            return
        self._repository.mark_indexed(resume_id=resume_id)