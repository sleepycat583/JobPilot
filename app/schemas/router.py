"""Supervisor 路由决策 Schema。

本文件只定义第 3.1 节与第 6.1 节冻结的路由输出契约，不包含任何路由逻辑。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RouterDecision(BaseModel):
    """Supervisor 的结构化路由结果。

    字段严格对照文档 §3.1 和 §6.1。
    """

    model_config = ConfigDict(extra="forbid")

    route: Literal["jd_parse", "resume_match", "mock_interview", "clarify", "out_of_scope"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=200)
    task_queue: list[Literal["jd_parse", "resume_match", "mock_interview"]] = Field(default_factory=list)
