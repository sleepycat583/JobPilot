import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from app.graph.models import ModelBundle
from app.graph.prompts import STUB_OUTPUTS, WORKER_PROMPTS
from app.graph.state import CareerGraphState, WorkerAction, WorkerName


class WorkerDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: WorkerAction = "respond"
    message: str = Field(default="", max_length=12_000)


ALLOWED_ACTIONS: dict[WorkerName, frozenset[WorkerAction]] = {
    "resume_worker": frozenset({"respond"}),
    "jd_worker": frozenset({"respond", "create_jd"}),
    "match_worker": frozenset({"respond", "run_match"}),
    "interview_worker": frozenset(
        {
            "respond",
            "start_interview",
            "submit_interview_answer",
            "continue_interview",
            "end_interview",
        }
    ),
    "chat_worker": frozenset({"respond"}),
}


def build_worker_update(worker: WorkerName, state: CareerGraphState, models: ModelBundle) -> dict[str, Any]:
    context = state.get("readonly_context", {})
    if models.worker_model is None:
        output = STUB_OUTPUTS[worker]
        action: WorkerAction = "respond"
    else:
        input_payload = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        runnable = models.worker_model.with_structured_output(WorkerDecision, method="function_calling")
        response = runnable.invoke(
            [
                SystemMessage(content=WORKER_PROMPTS[worker]),
                HumanMessage(content=f"只读上下文：{input_payload}"),
            ]
        )
        decision = response if isinstance(response, WorkerDecision) else WorkerDecision.model_validate(response)
        action = decision.action if decision.action in ALLOWED_ACTIONS[worker] else "respond"
        output = decision.message.strip() or "正在执行你的请求。"
    return {
        "messages": [AIMessage(content=output, name=worker)],
        "worker_name": worker,
        "worker_result": {"kind": "worker_decision", "worker": worker, "action": action},
        "visible_output": output,
    }


def build_worker_graph(worker: WorkerName, models: ModelBundle):
    builder = StateGraph(CareerGraphState)

    def run_worker(state: CareerGraphState) -> dict[str, Any]:
        return build_worker_update(worker, state, models)

    builder.add_node("run", run_worker)
    builder.add_edge(START, "run")
    builder.add_edge("run", END)
    return builder.compile(name=worker)


def sanitize_output(state: CareerGraphState) -> dict[str, str | None]:
    visible = state.get("visible_output")
    if not isinstance(visible, str):
        return {"public_output": None}
    cleaned = "".join(character for character in visible if character in "\n\t" or ord(character) >= 32).strip()
    if not cleaned:
        cleaned = "暂时无法生成有效结果，请稍后重试。"
    return {"public_output": cleaned[:12_000]}


def finalize_supervisor_step(state: CareerGraphState) -> dict[str, Any]:
    """Sanitize output and make the one-worker-per-turn invariant idempotent."""

    update: dict[str, Any] = sanitize_output(state)
    if state.get("worker_name") is None:
        return update

    messages = state.get("messages", ())
    last_message = messages[-1] if messages else None
    if isinstance(last_message, AIMessage):
        update["messages"] = [
            AIMessage(content="FINISH", name="supervisor", id=last_message.id)
        ]
    return update
