"""Preview or apply retention cleanup for local runtime metadata."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.db import build_engine, build_session_factory
from app.services.local_maintenance import apply_local_cleanup, preview_local_cleanup


def main() -> int:
    parser = argparse.ArgumentParser(description="清理个人电脑本地工作台的可再生历史记录")
    parser.add_argument("--apply", action="store_true", help="实际删除；省略时只显示预览")
    parser.add_argument("--retention-days", type=int, default=None, help="保留天数，默认读取 LOCAL_HISTORY_RETENTION_DAYS")
    args = parser.parse_args()
    settings = get_settings()
    if settings.upload_storage_backend != "local" or settings.checkpoint_backend != "sqlite" or settings.chroma_backend != "local":
        raise RuntimeError("维护命令仅支持默认单机本地存储拓扑")
    retention_days = args.retention_days or settings.local_history_retention_days
    if retention_days < 1:
        raise ValueError("retention days must be positive")
    engine = build_engine(settings)
    try:
        with build_session_factory(engine)() as session:
            result = apply_local_cleanup(session, retention_days=retention_days) if args.apply else preview_local_cleanup(session, retention_days=retention_days)
    finally:
        engine.dispose()
    mode = "applied" if args.apply else "preview"
    print(f"cleanup {mode}: terminal_jobs={result['terminal_jobs']} idempotency_records={result['idempotency_records']} execution_events={result['execution_events']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
