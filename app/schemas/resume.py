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


class UnavailableEvidenceItem(BaseModel):
    """结构化匹配不可用时，供人工检查的单项检索证据。"""

    model_config = ConfigDict(extra="forbid")

    requirement: str
    evidence: list[EvidenceRef]


class MatchUnavailableResult(BaseModel):
    """结构化匹配重试耗尽后的无分数结果。

    该对象只表达“尚不能得出匹配结论”及已取得的检索证据，避免将 LLM
    不可用误写成零分或其他未经验证的分数。
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["MATCH_UNAVAILABLE"]
    resume_version: str
    retrieval_evidence: list[UnavailableEvidenceItem]
    message: str
