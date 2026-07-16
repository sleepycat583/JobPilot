"""对话滚动摘要 Schema。

本模块由 rolling summary 状态维护节点调用，用于验证 LLM 对旧对话的压缩结果。
"""

from pydantic import BaseModel, Field


class ConversationSummary(BaseModel):
    """旧对话中必须跨轮保留的事实分类。"""

    user_goals: list[str] = Field(default_factory=list)
    confirmed_facts_and_decisions: list[str] = Field(default_factory=list)
    corrections_and_constraints: list[str] = Field(default_factory=list)
    unresolved_questions_and_next_actions: list[str] = Field(default_factory=list)
    approval_feedback: list[str] = Field(default_factory=list)
    interview_topics_and_scores: list[str] = Field(default_factory=list)