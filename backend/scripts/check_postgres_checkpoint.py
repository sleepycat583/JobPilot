"""Verify a PostgreSQL LangGraph checkpoint store without exposing credentials."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from uuid import uuid4

from langgraph.checkpoint.base import empty_checkpoint


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.checkpoint import initialize_checkpoint, open_checkpoint, probe_checkpoint  # noqa: E402
from app.core.config import get_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Check PostgreSQL LangGraph checkpoint persistence")
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Create checkpoint tables before the probe (use only in a controlled migration step)",
    )
    args = parser.parse_args()

    settings = get_settings()
    if settings.checkpoint_backend != "postgres":
        print("FAIL checkpoint backend is not postgres; set CHECKPOINT_BACKEND=postgres", file=sys.stderr)
        return 1
    if not settings.graph_checkpoint_database_url:
        print("FAIL GRAPH_CHECKPOINT_DATABASE_URL is not configured", file=sys.stderr)
        return 1

    manager = None
    checkpointer = None
    entered = False
    thread_id: str | None = None
    try:
        manager = open_checkpoint(settings)
        checkpointer = manager.__enter__()
        entered = True
        if args.setup:
            setup_settings = settings.model_copy(update={"auto_create_checkpoint_schema": True})
            initialize_checkpoint(checkpointer, setup_settings)
        probe_checkpoint(checkpointer)

        thread_id = f"storage-smoke-{uuid4()}"
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        checkpointer.put(
            config,
            empty_checkpoint(),
            {"source": "input", "step": -1, "writes": None, "parents": {}},
            {},
        )
        if checkpointer.get_tuple(config) is None:
            print("FAIL checkpoint write completed but read returned no record", file=sys.stderr)
            return 1
        print("PASS PostgreSQL checkpoint probe, write, and read")
        return 0
    except Exception:
        # Never print the exception: connection errors can contain deployment details.
        print("FAIL PostgreSQL checkpoint operation", file=sys.stderr)
        return 1
    finally:
        if checkpointer is not None and thread_id is not None:
            try:
                checkpointer.delete_thread(thread_id)
            except Exception:
                pass
        if manager is not None and entered:
            manager.__exit__(None, None, None)


if __name__ == "__main__":
    raise SystemExit(main())
