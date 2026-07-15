"""模拟面试 Worker 的结构化状态与评价契约。

本文件由模拟面试子图、结构化输出适配层和最终核可流程调用；只定义
Checkpoint 中需要持久化的面试数据及 LLM 输出的基础门卫校验，不承担
题目规划、评分汇总或 Graph 路由等业务逻辑。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


InterviewDimension = Literal["technical_accuracy", "structure", "job_relevance", "evidence"]
AnswerRelevance = Literal["on_topic", "partial", "off_topic"]


class InterviewTopicPlan(BaseModel):
    """面试计划中的一个可恢复主题槽位。

    ``basis`` 记录主题来自 JD、匹配结果或独立用户目标，供后续出题节点
    校验题目来源，避免在 Checkpoint 恢复后把自由生成题当作既定计划题。
    """

    model_config = ConfigDict(extra="forbid")

    topic_id: str = Field(min_length=1, max_length=80)
    topic: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=500)
    priority: Literal["core", "supporting"]
    basis: Literal["jd", "match_result", "user_goal", "general"]


class AnswerEvaluation(BaseModel):
    """LLM 对单题回答的受限结构化评价。

    这是评价节点的模型输出，不包含题目、回答或 ID；程序将在校验成功后
    将其与 Checkpoint 中的原始题答合并为 ``QuestionRecord``。
    """

    model_config = ConfigDict(extra="forbid")

    scores: dict[InterviewDimension, float]
    feedback: str = Field(min_length=1, max_length=2000)
    strengths: list[str] = Field(default_factory=list, max_length=8)
    issues: list[str] = Field(default_factory=list, max_length=8)
    answer_relevance: AnswerRelevance
    fatal_error: bool = False
    fatal_error_reason: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_evaluation_consistency(self) -> "AnswerEvaluation":
        """阻断自相矛盾评价，保留无法规则化的语义判断给 LLM。"""

        expected_dimensions = {"technical_accuracy", "structure", "job_relevance", "evidence"}
        if set(self.scores) != expected_dimensions:
            raise ValueError("scores must contain exactly the four interview dimensions")
        if any(score < 0.0 or score > 100.0 for score in self.scores.values()):
            raise ValueError("all interview scores must be within 0..100")
        if self.fatal_error:
            if self.fatal_error_reason is None or not self.issues or self.scores["technical_accuracy"] >= 60.0:
                raise ValueError("fatal_error requires a reason, at least one issue, and technical_accuracy below 60")
        elif self.fatal_error_reason is not None:
            raise ValueError("fatal_error_reason must be null when fatal_error is false")
        if self.answer_relevance == "off_topic" and self.scores["job_relevance"] >= 60.0:
            raise ValueError("off_topic requires job_relevance below 60")
        if self.answer_relevance == "on_topic" and self.scores["job_relevance"] < 40.0:
            raise ValueError("on_topic requires job_relevance of at least 40")
        return self


class InterviewPlanOutput(BaseModel):
    """LLM 生成的计划主题，程序随后补齐可恢复 InterviewState。"""

    model_config = ConfigDict(extra="forbid")

    plan: list[InterviewTopicPlan] = Field(min_length=1, max_length=15)


class QuestionProposal(BaseModel):
    """LLM 生成的下一题内容；题号和追问关联由程序确定。"""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=2000)


class QuestionRecord(BaseModel):
    """单题题目、回答和评价的可恢复记录。

    等待回答的骨架记录允许 ``scores`` 为空、评价信号为 ``None``；评价节点
    成功后必须将同一套字段填入经 ``AnswerEvaluation`` 门卫校验的结果。
    """

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=2000)
    answer: str
    follow_up_of: str | None
    scores: dict[InterviewDimension, float] = Field(default_factory=dict)
    feedback: str
    strengths: list[str]
    issues: list[str]
    answer_relevance: AnswerRelevance | None = None
    fatal_error: bool = False
    fatal_error_reason: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_record_score_shape(self) -> "QuestionRecord":
        """允许等待态空分数，同时拒绝部分或越界的已评价分数。"""

        if not self.scores:
            if self.answer_relevance is not None or self.fatal_error or self.fatal_error_reason is not None:
                raise ValueError("unevaluated records cannot contain evaluation route signals")
            return self
        expected_dimensions = {"technical_accuracy", "structure", "job_relevance", "evidence"}
        if set(self.scores) != expected_dimensions:
            raise ValueError("scores must be empty or contain exactly the four interview dimensions")
        if any(score < 0.0 or score > 100.0 for score in self.scores.values()):
            raise ValueError("all interview scores must be within 0..100")
        if self.answer_relevance is None:
            raise ValueError("evaluated records require answer_relevance")
        AnswerEvaluation(
            scores=self.scores,
            feedback=self.feedback,
            strengths=self.strengths,
            issues=self.issues,
            answer_relevance=self.answer_relevance,
            fatal_error=self.fatal_error,
            fatal_error_reason=self.fatal_error_reason,
        )
        return self


class ReviewAction(BaseModel):
    """面试复盘动作的最小冻结骨架。"""

    model_config = ConfigDict(extra="forbid")

    priority: Literal["P0", "P1", "P2"]
    weakness: str
    related_questions: list[str]
    study_topic: str
    practice_action: str
    verification: str


class InterviewReportNarrative(BaseModel):
    """LLM 生成的复盘叙述，不含程序计算的分数和覆盖元数据。"""

    model_config = ConfigDict(extra="forbid")

    performance_summary: str = Field(min_length=1, max_length=4000)
    recurring_strengths: list[str] = Field(default_factory=list, max_length=10)
    recurring_weaknesses: list[str] = Field(default_factory=list, max_length=10)
    review_actions: list[ReviewAction] = Field(default_factory=list, max_length=10)
    question_references: list[str] = Field(default_factory=list, max_length=15)


class InterviewReport(BaseModel):
    """面试结束后的复盘报告及程序确定的完整性元数据。"""

    model_config = ConfigDict(extra="forbid")

    overall_score: float = Field(ge=0.0, le=100.0)
    dimension_scores: dict[InterviewDimension, float]
    performance_summary: str = Field(min_length=1, max_length=4000)
    recurring_strengths: list[str]
    recurring_weaknesses: list[str]
    review_actions: list[ReviewAction]
    question_references: list[str]
    completion_reason: Literal["target_reached", "topics_completed", "user_ended", "max_questions_reached"]
    covered_topics: list[str]
    uncovered_topics: list[str]
    sample_limited: bool

    @model_validator(mode="after")
    def validate_report_scores(self) -> "InterviewReport":
        """保证报告分数维度完整，完整性事实不由自由文本代替。"""

        expected_dimensions = {"technical_accuracy", "structure", "job_relevance", "evidence"}
        if set(self.dimension_scores) != expected_dimensions:
            raise ValueError("dimension_scores must contain exactly the four interview dimensions")
        if any(score < 0.0 or score > 100.0 for score in self.dimension_scores.values()):
            raise ValueError("all dimension scores must be within 0..100")
        return self


class InterviewState(BaseModel):
    """模拟面试子图的可恢复状态，由 Worker 独占写入。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["planning", "asking", "waiting", "evaluating", "completed"]
    target_question_count: int = Field(ge=1, le=15)
    current_question_id: str | None
    question_records: list[QuestionRecord]
    user_context_updates: list[str]
    report: InterviewReport | None
    plan: list[InterviewTopicPlan] = Field(default_factory=list, max_length=15)
