from __future__ import annotations

from typing import Any


TRACE_SCHEMA_VERSION = "career-workbench.trace.v1"
TRACE_TAGS = ("career-workbench", "supervisor-worker")


def build_trace_config(thread_id: str, run_id: str, *, operation: str) -> dict[str, Any]:
    """Build trace metadata without copying user content or business payloads."""

    return {
        "configurable": {"thread_id": thread_id},
        "metadata": {
            "app_thread_id": thread_id,
            "run_id": run_id,
            "operation": operation,
            "trace_schema_version": TRACE_SCHEMA_VERSION,
        },
        "tags": [*TRACE_TAGS, f"operation:{operation}"],
        "run_name": f"career.{operation}",
        "recursion_limit": 30,
    }
