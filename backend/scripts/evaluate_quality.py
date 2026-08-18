from __future__ import annotations

import json
from pathlib import Path
import sys
from uuid import uuid4

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.graph.builder import WORKER_DESCRIPTIONS, build_career_graph  # noqa: E402
from app.graph.models import ModelBundle, StubSupervisorModel  # noqa: E402
from app.graph.state import WORKER_WRITABLE_FIELDS, WorkerName  # noqa: E402
from app.graph.workers import ALLOWED_ACTIONS, sanitize_output  # noqa: E402


EVAL_PATH = BACKEND_DIR / "evals" / "supervisor_routes.json"
EXPECTED_ACTIONS: dict[WorkerName, frozenset[str]] = {
    "resume_worker": frozenset({"respond"}),
    "jd_worker": frozenset({"respond", "create_jd"}),
    "match_worker": frozenset({"respond", "run_match"}),
    "interview_worker": frozenset(
        {"respond", "start_interview", "submit_interview_answer", "continue_interview", "end_interview"}
    ),
    "chat_worker": frozenset({"respond"}),
}


def _check(label: str, condition: bool) -> bool:
    print(f"{'PASS' if condition else 'FAIL'} {label}")
    return condition


def _run_stub_contract(worker: WorkerName) -> bool:
    models = ModelBundle(mode="stub", supervisor_model=StubSupervisorModel(route=worker), worker_model=None)
    with SqliteSaver.from_conn_string(":memory:") as saver:
        graph = build_career_graph(models, saver)
        result = graph.invoke(
            {
                "messages": [HumanMessage(content="quality-contract")],
                "remaining_steps": 30,
                "readonly_context": {"latest_user_message": "quality-contract"},
                "run_id": str(uuid4()),
                "route_audit": None,
                "worker_name": None,
                "worker_result": None,
                "visible_output": None,
                "public_output": None,
            },
            {"configurable": {"thread_id": str(uuid4())}},
        )
    return (
        result.get("worker_name") == worker
        and result.get("route_audit", {}).get("worker") == worker
        and result.get("public_output")
        and "tool_call" not in str(result.get("public_output"))
        and set(result.get("worker_result", {})).issubset({"kind", "worker", "action"})
    )


def main() -> int:
    cases = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    checks = [
        _check("route dataset has unique case IDs", len({case["id"] for case in cases}) == len(cases)),
        _check(
            "route dataset workers are declared",
            all(case["expected_worker"] in WORKER_DESCRIPTIONS for case in cases),
        ),
        _check(
            "worker action boundaries match design",
            all(ALLOWED_ACTIONS[name] == EXPECTED_ACTIONS[name] for name in WORKER_DESCRIPTIONS),
        ),
        _check(
            "worker state ownership remains bounded",
            WORKER_WRITABLE_FIELDS == frozenset({"messages", "worker_name", "worker_result", "visible_output"}),
        ),
    ]
    for worker in WORKER_DESCRIPTIONS:
        checks.append(_check(f"stub graph contract: {worker}", _run_stub_contract(worker)))

    checks.extend(
        [
            _check(
                "structured envelope exposes only public message",
                sanitize_output({"visible_output": '{"action":"run_match","message":"结果已生成"}'})[
                    "public_output"
                ]
                == "结果已生成",
            ),
            _check(
                "tool-call envelope is blocked",
                sanitize_output({"visible_output": '{"tool_calls":[{"name":"transfer_to_match_worker"}]}'})[
                    "public_output"
                ]
                == "暂时无法生成有效结果，请稍后重试。",
            ),
            _check(
                "fenced structured envelope exposes only public message",
                sanitize_output({"visible_output": '```json\n{"message":"结果已生成"}\n```'})["public_output"]
                == "结果已生成",
            ),
        ]
    )
    passed = sum(checks)
    print(f"summary: passed={passed} failed={len(checks) - passed} total={len(checks)}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
