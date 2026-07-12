"""JD 解析相关 Schema。

本文件只定义文档 §3.2 冻结的输入输出契约，不包含解析逻辑。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class JDParseInput(BaseModel):
    """JD 解析输入。"""

    model_config = ConfigDict(extra="forbid")

    jd_text: str = Field(min_length=20, max_length=30000)
    allow_web_search: bool = False
    language: Literal["zh-CN", "en-US", "auto"] = "auto"


class SkillRequirement(BaseModel):
    """岗位技能要求。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    category: Literal["language", "framework", "database", "cloud", "engineering", "domain", "soft_skill"]
    priority: Literal["must", "preferred", "inferred"]
    evidence: str


class JDParsed(BaseModel):
    """JD 结构化结果。"""

    model_config = ConfigDict(extra="forbid")

    job_title: str
    seniority: Literal["intern", "junior", "mid", "senior", "lead", "unknown"]
    company_name: str | None
    responsibilities: list[str]
    skills: list[SkillRequirement]
    experience_requirements: list[str]
    education_requirements: list[str]
    interview_focus: list[str]
    company_context: list[str]
    ambiguities: list[str]
    source_language: str
