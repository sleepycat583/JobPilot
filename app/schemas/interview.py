"""模拟面试最小冻结契约。

本文件只保留 JobAssistantState 在 §5.1 中引用到的最小类型骨架，不实现 Week 2 的完整面试业务字段和逻辑。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class QuestionRecord(BaseModel):
    """单题记录的最小冻结骨架。"""

    model_config = ConfigDict(extra="forbid")

    question_id: str
    topic: str
    question: str
    answer: str
    follow_up_of: str | None
    scores: dict[str, float]
    feedback: str
    strengths: list[str]
    issues: list[str]


class ReviewAction(BaseModel):
    """面试复盘动作的最小冻结骨架。"""

    model_config = ConfigDict(extra="forbid")

    priority: Literal["P0", "P1", "P2"]
    weakness: str
    related_questions: list[str]
    study_topic: str
    practice_action: str
    verification: str


class InterviewReport(BaseModel):
    """面试复盘报告的最小冻结骨架。"""

    model_config = ConfigDict(extra="forbid")

    overall_score: float
    dimension_scores: dict[str, float]
    performance_summary: str
    recurring_strengths: list[str]
    recurring_weaknesses: list[str]
    review_actions: list[ReviewAction]
    question_references: list[str]


class InterviewState(BaseModel):
    """模拟面试状态骨架。

    只保留 §3.4 / §5.1 在本阶段需要的字段引用，不实现面试流程。
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["planning", "asking", "waiting", "evaluating", "completed"]
    target_question_count: int
    current_question_id: str | None
    question_records: list[QuestionRecord]
    user_context_updates: list[str]
    report: InterviewReport | None
