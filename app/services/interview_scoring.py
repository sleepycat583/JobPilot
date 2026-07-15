"""模拟面试的确定性决策、评分与完整性计算。

本文件由后续模拟面试 Worker 节点调用，不依赖 LLM、LangGraph 或 Checkpoint。
它将已通过 Schema 门卫的逐题记录转换为受限的下一步动作、分数和报告元数据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.schemas.interview import InterviewDimension, InterviewState, InterviewTopicPlan, QuestionRecord


InterviewAction = Literal[
    "retry_same_question",
    "clarification_follow_up",
    "evidence_follow_up",
    "next_topic",
    "finish",
]
CompletionReason = Literal["target_reached", "topics_completed", "user_ended", "max_questions_reached"]

MAX_INTERVIEW_QUESTION_COUNT = 15
TECHNICAL_CLARIFICATION_THRESHOLD = 60.0
EVIDENCE_FOLLOW_UP_TECHNICAL_THRESHOLD = 80.0
EVIDENCE_FOLLOW_UP_EVIDENCE_THRESHOLD = 60.0
MAX_CONSECUTIVE_FOLLOW_UPS_PER_TOPIC = 2
PRIMARY_QUESTION_WEIGHT = 1.0
FOLLOW_UP_QUESTION_WEIGHT = 0.5
INTERVIEW_DIMENSION_WEIGHTS: dict[InterviewDimension, float] = {
    "technical_accuracy": 0.40,
    "structure": 0.20,
    "job_relevance": 0.25,
    "evidence": 0.15,
}


@dataclass(frozen=True)
class InterviewDecision:
    """评价后可由 Graph 消费的确定性下一步动作。"""

    action: InterviewAction
    completion_reason: CompletionReason | None = None


@dataclass(frozen=True)
class InterviewScoreBreakdown:
    """逐题聚合后的四维分与确定性总分。"""

    overall_score: float
    dimension_scores: dict[InterviewDimension, float]


@dataclass(frozen=True)
class InterviewCompletionMetadata:
    """报告必须披露的计划覆盖与样本限制事实。"""

    completion_reason: CompletionReason
    covered_topics: list[str]
    uncovered_topics: list[str]
    sample_limited: bool


def count_primary_questions(records: list[QuestionRecord]) -> int:
    """返回主问题数量；追问与重答通过 ``follow_up_of`` 区分。"""

    return sum(record.follow_up_of is None for record in records)


def count_consecutive_follow_ups(records: list[QuestionRecord], topic: str) -> int:
    """计算末尾同主题连续追问数，用于执行 §3.4 的两次上限。"""

    count = 0
    for record in reversed(records):
        if record.topic != topic or record.follow_up_of is None:
            break
        count += 1
    return count


def decide_next_interview_action(
    state: InterviewState,
    *,
    user_ended: bool = False,
    current_question_retried: bool = False,
) -> InterviewDecision:
    """按 §3.4 固定优先级决定追问、换题或结束。

    参数：
        state: 包含当前已评价记录和计划的面试状态。
        user_ended: 用户是否已通过既有 end_interview 命令结束。
        current_question_retried: 当前主问题是否已获得一次重答机会。
    返回：
        InterviewDecision；结束时携带确定性 completion_reason。
    """

    records = state.question_records
    if user_ended:
        return InterviewDecision("finish", "user_ended")
    if len(records) >= MAX_INTERVIEW_QUESTION_COUNT:
        return InterviewDecision("finish", "max_questions_reached")

    covered_ids = covered_topic_ids(state.plan, records)
    core_topic_ids = {topic.topic_id for topic in state.plan if topic.priority == "core"}
    if core_topic_ids and core_topic_ids.issubset(covered_ids):
        return InterviewDecision("finish", "topics_completed")
    if count_primary_questions(records) >= state.target_question_count:
        return InterviewDecision("finish", "target_reached")
    if not records:
        return InterviewDecision("next_topic")

    current = records[-1]
    _require_evaluated_record(current)
    if current.answer_relevance == "off_topic":
        return InterviewDecision("next_topic" if current_question_retried else "retry_same_question")
    if count_consecutive_follow_ups(records, current.topic) >= MAX_CONSECUTIVE_FOLLOW_UPS_PER_TOPIC:
        return InterviewDecision("next_topic")
    if current.scores["technical_accuracy"] < TECHNICAL_CLARIFICATION_THRESHOLD:
        return InterviewDecision("clarification_follow_up")
    if (
        current.scores["technical_accuracy"] >= EVIDENCE_FOLLOW_UP_TECHNICAL_THRESHOLD
        and current.scores["evidence"] < EVIDENCE_FOLLOW_UP_EVIDENCE_THRESHOLD
    ):
        return InterviewDecision("evidence_follow_up")
    return InterviewDecision("next_topic")


def calculate_interview_score(records: list[QuestionRecord]) -> InterviewScoreBreakdown:
    """按主问题 1.0、追问 0.5 聚合四维并计算固定权重总分。

    空记录返回全零分，便于用户第一题前提前结束时生成样本不足报告；含有
    未评价记录时抛出 ValueError，避免将等待态题目静默计入最终结论。
    """

    if not records:
        zero_scores: dict[InterviewDimension, float] = {
            "technical_accuracy": 0.0,
            "structure": 0.0,
            "job_relevance": 0.0,
            "evidence": 0.0,
        }
        return InterviewScoreBreakdown(overall_score=0.0, dimension_scores=zero_scores)

    for record in records:
        _require_evaluated_record(record)
    total_weight = sum(_record_weight(record) for record in records)
    dimension_scores: dict[InterviewDimension, float] = {}
    for dimension in INTERVIEW_DIMENSION_WEIGHTS:
        weighted_total = sum(record.scores[dimension] * _record_weight(record) for record in records)
        dimension_scores[dimension] = round(weighted_total / total_weight, 1)
    overall_score = round(
        sum(dimension_scores[dimension] * weight for dimension, weight in INTERVIEW_DIMENSION_WEIGHTS.items()),
        1,
    )
    return InterviewScoreBreakdown(overall_score=overall_score, dimension_scores=dimension_scores)


def build_completion_metadata(
    plan: list[InterviewTopicPlan],
    records: list[QuestionRecord],
    completion_reason: CompletionReason,
) -> InterviewCompletionMetadata:
    """从计划和主问题记录确定覆盖差集与样本不足标记。"""

    covered_ids = covered_topic_ids(plan, records)
    covered_topics = [topic.topic for topic in plan if topic.topic_id in covered_ids]
    uncovered_topics = [topic.topic for topic in plan if topic.topic_id not in covered_ids]
    sample_limited = completion_reason in {"user_ended", "max_questions_reached"} or bool(uncovered_topics)
    return InterviewCompletionMetadata(
        completion_reason=completion_reason,
        covered_topics=covered_topics,
        uncovered_topics=uncovered_topics,
        sample_limited=sample_limited,
    )


def covered_topic_ids(plan: list[InterviewTopicPlan], records: list[QuestionRecord]) -> set[str]:
    """仅将主问题映射到计划主题，避免追问虚增覆盖范围。"""

    topic_by_name = {topic.topic: topic.topic_id for topic in plan}
    return {
        topic_by_name[record.topic]
        for record in records
        if record.follow_up_of is None and record.topic in topic_by_name
    }


def _record_weight(record: QuestionRecord) -> float:
    return FOLLOW_UP_QUESTION_WEIGHT if record.follow_up_of is not None else PRIMARY_QUESTION_WEIGHT


def _require_evaluated_record(record: QuestionRecord) -> None:
    if not record.scores or record.answer_relevance is None:
        raise ValueError("interview scoring requires evaluated question records")