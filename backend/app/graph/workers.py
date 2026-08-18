import json
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from app.graph.models import ModelBundle
from app.graph.prompts import STUB_OUTPUTS, WORKER_PROMPTS
from app.graph.state import CareerGraphState, WorkerName


def _message_text(message: BaseMessage) -> str:
    if isinstance(message.content, str):
        return message.content.strip()
    text_blocks = [
        str(block.get("text", ""))
        for block in message.content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(part for part in text_blocks if part).strip()


def build_worker_update(worker: WorkerName, state: CareerGraphState, models: ModelBundle) -> dict[str, Any]:
    context = state.get("readonly_context", {})
    if models.worker_model is None:
        output = STUB_OUTPUTS[worker]
    else:
        input_payload = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        response = models.worker_model.invoke(
            [
                SystemMessage(content=WORKER_PROMPTS[worker]),
                HumanMessage(content=f"只读上下文：{input_payload}"),
            ]
        )
        output = _message_text(response) or "暂时无法生成有效结果，请稍后重试。"
    return {
        "messages": [AIMessage(content=output, name=worker)],
        "worker_name": worker,
        "worker_result": {"kind": "assistant_text", "worker": worker},
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
