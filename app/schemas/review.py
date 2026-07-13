"""Review 最小冻结契约。

本文件只定义 §5.1 中引用到的 ReviewStatus 枚举，不实现 Week 2 的完整审核流程。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReviewStatus = Literal["pending", "in_review", "approved", "rejected", "revising"]


class LowScoreReviewCommand(BaseModel):
    """校验低分 Gate 恢复命令，供 API 在构造 Command 前调用。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["continue", "cancel"]
    feedback: str = Field(default="", max_length=2000)


class LowScoreInterruptPayload(BaseModel):
    """低分匹配暂停时发送给调用方的最小审核上下文。

    完整 JD、简历证据和 MatchResult 已由 Checkpoint 持久化，不能重复复制到
    interrupt payload，避免恢复协议膨胀并扩大敏感数据暴露范围。
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["low_match_score"]
    score: float = Field(ge=0.0, le=100.0)
    threshold: float = Field(ge=0.0, le=100.0)
    top_gaps: list[str] = Field(max_length=5)
    accepted_actions: list[Literal["continue", "cancel"]] = Field(min_length=1)
