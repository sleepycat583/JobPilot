"""第 3 章本地浏览器验证用 FastAPI 服务。

本文件复用真实 HTTP 路由、LangGraph 和业务 SQLite，只替换外部 LLM 与简历检索，
使 Playwright 验证不依赖网络模型服务。由 scripts/run_week3_browser_verification.mjs 启动。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from app.api import AppDependencies, create_app
from app.db import build_session_factory, create_sqlalchemy_engine
from app.graph.builder import build_graph
from app.graph.checkpoint import open_sqlite_checkpointer

JD_PARSED = json.dumps({
    "job_title": "Java后端工程师", "seniority": "mid", "company_name": None,
    "responsibilities": ["API design"],
    "skills": [{"name": "Java", "category": "language", "priority": "must", "evidence": "熟悉 Java"}],
    "experience_requirements": ["3年以上后端开发经验"], "education_requirements": [],
    "interview_focus": [], "company_context": [], "ambiguities": [], "source_language": "zh-CN",
}, ensure_ascii=False)


@dataclass
class VerificationChatModel:
    """返回固定结构化 JD，并留出时间窗口供 SSE 断线测试。"""

    def invoke(self, prompt: str) -> str:
        time.sleep(0.4)
        if "RouterDecision" in prompt:
            return json.dumps({"route": "jd_parse", "confidence": 0.9, "reason": "verification", "task_queue": ["jd_parse"]})
        return JD_PARSED

    def bind(self, **_: Any) -> "VerificationChatModel":
        return self


def build_verification_app():
    """装配真实 API、业务库和 Checkpoint，用于浏览器验证。"""

    checkpointer, checkpoint_connection = open_sqlite_checkpointer("data/checkpoints-verification.sqlite3")
    engine = create_sqlalchemy_engine("sqlite:///data/app.sqlite3")
    graph = build_graph(VerificationChatModel(), checkpointer=checkpointer)

    def close() -> None:
        checkpoint_connection.close()
        engine.dispose()

    return create_app(
        dependencies=AppDependencies(
            graph=graph,
            session_factory=build_session_factory(engine),
            close=close,
        )
    )


app = build_verification_app()