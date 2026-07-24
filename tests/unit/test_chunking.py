"""简历语义切分测试。"""

from pathlib import Path

import pytest

from app.rag.chunking import chunk_resume


FIXTURE_PATH = Path("tests/fixtures/resumes/semantic_resume.txt")


@pytest.mark.core_agent_tests
def test_chunk_resume_splits_all_five_semantic_sections() -> None:
    """验证五类 section 的切分边界与 metadata 完整性。"""

    chunks = chunk_resume(FIXTURE_PATH.read_text(encoding="utf-8"), resume_id="2026-07-v1")

    assert [chunk["chunk_type"] for chunk in chunks] == [
        "profile",
        "experience",
        "experience",
        "project",
        "project",
        "skill",
        "education",
    ]
    assert chunks[0]["chunk_id"] == "2026-07-v1:profile-001"
    assert chunks[1]["chunk_id"] == "2026-07-v1:experience-001"
    assert chunks[3]["chunk_id"] == "2026-07-v1:project-001"
    assert chunks[5]["chunk_id"] == "2026-07-v1:skill-001"
    assert chunks[6]["chunk_id"] == "2026-07-v1:education-001"

    for chunk in chunks:
        assert chunk["section_type"] == chunk["chunk_type"]
        assert chunk["resume_id"] == "2026-07-v1"
        assert chunk["source_id"] == "resume-main"
        assert chunk["source_text"]
        assert chunk["start_line"] <= chunk["end_line"]


@pytest.mark.core_agent_tests
def test_chunk_resume_supports_non_standard_entry_boundaries() -> None:
    """验证非标准日期与无日期显式标签不会漏切或误切。"""

    text = """工作经历
公司：甲公司
岗位：后端开发
负责接口开发
乙公司 后端工程师 2023/07 至今
负责检索链路

项目经历
项目名称：A系统
负责调度模块
求职助手平台 2023年1月—现在
负责 Agent 编排
"""

    chunks = chunk_resume(text, resume_id="non-standard-v1", source_id="fixture.txt")

    assert [chunk["chunk_type"] for chunk in chunks] == ["experience", "experience", "project", "project"]
    assert "公司：甲公司" in chunks[0]["source_text"]
    assert "乙公司 后端工程师 2023/07 至今" in chunks[1]["source_text"]
    assert "项目名称：A系统" in chunks[2]["source_text"]
    assert "求职助手平台 2023年1月—现在" in chunks[3]["source_text"]