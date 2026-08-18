from __future__ import annotations

import argparse
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
DEFAULT_MIN_OVERALL_ACCURACY = 0.90
DEFAULT_MIN_WORKER_ACCURACY = 0.80


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


def summarize_routes(cases: list[dict[str, Any]], actual_workers: list[str | None]) -> dict[str, Any]:
    """Return route metrics without copying case text into the report."""

    if len(cases) != len(actual_workers):
        raise ValueError("cases and predictions must have the same length")
    passed = sum(actual == case["expected_worker"] for case, actual in zip(cases, actual_workers))
    worker_totals: dict[str, int] = {}
    worker_passed: dict[str, int] = {}
    for case, actual in zip(cases, actual_workers):
        expected = str(case["expected_worker"])
        worker_totals[expected] = worker_totals.get(expected, 0) + 1
        if actual == expected:
            worker_passed[expected] = worker_passed.get(expected, 0) + 1
    worker_accuracy = {
        worker: worker_passed.get(worker, 0) / total
        for worker, total in sorted(worker_totals.items())
    }
    return {
        "passed": passed,
        "failed": len(cases) - passed,
        "total": len(cases),
        "accuracy": passed / len(cases) if cases else 0.0,
        "invalid_tool_calls": sum(actual is None for actual in actual_workers),
        "worker_accuracy": worker_accuracy,
    }


def meets_thresholds(
    summary: dict[str, Any],
    *,
    min_overall_accuracy: float = DEFAULT_MIN_OVERALL_ACCURACY,
    min_worker_accuracy: float = DEFAULT_MIN_WORKER_ACCURACY,
) -> bool:
    """Apply evaluation gates; these do not participate in runtime routing."""

    return (
        summary["total"] > 0
        and summary["accuracy"] >= min_overall_accuracy
        and summary["invalid_tool_calls"] == 0
        and all(value >= min_worker_accuracy for value in summary["worker_accuracy"].values())
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the live semantic Supervisor route evaluation")
    parser.add_argument(
        "--min-overall-accuracy",
        type=float,
        default=DEFAULT_MIN_OVERALL_ACCURACY,
        help="Required overall exact-route accuracy (default: 0.90)",
    )
    parser.add_argument(
        "--min-worker-accuracy",
        type=float,
        default=DEFAULT_MIN_WORKER_ACCURACY,
        help="Required accuracy for every expected Worker (default: 0.80)",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="Optional path for an aggregate report containing IDs and route metrics only",
    )
    args = parser.parse_args()
    if not 0 <= args.min_overall_accuracy <= 1 or not 0 <= args.min_worker_accuracy <= 1:
        parser.error("accuracy thresholds must be between 0 and 1")
    return args


def main() -> int:
    args = _parse_args()
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

    actual_workers: list[str | None] = []
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
        actual_workers.append(actual)
        expected = case["expected_worker"]
        passed = actual == expected
        print(
            f"{'PASS' if passed else 'FAIL'} {case['id']}: "
            f"expected={expected} actual={actual or 'invalid_tool_call'}"
        )

    summary = summarize_routes(cases, actual_workers)
    print(
        f"summary: passed={summary['passed']} failed={summary['failed']} "
        f"total={summary['total']} accuracy={summary['accuracy']:.3f} "
        f"invalid_tool_calls={summary['invalid_tool_calls']}"
    )
    for worker, accuracy in summary["worker_accuracy"].items():
        print(f"worker_accuracy: {worker}={accuracy:.3f}")
    passed_gate = meets_thresholds(
        summary,
        min_overall_accuracy=args.min_overall_accuracy,
        min_worker_accuracy=args.min_worker_accuracy,
    )
    print(
        f"gate: {'PASS' if passed_gate else 'FAIL'} "
        f"min_overall={args.min_overall_accuracy:.3f} min_worker={args.min_worker_accuracy:.3f}"
    )
    if args.json_output:
        report = {
            "cases": [
                {"id": case["id"], "expected_worker": case["expected_worker"], "actual_worker": actual}
                for case, actual in zip(cases, actual_workers)
            ],
            "summary": summary,
            "thresholds": {
                "min_overall_accuracy": args.min_overall_accuracy,
                "min_worker_accuracy": args.min_worker_accuracy,
            },
            "passed_gate": passed_gate,
        }
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if passed_gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
