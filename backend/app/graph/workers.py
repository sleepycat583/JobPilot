import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.config import get_stream_writer
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
    "employer_worker": frozenset({"respond", "run_employer_due_diligence"}),
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


def _chunk_text(content: Any) -> str:
    """提取 ChatModel 流式 chunk 中的纯文本，过滤工具调用等非文本内容。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    return ""


async def build_worker_update_stream(worker: WorkerName, state: CareerGraphState, models: ModelBundle) -> dict[str, Any]:
    """先完成内部 action 决策，再仅对 respond 文本进行 token 级流式生成。

    action 决策仍使用结构化输出，避免把 JSON 或工具调用暴露给用户；只有
    `respond` 分支会调用普通文本流，并通过 LangGraph custom stream 发送增量。
    """
    if models.worker_model is None:
        return build_worker_update(worker, state, models)

    context = state.get("readonly_context", {})
    input_payload = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    runnable = models.worker_model.with_structured_output(WorkerDecision, method="function_calling")
    decision_result = await runnable.ainvoke(
        [
            SystemMessage(content=WORKER_PROMPTS[worker]),
            HumanMessage(content=f"只读上下文：{input_payload}"),
        ]
    )
    decision = decision_result if isinstance(decision_result, WorkerDecision) else WorkerDecision.model_validate(decision_result)
    action = decision.action if decision.action in ALLOWED_ACTIONS[worker] else "respond"

    if action != "respond":
        output = decision.message.strip() or "正在执行你的请求。"
    else:
        writer = get_stream_writer()
        response_messages = [
            SystemMessage(
                content=(
                    f"{WORKER_PROMPTS[worker]}\n"
                    "现在只生成最终给用户看的自然语言回答。不要输出 JSON、action、工具调用或内部字段。"
                )
            ),
            HumanMessage(content=f"只读上下文：{input_payload}"),
        ]
        parts: list[str] = []
        async for chunk in models.worker_model.astream(response_messages):
            text = _chunk_text(getattr(chunk, "content", ""))
            if not text:
                continue
            parts.append(text)
            writer({"type": "public_output_delta", "delta": text})
        output = "".join(parts).strip() or decision.message.strip() or "正在执行你的请求。"

    return {
        "messages": [AIMessage(content=output, name=worker)],
        "worker_name": worker,
        "worker_result": {"kind": "worker_decision", "worker": worker, "action": action},
        "visible_output": output,
    }


def build_worker_graph(worker: WorkerName, models: ModelBundle):
    builder = StateGraph(CareerGraphState)

    if models.worker_model is None:
        def run_worker(state: CareerGraphState) -> dict[str, Any]:
            return build_worker_update(worker, state, models)
    else:
        async def run_worker(state: CareerGraphState) -> dict[str, Any]:
            return await build_worker_update_stream(worker, state, models)

    builder.add_node("run", run_worker)
    builder.add_edge(START, "run")
    builder.add_edge("run", END)
    return builder.compile(name=worker)


def sanitize_output(state: CareerGraphState) -> dict[str, str | None]:
    visible = state.get("visible_output")
    if not isinstance(visible, str):
        return {"public_output": None}
    cleaned = "".join(character for character in visible if character in "\n\t" or ord(character) >= 32).strip()
    cleaned = _extract_public_text(cleaned)
    if not cleaned:
        cleaned = "暂时无法生成有效结果，请稍后重试。"
    return {"public_output": cleaned[:12_000]}


def _extract_public_text(value: str) -> str:
    """Keep only the user-facing field if a model leaks a structured envelope."""

    candidate = value.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        lines = candidate.splitlines()
        candidate = "\n".join(lines[1:-1]).strip()
    if not candidate.startswith(("{", "[")):
        if any(marker in candidate for marker in ("transfer_to_", '"tool_calls"', '"worker_result"')):
            return "暂时无法生成有效结果，请稍后重试。"
        return value
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return value
    if isinstance(payload, dict):
        for key in ("public_output", "message", "content", "text"):
            candidate = payload.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return "暂时无法生成有效结果，请稍后重试。"
    if isinstance(payload, list):
        return "暂时无法生成有效结果，请稍后重试。"
    return candidate


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
