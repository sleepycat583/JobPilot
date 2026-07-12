"""简历匹配相关 Schema。

本文件只定义文档 §3.3 冻结的证据与匹配结果契约，不包含评分逻辑。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EvidenceRef(BaseModel):
    """简历证据引用。"""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    quote: str
    relevance: float = Field(ge=0.0, le=1.0)


class MatchItem(BaseModel):
    """单项需求匹配结果。"""

    model_config = ConfigDict(extra="forbid")

    requirement: str
    status: Literal["matched", "transferable", "weak", "missing"]
    score: float
    evidence: list[EvidenceRef]
    rationale: str


class MatchResult(BaseModel):
    """简历匹配总结果。"""

    model_config = ConfigDict(extra="forbid")

    total_score: float = Field(ge=0.0, le=100.0)
    dimension_scores: dict[str, float]
    matched_items: list[MatchItem]
    strengths: list[str]
    gaps: list[str]
    recommendations: list[str]
    low_score_review_required: bool
    resume_version: str
