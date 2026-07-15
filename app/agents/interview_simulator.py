"""模拟面试 Worker 的可独立测试业务函数。

本模块由后续 Graph 节点调用：它复用结构化输出重试服务生成计划、题目、评价和复盘叙述，
并把所有可验证事实交给 Schema 门卫和 interview_scoring 服务处理。本提交不注册 Graph 节点。
"""

from __future__ import annotations

from typing import Any, Literal

from app.schemas.interview import (
    AnswerEvaluation,
    InterviewPlanOutput,
    InterviewReport,
    InterviewReportNarrative,
    InterviewState,
    InterviewTopicPlan,
    QuestionProposal,
    QuestionRecord,
)
from app.schemas.jd import JDParsed
from app.schemas.resume import MatchResult
from app.services.interview_scoring import CompletionReason, build_completion_metadata, calculate_interview_score
from app.services.structured_output import StructuredPromptContext, StructuredOutputResult, call_with_structured_output


QuestionMode = Literal["primary", "retry_same_question", "clarification_follow_up", "evidence_follow_up"]


def build_interview_plan(
    chat_model: Any,
    *,
    user_goal: str,
    jd_parsed: JDParsed | None = None,
    match_result: MatchResult | None = None,
    target_question_count: int = 8,
) -> StructuredOutputResult[InterviewPlanOutput]:
    """按 JD/匹配结果或独立目标生成受限主题计划。"""

    source = _plan_source(jd_parsed, match_result, user_goal)
    prompt = (
        "You are planning a mock interview. Return only JSON matching InterviewPlanOutput. "
        "Create 1..15 unique topics. Use basis=jd only for supplied JD facts, basis=match_result only for supplied match signals, "
        "and basis=user_goal/general for independent requests. Mark core topics needed for the target interview.\n"
        f"Target primary question count: {target_question_count}\n{source}"
    )
    result = call_with_structured_output(
        chat_model,
        InterviewPlanOutput,
        StructuredPromptContext(full_prompt=prompt, minimal_input=source),
        "interview_plan",
    )
    if result.value is not None:
        _validate_plan_sources(result.value.plan, jd_parsed is not None, match_result is not None)
    return result


def initialize_interview_state(plan_output: InterviewPlanOutput, *, target_question_count: int = 8) -> InterviewState:
    """将已校验计划转换为可持久化的初始 InterviewState。"""

    return InterviewState(
        status="planning",
        target_question_count=target_question_count,
        current_question_id=None,
        question_records=[],
        user_context_updates=[],
        report=None,
        plan=plan_output.plan,
    )


def ask_question(
    chat_model: Any,
    state: InterviewState,
    *,
    mode: QuestionMode = "primary",
    follow_up_of: str | None = None,
) -> StructuredOutputResult[QuestionRecord]:
    """生成下一题，并由程序分配题号与追问关联。"""

    if mode == "primary":
        allowed_topics = [topic.topic for topic in state.plan]
    else:
        if follow_up_of is None:
            raise ValueError("follow_up_of is required for non-primary interview questions")
        parent = _find_record(state.question_records, follow_up_of)
        allowed_topics = [parent.topic]
    if not allowed_topics:
        raise ValueError("interview plan must contain an allowed topic")

    history = [{"question_id": item.question_id, "topic": item.topic, "question": item.question} for item in state.question_records]
    prompt = (
        "You are asking the next mock interview question. Return only JSON matching QuestionProposal. "
        "Do not repeat or paraphrase historical questions. The topic must be one of Allowed topics.\n"
        f"Mode: {mode}\nAllowed topics: {allowed_topics}\nHistory: {history}\nContext updates: {state.user_context_updates}"
    )
    raw_result = call_with_structured_output(
        chat_model,
        QuestionProposal,
        StructuredPromptContext(full_prompt=prompt, minimal_input=f"Allowed topics: {allowed_topics}; mode: {mode}"),
        "ask_question",
    )
    if raw_result.value is None:
        return StructuredOutputResult(value=None, retry_count=raw_result.retry_count, error_log=raw_result.error_log, degraded=True)
    if raw_result.value.topic not in allowed_topics:
        raise ValueError("question topic must be allowed by the plan or follow-up parent")
    if any(_normalize_question(item.question) == _normalize_question(raw_result.value.question) for item in state.question_records):
        raise ValueError("question must not duplicate an existing question")
    question_id = f"q-{len(state.question_records) + 1}"
    record = QuestionRecord(
        question_id=question_id,
        topic=raw_result.value.topic,
        question=raw_result.value.question,
        answer="",
        follow_up_of=follow_up_of,
        scores={},
        feedback="",
        strengths=[],
        issues=[],
    )
    return StructuredOutputResult(value=record, retry_count=raw_result.retry_count, error_log=raw_result.error_log, degraded=False)


