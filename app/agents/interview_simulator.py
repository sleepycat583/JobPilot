"""模拟面试 Worker 的可独立测试业务函数与 Graph 节点。

本模块由后续 Graph 节点调用：它复用结构化输出重试服务生成计划、题目、评价和复盘叙述，
并把所有可验证事实交给 Schema 门卫和 interview_scoring 服务处理。Graph builder 调用本模块的
节点适配函数，最终审核由 graph.control_nodes 的通用 Review Gate 负责。
"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.types import interrupt

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
from app.schemas.review import InterviewEvaluationUnavailableInterruptPayload
from app.services.interview_scoring import CompletionReason, build_completion_metadata, calculate_interview_score
from app.services.structured_output import StructuredPromptContext, StructuredOutputResult, call_with_structured_output


QuestionMode = Literal["primary", "retry_same_question", "clarification_follow_up", "evidence_follow_up"]


def interview_plan_node(state: dict[str, object], chat_model: Any) -> dict[str, object]:
    """Graph 入口：生成计划并初始化默认 8 题的可恢复面试状态。"""

    result = build_interview_plan(
        chat_model,
        user_goal=str(state.get("user_input", "")),
        jd_parsed=state.get("jd_parsed") if isinstance(state.get("jd_parsed"), JDParsed) else None,
        match_result=state.get("match_result") if isinstance(state.get("match_result"), MatchResult) else None,
    )
    if result.value is None:
        raise ValueError("interview plan generation degraded after structured-output retries")
    return {"current_node": "interview_plan", "interview_state": initialize_interview_state(result.value), "retry_count": {"interview_plan": result.retry_count}, "error_log": result.error_log}


def ask_question_node(state: dict[str, object], chat_model: Any) -> dict[str, object]:
    """Graph 节点：依据上一次决策生成并追加一条等待态题目。"""

    interview_state = _require_state(state)
    pending = state.get("interview_next_action", "primary")
    mode = pending if pending in {"primary", "retry_same_question", "clarification_follow_up", "evidence_follow_up"} else "primary"
    parent_id = state.get("interview_follow_up_of")
    result = ask_question(chat_model, interview_state, mode=mode, follow_up_of=parent_id if isinstance(parent_id, str) else None)
    if result.value is None:
        raise ValueError("question generation degraded after structured-output retries")
    updated = interview_state.model_copy(update={"status": "waiting", "current_question_id": result.value.question_id, "question_records": [*interview_state.question_records, result.value]})
    return {"current_node": "ask_question", "interview_state": updated, "interview_next_action": None, "interview_follow_up_of": None, "retry_count": {"ask_question": result.retry_count}, "error_log": result.error_log}


def evaluate_answer_node(state: dict[str, object], chat_model: Any) -> dict[str, object]:
    """Graph 节点：只评价当前已回答题，并保留此前所有记录。

    结构化评价耗尽内部重试后，按原题 ID 原地标记不可用，不写入猜测分数；
    后续 decision 节点让用户选择重评或跳过，重评仍会回到本节点替换同一条记录。
    """

    interview_state = _require_state(state)
    current = _find_record(interview_state.question_records, interview_state.current_question_id or "")
    result = evaluate_answer(chat_model, current, user_goal=str(state.get("user_input", "")))
    if result.value is None:
        unavailable = QuestionRecord.model_validate(
            {
                **current.model_dump(),
                "scores": {},
                "feedback": "",
                "strengths": [],
                "issues": [],
                "evaluation_status": "unavailable",
                "answer_relevance": None,
                "fatal_error": False,
                "fatal_error_reason": None,
            }
        )
        updated_records = [unavailable if item.question_id == current.question_id else item for item in interview_state.question_records]
        return {
            "current_node": "evaluate_answer",
            "interview_state": interview_state.model_copy(update={"status": "evaluating", "question_records": updated_records}),
            "retry_count": {"evaluate_answer": result.retry_count},
            "error_log": result.error_log,
        }
    updated_records = [result.value if item.question_id == current.question_id else item for item in interview_state.question_records]
    return {"current_node": "evaluate_answer", "interview_state": interview_state.model_copy(update={"status": "evaluating", "question_records": updated_records}), "retry_count": {"evaluate_answer": result.retry_count}, "error_log": result.error_log}


def interview_decision_node(state: dict[str, object]) -> dict[str, object]:
    """Graph 节点：调用确定性规则，将评价转换为受限的下一跳动作。

    本节点不调用 LLM 或外部服务。评价不可用时可以安全地通过 interrupt 等待用户
    选择：重评复用原答案返回评价节点，跳过则继续同一套确定性决策规则。
    """

    from app.services.interview_scoring import decide_next_interview_action

    interview_state = _require_state(state)
    current = interview_state.question_records[-1] if interview_state.question_records else None
    if current is not None and current.evaluation_status == "unavailable":
        payload = InterviewEvaluationUnavailableInterruptPayload(
            type="interview_evaluation_unavailable",
            question_id=current.question_id,
            accepted_actions=["retry_evaluation", "skip_evaluation"],
        )
        user_choice = interrupt(payload.model_dump(mode="json"))
        action = user_choice.get("action") if isinstance(user_choice, dict) else None
        if action == "retry_evaluation":
            return {
                "current_node": "interview_decision",
                "interview_next_action": "retry_evaluation",
                "interview_completion_reason": None,
                "interview_follow_up_of": None,
            }
        if action != "skip_evaluation":
            raise ValueError("Unsupported evaluation-unavailable action")
    decision = decide_next_interview_action(interview_state, current_question_retried=bool(current and current.follow_up_of))
    return {"current_node": "interview_decision", "interview_next_action": decision.action, "interview_completion_reason": decision.completion_reason, "interview_follow_up_of": current.question_id if current and decision.action != "next_topic" and decision.action != "finish" else None}


def generate_review_report_node(state: dict[str, object], chat_model: Any) -> dict[str, object]:
    """Graph 节点：只重建复盘报告，并把草稿送入通用最终核可 Gate。

    参数：
        state: 含已完成逐题事实的全局 State。
        chat_model: 仅用于生成报告叙述的模型。
    返回：
        新报告、待审核状态及结构化调用轨迹；逐题记录不得被本节点改变。
    """

    interview_state = _require_state(state)
    records_before = _question_records_snapshot(interview_state)
    reason = state.get("interview_completion_reason", "user_ended")
    result = generate_review_report(chat_model, interview_state, completion_reason=reason, jd_parsed=state.get("jd_parsed") if isinstance(state.get("jd_parsed"), JDParsed) else None)
    if result.value is None:
        raise ValueError("review report generation degraded after structured-output retries")
    completed = interview_state.model_copy(update={"status": "completed", "current_question_id": None, "report": result.value})
    # 复盘修订只能替换汇总报告；逐题事实是评分审计依据，绝不能被报告节点覆盖。
    _assert_question_records_unchanged(records_before, completed)
    return {
        "current_node": "generate_review_report",
        "interview_state": completed,
        "review_status": "pending",
        "review_target": "interview_report",
        "review_feedback": None,
        "final_output": None,
        "retry_count": {"generate_review_report": result.retry_count},
        "error_log": result.error_log,
    }


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
            "evaluation_status": "available",
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
        unavailable_marker = "不可用：复盘文字生成失败，请人工核可。"
        report = InterviewReport(
            overall_score=score.overall_score,
            dimension_scores=score.dimension_scores,
            scoring_status="unavailable" if score.overall_score is None else "available",
            performance_summary=unavailable_marker,
            recurring_strengths=[unavailable_marker],
            recurring_weaknesses=[unavailable_marker],
            review_actions=[
                {
                    "priority": "P0",
                    "weakness": unavailable_marker,
                    "related_questions": [],
                    "study_topic": unavailable_marker,
                    "practice_action": unavailable_marker,
                    "verification": unavailable_marker,
                }
            ],
            question_references=sorted(allowed_ids),
            completion_reason=metadata.completion_reason,
            covered_topics=metadata.covered_topics,
            uncovered_topics=metadata.uncovered_topics,
            sample_limited=metadata.sample_limited,
        )
        return StructuredOutputResult(value=report, retry_count=raw_result.retry_count, error_log=raw_result.error_log, degraded=True)
    narrative = raw_result.value
    # 空样本复盘没有合法题号；清空模型误带的引用，避免为“尚未回答”制造题目事实。
    if not allowed_ids and (narrative.question_references or narrative.review_actions):
        narrative = narrative.model_copy(update={"question_references": [], "review_actions": []})
    _validate_report_references(narrative, allowed_ids)
    report = InterviewReport(
        overall_score=score.overall_score,
        dimension_scores=score.dimension_scores,
        scoring_status="unavailable" if score.overall_score is None else "available",
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


def _require_state(state: dict[str, object]) -> InterviewState:
    interview_state = state.get("interview_state")
    if not isinstance(interview_state, InterviewState):
        raise ValueError("interview node requires InterviewState")
    return interview_state


def _question_records_snapshot(interview_state: InterviewState) -> list[dict[str, object]]:
    """生成逐题事实的 JSON 兼容快照，供复盘节点校验其不可变性。"""

    return [record.model_dump(mode="json") for record in interview_state.question_records]


def _assert_question_records_unchanged(records_before: list[dict[str, object]], updated_state: InterviewState) -> None:
    """拒绝报告节点修改题目、回答或逐题评价，防止修订越过职责边界。"""

    if records_before != _question_records_snapshot(updated_state):
        raise ValueError("Report generation must not modify interview question_records")


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