"""pytest 公共 fixture。

本文件为 unit/ 与 integration/ 测试提供共享的路径、Graph 与 State 基线夹具。
Week3 起新增的 Graph/State fixture 供后续 SQLAlchemy 接入前后复用，验证同一
Graph 基线输出 Schema 与 Checkpoint 恢复语义保持一致。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.graph.builder import build_graph
from app.graph.checkpoint import open_sqlite_checkpointer
from app.schemas.jd import JDParsed, SkillRequirement
from app.schemas.state import JobAssistantState


@dataclass
class FixtureChatModel:
    """供公共 Graph fixture 复用的最小假模型。

    做什么：按固定顺序返回 Supervisor/JD/面试相关结构化 JSON，避免测试依赖真实 LLM。
    关键参数：
        responses: 可选的显式响应序列；未覆盖时回退到内置 interview/JD 默认响应。
    返回值：与 LangChain ChatModel `invoke` 兼容的字符串响应。
    """

    responses: list[Any]
    invoke_calls: int = 0
    prompts: list[str] = field(default_factory=list)

    def invoke(self, prompt: str) -> Any:
        self.prompts.append(prompt)
        response = self.responses[self.invoke_calls] if self.invoke_calls < len(self.responses) else self._fallback_response(prompt)
        self.invoke_calls += 1
        if isinstance(response, Exception):
            raise response
        return response

    def bind(self, **_: Any) -> "FixtureChatModel":
        return self

    def _fallback_response(self, prompt: str) -> str:
        """根据真实节点提示词返回最小可用响应，保证公共 fixture 可运行。"""

        if "You are a JD parser" in prompt:
            return (
                '{"job_title":"后端工程师","seniority":"mid","company_name":null,'
                '"responsibilities":["负责接口设计与性能优化"],'
                '"skills":[{"name":"Python","category":"language","priority":"must","evidence":"熟悉 Python"}],'
                '"experience_requirements":["3年以上后端开发经验"],'
                '"education_requirements":[],"interview_focus":["接口设计"],'
                '"company_context":[],"ambiguities":[],"source_language":"zh-CN"}'
            )
        if "InterviewPlanOutput" in prompt:
            return (
                '{"plan":[{"topic_id":"project","topic":"项目经历","objective":"考察项目贡献",'
                '"priority":"core","basis":"user_goal"},'
                '{"topic_id":"foundation","topic":"技术基础","objective":"考察技术基础",'
                '"priority":"core","basis":"user_goal"}]}'
            )
        if "QuestionProposal" in prompt:
            if "'question_id': 'q-1'" in prompt:
                return '{"topic":"技术基础","question":"请解释一次性能排查过程。"}'
            return '{"topic":"项目经历","question":"请介绍一个你负责的项目。"}'
        if "AnswerEvaluation" in prompt:
            return (
                '{"scores":{"technical_accuracy":70,"structure":70,"job_relevance":70,"evidence":70},'
                '"feedback":"ok","strengths":[],"issues":[],"answer_relevance":"on_topic",'
                '"fatal_error":false,"fatal_error_reason":null}'
            )
        if "InterviewReportNarrative" in prompt:
            return (
                '{"performance_summary":"样本不足。","recurring_strengths":[],'
                '"recurring_weaknesses":[],"review_actions":[],"question_references":["q-1"]}'
            )
        return self.responses[-1]


@dataclass
class FixtureResumeStore:
    """供公共 Graph fixture 复用的最小假简历检索器。"""

    mapping: dict[tuple[str, str], list[dict[str, Any]]]

    def query(self, query_text: str, resume_version: str) -> list[dict[str, Any]]:
        return self.mapping.get((query_text, resume_version), [])


@pytest.fixture
def project_root() -> Path:
    """返回项目根目录路径，供后续测试定位配置和夹具文件。"""
    return Path(__file__).resolve().parent.parent


@pytest.fixture
def test_data_dir(project_root: Path) -> Path:
    """返回测试数据目录路径。

    当前仅提供统一入口，具体夹具内容将在后续步骤补充。
    """
    return project_root / "tests" / "fixtures"


@pytest.fixture
def temp_chroma_dir(tmp_path: Path) -> Path:
    """为后续 Chroma 相关测试提供临时目录。"""
    return tmp_path / "chroma"


@pytest.fixture
def sample_jd_parsed() -> JDParsed:
    """返回供 Graph/State 基线复用的标准化 JD 样例。"""

    return JDParsed(
        job_title="后端工程师",
        seniority="mid",
        company_name=None,
        responsibilities=["负责接口设计与性能优化"],
        skills=[SkillRequirement(name="Python", category="language", priority="must", evidence="熟悉 Python")],
        experience_requirements=["3年以上后端开发经验"],
        education_requirements=[],
        interview_focus=["接口设计"],
        company_context=[],
        ambiguities=[],
        source_language="zh-CN",
    )


@pytest.fixture
def graph_test_chat_model() -> FixtureChatModel:
    """返回覆盖 JD、匹配和面试子图的公共假模型。"""

    return FixtureChatModel(
        responses=[
            '{"route":"jd_parse","confidence":0.95,"reason":"jd","task_queue":[]}',
        ]
    )


@pytest.fixture
def graph_test_resume_store() -> FixtureResumeStore:
    """返回覆盖简历匹配最小可用查询的公共假检索器。"""

    return FixtureResumeStore(
        {
            ("Python", "resume-v1"): [{"chunk_id": "python-1", "quote": "熟悉 Python", "relevance": 1.0}],
            ("负责接口设计与性能优化", "resume-v1"): [
                {"chunk_id": "resp-1", "quote": "负责接口设计与性能优化", "relevance": 1.0}
            ],
            ("3年以上后端开发经验", "resume-v1"): [
                {"chunk_id": "exp-1", "quote": "3 years", "relevance": 1.0}
            ],
        }
    )


@pytest.fixture
def graph_test_state_sample() -> JobAssistantState:
    """返回基于真实 `JobAssistantState` 字段的初始样例。

    做什么：提供后续 SQLAlchemy 接入前后可直接复用的状态基线。
    返回值：包含当前实现全部附加字段的最小可用 State 样例。
    """

    return {
        "thread_id": "fixture-thread",
        "user_input": "某后端岗位，要求熟悉 Python，负责接口设计与性能优化。",
        "messages": [],
        "route_decision": None,
        "task_queue": [],
        "jd_parsed": None,
        "match_result": None,
        "interview_state": None,
        "interview_next_action": None,
        "interview_follow_up_of": None,
        "interview_completion_reason": None,
        "resume_version": None,
        "review_status": "pending",
        "review_target": None,
        "review_feedback": None,
        "current_node": "",
        "execution_history": [],
        "error_log": [],
        "retry_count": {},
        "conversation_summary": "",
        "summarized_message_count": 0,
        "final_output": None,
    }


@pytest.fixture
def graph_test_factory(graph_test_resume_store: FixtureResumeStore):
    """返回创建公共 Graph 基线的工厂函数。

    做什么：允许测试按需覆盖路由响应、是否启用内存 Checkpoint 或 SQLite Checkpoint。
    关键参数：
        responses: 假模型返回序列；首项通常为 Supervisor 路由 JSON。
        checkpointer: 可选 LangGraph Checkpointer；缺省使用内存 Saver。
    返回值：`(graph, model)`，便于测试同时断言图输出与模型调用次数。
    """

    def factory(*, responses: list[Any], checkpointer: Any | None = None) -> tuple[Any, FixtureChatModel]:
        model = FixtureChatModel(responses=responses)
        saver = checkpointer if checkpointer is not None else MemorySaver()
        graph = build_graph(model, resume_store=graph_test_resume_store, checkpointer=saver)
        return graph, model

    return factory


@pytest.fixture
def graph_test_graph(graph_test_factory):
    """返回带内存 Checkpoint 的公共 Graph 实例。"""

    graph, _model = graph_test_factory(
        responses=['{"route":"jd_parse","confidence":0.95,"reason":"jd","task_queue":[]}']
    )
    return graph


@pytest.fixture
def graph_test_config() -> dict[str, Any]:
    """返回供公共 Graph fixture 复用的最小 LangGraph configurable 配置。"""

    return {"configurable": {"thread_id": "fixture-thread"}}


@pytest.fixture
def checkpoint_graph_test_factory(tmp_path: Path, graph_test_resume_store: FixtureResumeStore):
    """返回带 SQLite Checkpoint 的公共 Graph 工厂。

    做什么：为后续跨恢复语义测试提供独立 SQLite Checkpoint 文件。
    返回值：`(graph, model, config, checkpoint_path)`；测试结束后自动关闭连接。
    """

    resources: list[tuple[Any, Any, Path]] = []

    def factory(*, responses: list[Any], thread_id: str = "fixture-thread") -> tuple[Any, FixtureChatModel, dict[str, Any], Path]:
        checkpoint_path = tmp_path / f"{thread_id}.sqlite3"
        checkpointer, connection = open_sqlite_checkpointer(checkpoint_path)
        model = FixtureChatModel(responses=responses)
        graph = build_graph(model, resume_store=graph_test_resume_store, checkpointer=checkpointer)
        resources.append((connection, model, checkpoint_path))
        return graph, model, {"configurable": {"thread_id": thread_id}}, checkpoint_path

    yield factory

    for connection, _model, _checkpoint_path in resources:
        connection.close()
