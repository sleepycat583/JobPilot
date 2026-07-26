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
from app.db import Base, build_session_factory, create_sqlalchemy_engine
from app.graph.builder import build_graph
from app.graph.checkpoint import open_sqlite_checkpointer
from app.services.resume_storage import ResumeStorage

JD_PARSED = json.dumps({
    "job_title": "Java后端工程师", "seniority": "mid", "company_name": None,
    "responsibilities": ["API design"],
    "skills": [{"name": "Java", "category": "language", "priority": "must", "evidence": "熟悉 Java"}],
    "experience_requirements": ["3年以上后端开发经验"], "education_requirements": [],
    "interview_focus": [], "company_context": [], "ambiguities": [], "source_language": "zh-CN",
}, ensure_ascii=False)


@dataclass
class VerificationChatModel:
    """返回固定结构化 JD 与匹配结论，供完整浏览器流程验证。"""

    def invoke(self, prompt: str) -> str:
        time.sleep(0.4)
        if "RouterDecision" in prompt:
            task_queue = ["jd_parse", "resume_match"] if "Has resume: True" in prompt else ["jd_parse"]
            return json.dumps({"route": "jd_parse", "confidence": 0.9, "reason": "verification", "task_queue": task_queue})
        if "LLMMatchAnalysis" in prompt:
            return json.dumps({
                "must_items": [{"requirement": "Java", "status": "matched", "rationale": "简历中包含 Java 开发经历", "evidence": [{"chunk_id": "verification-chunk", "quote": "候选人具备 Java、Spring Boot 与 API 设计经历"}]}],
                "responsibility_items": [{"requirement": "API design", "status": "matched", "rationale": "简历中包含接口设计经历", "evidence": [{"chunk_id": "verification-chunk", "quote": "候选人具备 Java、Spring Boot 与 API 设计经历"}]}],
                "preferred_items": [], "constraint_items": [],
                "strengths": ["具备 Java 后端基础"], "gaps": [], "recommendations": ["补充接口设计案例"],
            }, ensure_ascii=False)
        return JD_PARSED

    def bind(self, **_: Any) -> "VerificationChatModel":
        return self


class VerificationEmbeddingModel:
    """为本地浏览器验证提供确定性 embedding，避免加载外部模型服务。"""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[float(index)] for index, _ in enumerate(texts, start=1)]


class VerificationResumeStore:
    """提供确定性检索，验证 HTTP/UI 生命周期而非向量召回质量。"""

    def __init__(self) -> None:
        self.resume_ids: set[str] = set()

    def delete_resume_chunks(self, resume_id: str) -> None:
        self.resume_ids.discard(resume_id)

    def upsert_chunks(self, chunks: list[Any], embeddings: list[list[float]]) -> None:
        if chunks:
            self.resume_ids.add(chunks[0]["resume_id"])

    def query(self, query_text: str, resume_id: str) -> list[dict[str, object]]:
        """为已索引简历返回稳定证据，满足 Matcher 的检索接口。"""
        # 验证 SQLite 会跨脚本运行保留已索引版本，而此内存替身每次启动都会重建；
        # 因此不能用本进程的写入记录判断历史验证简历是否存在。
        return [{"chunk_id": "verification-chunk", "quote": "候选人具备 Java、Spring Boot 与 API 设计经历", "relevance": 0.9}]


def build_verification_app():
    """装配真实 API、业务库与本地索引替身，用于浏览器端上传验证。"""

    checkpointer, checkpoint_connection = open_sqlite_checkpointer("data/checkpoints-verification.sqlite3")
    engine = create_sqlalchemy_engine("sqlite:///data/app-verification.sqlite3")
    Base.metadata.create_all(engine)
    resume_store = VerificationResumeStore()
    graph = build_graph(VerificationChatModel(), resume_store=resume_store, checkpointer=checkpointer)

    def close() -> None:
        checkpoint_connection.close()
        engine.dispose()

    return create_app(
        dependencies=AppDependencies(
            graph=graph,
            session_factory=build_session_factory(engine),
            resume_store=resume_store,
            embedding_model=VerificationEmbeddingModel(),
            resume_storage=ResumeStorage("data/resumes-verification"),
            close=close,
        )
    )


app = build_verification_app()