def evaluate_answer(chat_model: Any, record: QuestionRecord, *, user_goal: str = "") -> StructuredOutputResult[QuestionRecord]:
    """评价当前题，并把 LLM 输出与程序持有的题答事实合并为 QuestionRecord。"""

    if not record.answer.strip():
        raise ValueError("cannot evaluate an empty interview answer")
    prompt = (
        "You are evaluating one mock interview answer. Return only JSON matching AnswerEvaluation. "
        "Judge only the provided question, answer, and target; do not invent resume facts.\n"
        f"Target: {user_goal}\nQuestion: {record.question}\nAnswer: {record.answer}"
    )
    raw_result = call_with_structured_output(
        chat_model,
        AnswerEvaluation,
        StructuredPromptContext(full_prompt=prompt, minimal_input=f"Question: {record.question}\nAnswer: {record.answer}"),
        "evaluate_answer",
    )
    if raw_result.value is None:
        return StructuredOutputResult(value=None, retry_count=raw_result.retry_count, error_log=raw_result.error_log, degraded=True)
    evaluation = raw_result.value
    merged = QuestionRecord.model_validate(
        {
            **record.model_dump(),
            "scores": evaluation.scores,
            "feedback": evaluation.feedback,
            "strengths": evaluation.strengths,
            "issues": evaluation.issues,
            "answer_relevance": evaluation.answer_relevance,
            "fatal_error": evaluation.fatal_error,
            "fatal_error_reason": evaluation.fatal_error_reason,
        }
    )
    return StructuredOutputResult(value=merged, retry_count=raw_result.retry_count, error_log=raw_result.error_log, degraded=False)


def generate_review_report(
    chat_model: Any,
    state: InterviewState,
    *,
    completion_reason: CompletionReason,
    jd_parsed: JDParsed | None = None,
) -> StructuredOutputResult[InterviewReport]:
    """先确定性聚合事实，再让 LLM 只生成可追溯复盘叙述。"""

    score = calculate_interview_score(state.question_records)
    metadata = build_completion_metadata(state.plan, state.question_records, completion_reason)
    allowed_ids = {record.question_id for record in state.question_records}
    prompt = (
        "You are writing a mock interview review. Return only JSON matching InterviewReportNarrative. "
        "Use only listed question IDs. A recurring weakness must occur in at least two questions or be tied to fatal_error=true. "
        "Each review action must include a concrete study topic, practice action, and verification.\n"
        f"Deterministic score: {score.dimension_scores}; overall: {score.overall_score}; metadata: {metadata}; "
        f"JD focus: {_jd_focus(jd_parsed)}; records: {_report_records(state.question_records)}"
    )
    raw_result = call_with_structured_output(
        chat_model,
        InterviewReportNarrative,
        StructuredPromptContext(full_prompt=prompt, minimal_input=f"Question IDs: {sorted(allowed_ids)}; score: {score.overall_score}"),
        "generate_review_report",
    )
    if raw_result.value is None:
        return StructuredOutputResult(value=None, retry_count=raw_result.retry_count, error_log=raw_result.error_log, degraded=True)
    narrative = raw_result.value
    _validate_report_references(narrative, allowed_ids)
    report = InterviewReport(
        overall_score=score.overall_score,
        dimension_scores=score.dimension_scores,
        performance_summary=narrative.performance_summary,
        recurring_strengths=narrative.recurring_strengths,
        recurring_weaknesses=narrative.recurring_weaknesses,
        review_actions=narrative.review_actions,
        question_references=narrative.question_references,
        completion_reason=metadata.completion_reason,
        covered_topics=metadata.covered_topics,
        uncovered_topics=metadata.uncovered_topics,
        sample_limited=metadata.sample_limited,
    )
    return StructuredOutputResult(value=report, retry_count=raw_result.retry_count, error_log=raw_result.error_log, degraded=False)


def _plan_source(jd_parsed: JDParsed | None, match_result: MatchResult | None, user_goal: str) -> str:
    if jd_parsed is not None:
        return (
            f"JD title: {jd_parsed.job_title}; skills: {[skill.model_dump() for skill in jd_parsed.skills]}; "
            f"responsibilities: {jd_parsed.responsibilities}; interview_focus: {jd_parsed.interview_focus}; "
            f"match strengths: {match_result.strengths if match_result else []}; match gaps: {match_result.gaps if match_result else []}; "
            f"user goal: {user_goal}"
        )
    return f"Independent interview request. User goal: {user_goal or 'not specified'}"


def _find_record(records: list[QuestionRecord], question_id: str) -> QuestionRecord:
    for record in records:
        if record.question_id == question_id:
            return record
    raise ValueError("follow_up_of must reference an existing question")


def _validate_plan_sources(plan: list[InterviewTopicPlan], has_jd: bool, has_match_result: bool) -> None:
    """阻止 LLM 为不存在的上游输入伪造计划来源。"""

    topic_ids = [topic.topic_id for topic in plan]
    if len(topic_ids) != len(set(topic_ids)):
        raise ValueError("interview plan topic_id values must be unique")
    if not has_jd and any(topic.basis == "jd" for topic in plan):
        raise ValueError("independent interview plans cannot use jd basis")
    if not has_match_result and any(topic.basis == "match_result" for topic in plan):
        raise ValueError("interview plans cannot use match_result basis without a match result")


def _normalize_question(question: str) -> str:
    return "".join(question.lower().split())


def _jd_focus(jd_parsed: JDParsed | None) -> list[str]:
    return jd_parsed.interview_focus if jd_parsed is not None else []


def _report_records(records: list[QuestionRecord]) -> list[dict[str, object]]:
    return [
        {
            "question_id": record.question_id,
            "question": record.question,
            "answer": record.answer,
            "scores": record.scores,
            "issues": record.issues,
            "fatal_error": record.fatal_error,
        }
        for record in records
    ]


def _validate_report_references(narrative: InterviewReportNarrative, allowed_ids: set[str]) -> None:
    references = set(narrative.question_references)
    for action in narrative.review_actions:
        references.update(action.related_questions)
    if not references.issubset(allowed_ids):
        raise ValueError("report references must use existing question IDs")