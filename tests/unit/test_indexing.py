"""简历索引写入测试。"""

from pathlib import Path
from unittest.mock import Mock

import pytest

from app.rag.chunking import chunk_resume
from app.rag.indexing import index_resume_chunks, index_resume_fixture_directory


@pytest.mark.core_agent_tests
def test_index_resume_chunks_calls_embedding_and_store() -> None:
    """验证 chunk 到向量库的调用链路参数正确。"""

    chunks = chunk_resume(
        """个人信息
姓名：张三
专业技能
Python、FastAPI
""",
        resume_version="2026-07-v1",
    )
    embedding_model = Mock()
    embedding_model.encode.return_value = [[0.1, 0.2], [0.3, 0.4]]
    store = Mock()

    returned = index_resume_chunks(chunks, embedding_model, store)

    assert returned == chunks
    embedding_model.encode.assert_called_once_with([chunk["source_text"] for chunk in chunks])
    store.upsert_chunks.assert_called_once_with(chunks, [[0.1, 0.2], [0.3, 0.4]])


@pytest.mark.core_agent_tests
def test_index_resume_fixture_directory_batches_txt_fixtures(tmp_path: Path) -> None:
    """验证测试夹具目录中的简历文本会被逐个切分并入库。"""

    (tmp_path / "resume_a.txt").write_text("个人信息\n姓名：张三\n", encoding="utf-8")
    (tmp_path / "resume_b.txt").write_text("专业技能\nPython\n", encoding="utf-8")

    embedding_model = Mock()
    embedding_model.encode.side_effect = [[[0.1, 0.2]], [[0.3, 0.4]]]
    store = Mock()

    all_chunks = index_resume_fixture_directory(tmp_path, embedding_model, store)

    assert [chunk["resume_version"] for chunk in all_chunks] == ["resume_a", "resume_b"]
    assert [chunk["source_id"] for chunk in all_chunks] == ["resume_a.txt", "resume_b.txt"]
    assert embedding_model.encode.call_count == 2
    assert store.upsert_chunks.call_count == 2