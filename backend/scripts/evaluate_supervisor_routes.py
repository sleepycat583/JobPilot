from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import configure_langsmith, get_settings  # noqa: E402
from app.graph.builder import WORKER_DESCRIPTIONS  # noqa: E402
from app.graph.handoff import create_semantic_handoff_tool  # noqa: E402
from app.graph.models import build_model_bundle  # noqa: E402
from app.graph.prompts import build_supervisor_prompt  # noqa: E402


EVAL_PATH = BACKEND_DIR / "evals" / "supervisor_routes.json"


def _history_messages(items: list[dict[str, str]]) -> list[BaseMessage]:
    messages: list[BaseMessage] = []
    for item in items:
        if item["role"] == "user":
            messages.append(HumanMessage(content=item["content"]))
        else:
            messages.append(AIMessage(content=item["content"]))
    return messages


def _selected_worker(response: AIMessage) -> str | None:
    if len(response.tool_calls) != 1:
        return None
    name = str(response.tool_calls[0].get("name", ""))
    prefix = "transfer_to_"
    return name[len(prefix) :] if name.startswith(prefix) else None


def main() -> int:
    settings = get_settings()
    if settings.llm_mode != "openai":
        raise RuntimeError("Live route evaluation requires LLM_MODE=openai")
    configure_langsmith(settings)
    bundle = build_model_bundle(settings)
    tools = [
        create_semantic_handoff_tool(name, description)
        for name, description in WORKER_DESCRIPTIONS.items()
    ]
    supervisor = bundle.supervisor_model.bind_tools(tools, parallel_tool_calls=False)
    cases: list[dict[str, Any]] = json.loads(EVAL_PATH.read_text(encoding="utf-8"))

    failures = 0
    for case in cases:
        history = case.get("history", [])
        messages = _history_messages(history)
        messages.append(HumanMessage(content=case["message"]))
        readonly_context = {
            "latest_user_message": case["message"],
            "conversation_tail": [*history, {"role": "user", "content": case["message"]}],
            **case.get("context", {}),
        }
        response = supervisor.invoke(
            build_supervisor_prompt({"messages": messages, "readonly_context": readonly_context})
        )
        actual = _selected_worker(response)
        expected = case["expected_worker"]
        passed = actual == expected
        failures += 0 if passed else 1
        print(
            f"{'PASS' if passed else 'FAIL'} {case['id']}: "
            f"expected={expected} actual={actual or 'invalid_tool_call'}"
        )

    print(f"summary: passed={len(cases) - failures} failed={failures} total={len(cases)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
