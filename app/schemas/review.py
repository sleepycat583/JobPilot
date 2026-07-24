"""Review 与 HITL 的冻结输入输出契约。

本模块由 API 恢复端点和确定性控制节点共同使用。所有命令通过 `type` 区分，
避免不同人工介入点接收彼此无效的字段组合。
"""

from typing import Annotated, Any, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

ReviewStatus = Literal["pending", "in_review", "approved", "rejected", "revising"]


class LowScoreReviewCommand(BaseModel):
    """校验低分 Gate 恢复命令，供 API 在构造 Command 前调用。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["low_match_score"] = "low_match_score"
    action: Literal["continue", "revise_inputs", "cancel"]
    feedback: str = Field(default="", max_length=2000)
    resume_id: str | None = Field(default=None, min_length=1, max_length=200)
    jd_text: str | None = Field(default=None, min_length=20)

    @model_validator(mode="after")
    def validate_revise_inputs(self) -> "LowScoreReviewCommand":
        """确保 revise_inputs 至少携带一项可用于重算的修订信息。"""

        if self.action == "revise_inputs" and not (self.feedback.strip() or self.resume_id or self.jd_text):
            raise ValueError("revise_inputs requires feedback, resume_id, or jd_text")
        return self


class LowScoreInterruptPayload(BaseModel):
    """低分匹配暂停时发送给调用方的最小审核上下文。

    完整 JD、简历证据和 MatchResult 已由 Checkpoint 持久化，不能重复复制到
    interrupt payload，避免恢复协议膨胀并扩大敏感数据暴露范围。
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["low_match_score"]
    target: Literal["match_result"] = "match_result"
    score: float = Field(ge=0.0, le=100.0)
    threshold: float = Field(ge=0.0, le=100.0)
    top_gaps: list[str] = Field(max_length=5)
    accepted_actions: list[Literal["continue", "revise_inputs", "cancel"]] = Field(min_length=1)


class InterviewAnswerCommand(BaseModel):
    """校验面试等待节点的恢复命令。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["interview_answer"] = "interview_answer"
    action: Literal["submit_answer", "context_update", "end_interview"]
    answer: str = Field(default="", max_length=5000)
    context: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_interview_input(self) -> "InterviewAnswerCommand":
        """要求提交回答或补充上下文时提供对应内容。"""

        if self.action == "submit_answer" and not self.answer.strip():
            raise ValueError("submit_answer requires answer")
        if self.action == "context_update" and not self.context.strip():
            raise ValueError("context_update requires context")
        return self


class InterviewInterruptPayload(BaseModel):
    """面试等待回答或补充信息时发送的最小上下文。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["interview_answer"]
    target: Literal["interview_state"] = "interview_state"
    question_id: str
    question: str
    accepted_actions: list[Literal["submit_answer", "context_update", "end_interview"]] = Field(min_length=1)


class InterviewEvaluationUnavailableCommand(BaseModel):
    """校验单题评价不可用时的恢复命令。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["interview_evaluation_unavailable"] = "interview_evaluation_unavailable"
    action: Literal["retry_evaluation", "skip_evaluation"]


class InterviewEvaluationUnavailableInterruptPayload(BaseModel):
    """单题结构化评价耗尽重试后发送给调用方的最小上下文。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["interview_evaluation_unavailable"]
    target: Literal["question_record"] = "question_record"
    question_id: str
    accepted_actions: list[Literal["retry_evaluation", "skip_evaluation"]] = Field(min_length=1)


class FinalReviewCommand(BaseModel):
    """校验最终候选产物核可的恢复命令。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["final_review"] = "final_review"
    action: Literal["approve", "reject"]
    feedback: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_rejection_feedback(self) -> "FinalReviewCommand":
        """要求驳回最终草稿时留下可执行反馈。"""

        if self.action == "reject" and not self.feedback.strip():
            raise ValueError("reject requires feedback")
        return self


class FinalReviewInterruptPayload(BaseModel):
    """最终核可前发送的结构化草稿。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["final_review"]
    target: Literal["jd_parsed", "match_result", "interview_report"]
    draft: dict[str, Any]
    accepted_actions: list[Literal["approve", "reject"]] = Field(min_length=1)


HITLCommand = Annotated[
    Union[LowScoreReviewCommand, InterviewAnswerCommand, InterviewEvaluationUnavailableCommand, FinalReviewCommand],
    Field(discriminator="type"),
]
HITLInterruptPayload = Annotated[
    Union[LowScoreInterruptPayload, InterviewInterruptPayload, InterviewEvaluationUnavailableInterruptPayload, FinalReviewInterruptPayload],
    Field(discriminator="type"),
]


class ResumeRequest(BaseModel):
    """HTTP resume 请求外层契约。

    幂等键属于 API 去重元数据，不会传入 LangGraph `Command(resume=...)` 的业务命令。
    """

    model_config = ConfigDict(extra="forbid")

    idempotency_key: UUID
    command: dict[str, Any]
