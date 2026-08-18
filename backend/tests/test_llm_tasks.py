from app.graph.models import StubSupervisorModel
from app.services.documents import make_text_chunks
from app.services.llm_tasks import InterviewReport, LLMTaskService, StructuredMatch, privacy_filter_text


def test_privacy_filter_removes_email_and_mainland_phone() -> None:
    filtered = privacy_filter_text("联系 me@example.com 或 13800138000")
    assert "me@example.com" not in filtered
    assert "13800138000" not in filtered
    assert "[EMAIL_REDACTED]" in filtered
    assert "[PHONE_REDACTED]" in filtered


def test_text_chunking_is_bounded_and_overlapping() -> None:
    chunks = make_text_chunks("A" * 2_500, size=1_000, overlap=100)
    assert len(chunks) == 3
    assert all(0 < len(chunk) <= 1_000 for chunk in chunks)
    assert chunks[0][-100:] == chunks[1][:100]


def test_match_score_is_derived_from_bounded_dimensions(monkeypatch) -> None:
    service = LLMTaskService(StubSupervisorModel())
    raw = StructuredMatch(
        total_score=99,
        dimension_scores={
            "必备技能": 80,
            "核心职责": 22,
            "加分技能": -5,
            "硬性条件": 8,
            "证据质量": 6,
        },
        strengths=["技能匹配"],
        gaps=[],
        evidence_count=0,
        evidence=[],
    )
    monkeypatch.setattr(service, "_structured", lambda *_args, **_kwargs: raw)

    result = service.match({}, {})

    assert result.dimension_scores == {
        "必备技能": 40.0,
        "核心职责": 22.0,
        "加分技能": 0.0,
        "硬性条件": 8.0,
        "证据质量": 6.0,
    }
    assert result.total_score == 76.0
    assert result.low_score_review_required is False


def test_interview_report_is_normalized_for_chinese_ui(monkeypatch) -> None:
    service = LLMTaskService(StubSupervisorModel())
    raw = InterviewReport(
        overall_score=68,
        summary="总结 " * 100,
        dimension_scores={
            "technical_depth": 55,
            "evidence_specificity": 120,
            "jd_alignment": 70,
            "communication_clarity": -3,
            "unexpected": 90,
        },
        actions=["行动一 " * 100, "行动二"],
    )
    monkeypatch.setattr(service, "_structured", lambda *_args, **_kwargs: raw)

    result = service.report("综合面试", [], {}, {})

    assert result.dimension_scores == {
        "技术深度": 55.0,
        "证据充分度": 100.0,
        "岗位匹配度": 70.0,
        "表达清晰度": 0.0,
    }
    assert len(result.summary) == 160
    assert len(result.actions[0]) == 180
    assert len(result.actions) == 2
