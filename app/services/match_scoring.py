"""简历匹配确定性评分服务。

本文件实现文档 §3.3 冻结的五维固定权重评分，不依赖 LLM。
它被 resume_matcher Agent 调用，用于把逐项匹配状态换算为最终总分与低分标志。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.constants import LOW_SCORE_THRESHOLD

MatchStatus = Literal["matched", "transferable", "weak", "missing"]
ConstraintStatus = Literal["satisfied", "partial", "missing"]

EMPTY_RAG_SCORE_CAP = 10.0
EVIDENCE_RELEVANCE_WEIGHT = 0.70
EVIDENCE_QUANTIFIED_BONUS = 0.15
EVIDENCE_RECENT_BONUS = 0.15
MATCH_STATUS_WEIGHTS: dict[MatchStatus, float] = {
    "matched": 1.0,
    "transferable": 0.6,
    "weak": 0.3,
    "missing": 0.0,
}
CONSTRAINT_STATUS_WEIGHTS: dict[ConstraintStatus, float] = {
    "satisfied": 1.0,
    "partial": 0.5,
    "missing": 0.0,
}


@dataclass(frozen=True)
class ScoreableMatchItem:
    """评分所需的单项匹配输入。

    参数：
        requirement: 当前 JD 需求文本。
        status: 该需求的覆盖等级，由上游证据判定得出。
        evidence_count: 合法证据数量；用于判断是否为空召回与估算证据质量。
        avg_relevance: 合法证据平均相关度，范围 0-1。
        recent: 证据是否体现近 3 年经验。
        quantified: 证据是否含明确量化结果。
    """

    requirement: str
    status: MatchStatus
    evidence_count: int
    avg_relevance: float
    recent: bool = False
    quantified: bool = False


@dataclass(frozen=True)
class MatchScoreBreakdown:
    """确定性评分结果。"""

    total_score: float
    dimension_scores: dict[str, float]
    low_score_review_required: bool
    rag_empty_result: bool


def calculate_match_score(
    *,
    must_items: list[ScoreableMatchItem],
    responsibility_items: list[ScoreableMatchItem],
    preferred_items: list[ScoreableMatchItem],
    constraint_statuses: list[ConstraintStatus],
) -> MatchScoreBreakdown:
    """按 §3.3 固定权重计算总分与低分标志。

    返回：
        MatchScoreBreakdown，包含五维得分、总分、低分复核标志，以及是否为空召回。
    """

    must_skill_score = _average_status_score(must_items)
    responsibility_score = _average_status_score(responsibility_items)
    preferred_skill_score = _average_status_score(preferred_items)
    constraint_score = _average_constraint_score(constraint_statuses)
    evidence_quality_score = _evidence_quality_score(
        must_items + responsibility_items + preferred_items
    )

    raw_total = round(
        must_skill_score * 0.40
        + responsibility_score * 0.30
        + preferred_skill_score * 0.10
        + constraint_score * 0.10
        + evidence_quality_score * 0.10,
        1,
    )

    rag_empty_result = _is_rag_empty(must_items + responsibility_items + preferred_items)
    total_score = min(raw_total, EMPTY_RAG_SCORE_CAP) if rag_empty_result else raw_total
    return MatchScoreBreakdown(
        total_score=total_score,
        dimension_scores={
            "must_skill_score": must_skill_score,
            "responsibility_score": responsibility_score,
            "preferred_skill_score": preferred_skill_score,
            "constraint_score": constraint_score,
            "evidence_quality_score": evidence_quality_score,
        },
        low_score_review_required=total_score < LOW_SCORE_THRESHOLD,
        rag_empty_result=rag_empty_result,
    )


def status_to_item_score(status: MatchStatus) -> float:
    """把单项匹配状态映射为 0-100 分制得分。"""

    return round(MATCH_STATUS_WEIGHTS[status] * 100.0, 1)


def _average_status_score(items: list[ScoreableMatchItem]) -> float:
    if not items:
        return 0.0
    total = sum(MATCH_STATUS_WEIGHTS[item.status] for item in items)
    return round(total / len(items) * 100.0, 1)


def _average_constraint_score(statuses: list[ConstraintStatus]) -> float:
    if not statuses:
        return 0.0
    total = sum(CONSTRAINT_STATUS_WEIGHTS[status] for status in statuses)
    return round(total / len(statuses) * 100.0, 1)


def _evidence_quality_score(items: list[ScoreableMatchItem]) -> float:
    """计算证据质量维度分数。

    工程规则：该维度只评价“已经有合法证据”的条目，因此仅统计 `evidence_count > 0`
    的项目；零证据项目不进入分母，避免把“无证据”与“有证据但质量差”混为一谈。

    单条证据质量采用确定性公式：
        min(
            1.0,
            clamp(avg_relevance, 0, 1) * 0.70
            + (0.15 if quantified else 0.0)
            + (0.15 if recent else 0.0)
        )

    权重依据：文档只冻结了“量化且近 3 年优先、无证据不得补分”，没有冻结内部配比。
    这里把相关度设为主体 70%，确保弱相关证据不会仅凭标签被抬成高质量；量化结果与
    近期性在文档中的优先级并列，因此各给 15% 奖励。所有有效条目等权平均，最终换算为
    0-100 分并保留 1 位小数。
    """

    scored_items = [item for item in items if item.evidence_count > 0]
    if not scored_items:
        return 0.0

    total = 0.0
    for item in scored_items:
        relevance_component = max(0.0, min(1.0, item.avg_relevance)) * EVIDENCE_RELEVANCE_WEIGHT
        quantified_component = EVIDENCE_QUANTIFIED_BONUS if item.quantified else 0.0
        recent_component = EVIDENCE_RECENT_BONUS if item.recent else 0.0
        total += min(1.0, relevance_component + quantified_component + recent_component)
    return round(total / len(scored_items) * 100.0, 1)


def _is_rag_empty(items: list[ScoreableMatchItem]) -> bool:
    return all(item.evidence_count == 0 for item in items)