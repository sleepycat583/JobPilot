"""简历匹配 Agent。

本文件实现文档 §3.3 冻结的简历匹配流程：
- 按 JD 需求逐项做固定 top-k 的 Chroma 证据检索
- 复用结构化输出服务做定性匹配判断
- 使用确定性评分服务计算总分，绝不让 LLM 直接决定总分
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field

from app.rag.chroma_store import ChromaQueryResult, ChromaResumeStore, ResumeVersionNotFoundError
from app.graph.review_helpers import next_match_business_attempt
from app.schemas.jd import JDParsed, SkillRequirement
from app.schemas.resume import EvidenceRef, MatchItem, MatchResult
from app.schemas.state import ErrorEntry, ExecutionEvent, ExecutionEventMetadata, JobAssistantState
from app.services.match_scoring import (
    ConstraintStatus,
    MatchScoreBreakdown,
    ScoreableMatchItem,
    calculate_match_score,
    status_to_item_score,
)
from app.services.structured_output import StructuredPromptContext, call_with_structured_output

RAG_EMPTY_RESULT_CODE = "RAG_EMPTY_RESULT"
RAG_EMPTY_RESULT_GAP = "未从简历中检索到与岗位要求匹配的有效证据，当前匹配结果需人工确认"
RESUME_VERSION_NOT_FOUND_CODE = "RESUME_VERSION_NOT_FOUND"


class LLMEvidenceRef(BaseModel):
    """LLM 输出的证据引用占位。"""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    quote: str


class LLMMatchItem(BaseModel):
    """LLM 输出的单项定性判断。"""

    model_config = ConfigDict(extra="forbid")

    requirement: str
    status: Literal["matched", "transferable", "weak", "missing"]
    rationale: str
    evidence: list[LLMEvidenceRef] = Field(default_factory=list)
    recent: bool = False
    quantified: bool = False


class LLMConstraintItem(BaseModel):
    """LLM 输出的硬约束判断。"""

    model_config = ConfigDict(extra="forbid")

    requirement: str
    status: Literal["satisfied", "partial", "missing"]
    rationale: str
    evidence: list[LLMEvidenceRef] = Field(default_factory=list)


class LLMMatchAnalysis(BaseModel):
    """LLM 输出的匹配定性结果。"""

    model_config = ConfigDict(extra="forbid")

    must_items: list[LLMMatchItem]
    responsibility_items: list[LLMMatchItem]
    preferred_items: list[LLMMatchItem]
    constraint_items: list[LLMConstraintItem]
    strengths: list[str]
    gaps: list[str]
    recommendations: list[str]


def resume_matcher_node(
    state: JobAssistantState,
    chat_model: BaseChatModel,
    resume_store: ChromaResumeStore,
) -> dict[str, object]:
    """执行 JD 与简历的证据绑定匹配。

    参数：
        state: 当前 LangGraph 全局状态，必须包含 `jd_parsed` 与 `resume_version`。
        chat_model: 已构建好的聊天模型实例，由外部注入。
        resume_store: 第⑨步实现的 Chroma 检索适配器。

    返回：
        仅写 `match_result` 业务字段，同时补充共享可观测字段。
    """

    jd_parsed = state.get("jd_parsed")
    resume_version = state.get("resume_version")
    if not isinstance(jd_parsed, JDParsed):
        raise ValueError("resume_matcher_node requires jd_parsed")
    if not isinstance(resume_version, str) or not resume_version.strip():
        raise ValueError("resume_matcher_node requires resume_version")

    current_node = "resume_matcher"
    try:
        retrieval_context = _collect_requirement_evidence(jd_parsed, resume_version, resume_store)
    except ResumeVersionNotFoundError as exc:
        return {
            "match_result": None,
            "current_node": current_node,
            "retry_count": {current_node: 0},
            "error_log": [
                _build_error_entry(
                    code=exc.code,
                    message=str(exc),
                    retryable=False,
                    attempt=0,
                )
            ],
            "execution_history": [_build_event(current_node, "error", "resume_version_not_found")],
        }

    prompt_context = StructuredPromptContext(
        full_prompt=_build_match_prompt(jd_parsed, resume_version, retrieval_context),
        minimal_input=_build_minimal_prompt(jd_parsed, resume_version, retrieval_context),
    )
    structured_result = call_with_structured_output(
        chat_model,
        LLMMatchAnalysis,
        prompt_context,
        current_node,
    )

    error_log = list(structured_result.error_log)
    if structured_result.value is None:
        analysis = _build_degraded_analysis(jd_parsed)
    else:
        analysis = structured_result.value

    normalized = _normalize_analysis(analysis, retrieval_context)
    score_breakdown = calculate_match_score(
        must_items=[_to_scoreable_item(item) for item in normalized["must_items"]],
        responsibility_items=[_to_scoreable_item(item) for item in normalized["responsibility_items"]],
        preferred_items=[_to_scoreable_item(item) for item in normalized["preferred_items"]],
        constraint_statuses=[item["status"] for item in normalized["constraint_items"]],
    )

    gaps = list(normalized["gaps"])
    execution_detail = "matched"
    if score_breakdown.rag_empty_result:
        error_log.append(
            _build_error_entry(
                code=RAG_EMPTY_RESULT_CODE,
                message="Chroma returned no valid evidence for any JD requirement",
                retryable=False,
                attempt=0,
            )
        )
        if RAG_EMPTY_RESULT_GAP not in gaps:
            gaps.append(RAG_EMPTY_RESULT_GAP)
        execution_detail = "rag_empty_result"

    match_result = MatchResult(
        total_score=score_breakdown.total_score,
        dimension_scores=score_breakdown.dimension_scores,
        matched_items=normalized["matched_items"],
        strengths=normalized["strengths"],
        gaps=gaps,
        recommendations=normalized["recommendations"],
        low_score_review_required=score_breakdown.low_score_review_required,
        resume_version=resume_version,
    )

    return {
        "match_result": match_result,
        "current_node": current_node,
        "retry_count": {current_node: structured_result.retry_count},
        "error_log": error_log,
        "execution_history": [
            _build_event(
                current_node,
                "success",
                execution_detail,
                metadata={
                    "business_attempt": next_match_business_attempt(state.get("execution_history", [])),
                    "resume_version": resume_version,
                    "total_score": match_result.total_score,
                },
            )
        ],
    }


def _collect_requirement_evidence(
    jd_parsed: JDParsed,
    resume_version: str,
    resume_store: ChromaResumeStore,
) -> dict[str, list[dict[str, Any]]]:
    """按 JD 各维度逐项检索证据。"""

    must_items = [skill for skill in jd_parsed.skills if skill.priority == "must"]
    preferred_items = [skill for skill in jd_parsed.skills if skill.priority == "preferred"]

    return {
        "must_items": [
            _build_requirement_entry(skill.name, resume_store.query(skill.name, resume_version))
            for skill in must_items
        ],
        "responsibility_items": [
            _build_requirement_entry(resp, resume_store.query(resp, resume_version))
            for resp in jd_parsed.responsibilities
        ],
        "preferred_items": [
            _build_requirement_entry(skill.name, resume_store.query(skill.name, resume_version))
            for skill in preferred_items
        ],
        "constraint_items": [
            _build_requirement_entry(req, resume_store.query(req, resume_version))
            for req in jd_parsed.experience_requirements + jd_parsed.education_requirements
        ],
    }


def _build_requirement_entry(requirement: str, evidence_rows: list[ChromaQueryResult]) -> dict[str, Any]:
    return {
        "requirement": requirement,
        "evidence": [EvidenceRef(**row) for row in evidence_rows],
    }


def _build_match_prompt(
    jd_parsed: JDParsed,
    resume_version: str,
    retrieval_context: dict[str, list[dict[str, Any]]],
) -> str:
    """构造匹配分析 Prompt。"""

    return (
        "You are a resume matcher. Return only a JSON object that matches LLMMatchAnalysis.\n"
        "Decide matched/transferable/weak/missing only from provided evidence.\n"
        "Every evidence item must reuse an existing chunk_id and exact quote from the provided evidence list.\n"
        "Do not generate total_score or dimension scores.\n"
        f"Resume version: {resume_version}\n"
        f"JD title: {jd_parsed.job_title}\n"
        f"Evidence context: {retrieval_context}"
    )


def _build_minimal_prompt(
    jd_parsed: JDParsed,
    resume_version: str,
    retrieval_context: dict[str, list[dict[str, Any]]],
) -> str:
    """构造 structured output 最小重试上下文。"""

    return (
        f"resume_version={resume_version}\n"
        f"must={[item['requirement'] for item in retrieval_context['must_items']]}\n"
        f"responsibilities={jd_parsed.responsibilities}\n"
        f"preferred={[item['requirement'] for item in retrieval_context['preferred_items']]}\n"
        f"constraints={[item['requirement'] for item in retrieval_context['constraint_items']]}\n"
        f"evidence={retrieval_context}"
    )


def _build_degraded_analysis(jd_parsed: JDParsed) -> LLMMatchAnalysis:
    """构造 LLM 结构化失败后的最小匹配结果。"""

    return LLMMatchAnalysis(
        must_items=[
            LLMMatchItem(requirement=skill.name, status="missing", rationale="structured output degraded", evidence=[])
            for skill in jd_parsed.skills
            if skill.priority == "must"
        ],
        responsibility_items=[
            LLMMatchItem(requirement=item, status="missing", rationale="structured output degraded", evidence=[])
            for item in jd_parsed.responsibilities
        ],
        preferred_items=[
            LLMMatchItem(requirement=skill.name, status="missing", rationale="structured output degraded", evidence=[])
            for skill in jd_parsed.skills
            if skill.priority == "preferred"
        ],
        constraint_items=[
            LLMConstraintItem(requirement=item, status="missing", rationale="structured output degraded", evidence=[])
            for item in jd_parsed.experience_requirements + jd_parsed.education_requirements
        ],
        strengths=[],
        gaps=["LLM_SCHEMA_INVALID"],
        recommendations=["请人工复核简历匹配结论"],
    )


def _normalize_analysis(
    analysis: LLMMatchAnalysis,
    retrieval_context: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """校验 LLM 引用的证据，剔除无效引用并构造最终 MatchItem。"""

    must_items = _normalize_match_items(analysis.must_items, retrieval_context["must_items"])
    responsibility_items = _normalize_match_items(
        analysis.responsibility_items,
        retrieval_context["responsibility_items"],
    )
    preferred_items = _normalize_match_items(
        analysis.preferred_items,
        retrieval_context["preferred_items"],
    )
    constraint_items = _normalize_constraint_items(
        analysis.constraint_items,
        retrieval_context["constraint_items"],
    )

    matched_items = [item["match_item"] for item in must_items + responsibility_items + preferred_items]
    return {
        "must_items": must_items,
        "responsibility_items": responsibility_items,
        "preferred_items": preferred_items,
        "constraint_items": constraint_items,
        "matched_items": matched_items,
        "strengths": analysis.strengths,
        "gaps": analysis.gaps,
        "recommendations": analysis.recommendations,
    }


def _normalize_match_items(
    llm_items: list[LLMMatchItem],
    retrieval_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    retrieval_by_requirement = {item["requirement"]: item["evidence"] for item in retrieval_items}
    normalized: list[dict[str, Any]] = []

    for llm_item in llm_items:
        allowed_evidence = retrieval_by_requirement.get(llm_item.requirement, [])
        valid_evidence = _filter_valid_evidence(llm_item.evidence, allowed_evidence)
        status = llm_item.status
        if not valid_evidence:
            status = "missing"

        match_item = MatchItem(
            requirement=llm_item.requirement,
            status=status,
            score=status_to_item_score(status),
            evidence=valid_evidence,
            rationale=llm_item.rationale,
        )
        normalized.append(
            {
                "requirement": llm_item.requirement,
                "status": status,
                "evidence_count": len(valid_evidence),
                "avg_relevance": _average_relevance(valid_evidence),
                "recent": llm_item.recent,
                "quantified": llm_item.quantified,
                "match_item": match_item,
            }
        )
    return normalized


def _normalize_constraint_items(
    llm_items: list[LLMConstraintItem],
    retrieval_items: list[dict[str, Any]],
) -> list[dict[str, ConstraintStatus]]:
    retrieval_by_requirement = {item["requirement"]: item["evidence"] for item in retrieval_items}
    normalized: list[dict[str, ConstraintStatus]] = []
    for llm_item in llm_items:
        allowed_evidence = retrieval_by_requirement.get(llm_item.requirement, [])
        valid_evidence = _filter_valid_evidence(llm_item.evidence, allowed_evidence)
        status: ConstraintStatus = llm_item.status
        if not valid_evidence:
            status = "missing"
        normalized.append({"requirement": llm_item.requirement, "status": status})
    return normalized


def _filter_valid_evidence(
    evidence_refs: list[LLMEvidenceRef],
    allowed_evidence: list[EvidenceRef],
) -> list[EvidenceRef]:
    allowed_by_chunk = {item.chunk_id: item for item in allowed_evidence}
    valid: list[EvidenceRef] = []
    for ref in evidence_refs:
        source = allowed_by_chunk.get(ref.chunk_id)
        if source is None:
            continue
        if ref.quote != source.quote and ref.quote not in source.quote:
            continue
        valid.append(source)
    return valid


def _average_relevance(evidence: list[EvidenceRef]) -> float:
    if not evidence:
        return 0.0
    return round(sum(item.relevance for item in evidence) / len(evidence), 4)


def _to_scoreable_item(item: dict[str, Any]) -> ScoreableMatchItem:
    return ScoreableMatchItem(
        requirement=str(item["requirement"]),
        status=item["status"],
        evidence_count=int(item["evidence_count"]),
        avg_relevance=float(item["avg_relevance"]),
        recent=bool(item["recent"]),
        quantified=bool(item["quantified"]),
    )


def _build_event(
    node: str,
    event: str,
    detail: str,
    metadata: ExecutionEventMetadata | None = None,
) -> ExecutionEvent:
    """构造 matcher 执行轨迹，并在成功评分时附加结构化业务 attempt。"""

    result: ExecutionEvent = {
        "node": node,
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "detail": detail,
    }
    if metadata is not None:
        result["metadata"] = metadata
    return result


def _build_error_entry(code: str, message: str, retryable: bool, attempt: int) -> ErrorEntry:
    return {
        "code": code,
        "node": "resume_matcher",
        "message": message,
        "retryable": retryable,
        "attempt": attempt,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "raw_output_excerpt": None,
    }