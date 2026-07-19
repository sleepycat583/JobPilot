"""把只读的 Case1 归档导入业务库，并生成可审计的 run_index 映射。

调用方传入归档源和目标业务库路径；源库只读，目标库必须已由 Alembic 初始化。
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

from app.db import build_session_factory
from app.repositories.experiment import ExperimentRunRepository


DATA_FIELDS = (
    "case_name", "architecture", "run_index", "model_name", "prompt_version", "status",
    "schema_valid", "unsupported_skill_claims", "llm_calls", "estimated_tokens", "latency_ms",
    "error_codes", "output_json", "created_at",
)


def build_archive_mapping(source_path: Path) -> list[dict[str, int | str]]:
    """按 architecture、created_at、原始 id 为归档行分配新的连续编号。"""

    with sqlite3.connect(source_path) as connection:
        rows = [dict(row) for row in _rows(connection)]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["architecture"])].append(row)
    mapping: list[dict[str, int | str]] = []
    for architecture, group in grouped.items():
        ordered = sorted(group, key=lambda row: (str(row["created_at"]), int(row["id"])))
        mapping.extend(
            {"source_id": int(row["id"]), "architecture": architecture, "new_run_index": index}
            for index, row in enumerate(ordered, 1)
        )
    return sorted(mapping, key=lambda item: int(item["source_id"]))


def migrate_archive(source_path: Path, target_path: Path) -> list[dict[str, int | str]]:
    """将归档行写入已初始化的目标库并返回 source id 到新编号映射。"""

    mapping = build_archive_mapping(source_path)
    by_id = {int(item["source_id"]): int(item["new_run_index"]) for item in mapping}
    with sqlite3.connect(source_path) as connection:
        rows = [dict(row) for row in _rows(connection)]
    engine = create_engine(f"sqlite:///{target_path.as_posix()}")
    factory = build_session_factory(engine)
    try:
        with factory() as session:
            repository = ExperimentRunRepository(session)
            for row in rows:
                row["run_index"] = by_id[int(row["id"])]
                repository.create_run({field: row[field] for field in DATA_FIELDS})
    finally:
        engine.dispose()
    return mapping


def _rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    connection.row_factory = sqlite3.Row
    return connection.execute("SELECT id, " + ", ".join(DATA_FIELDS) + " FROM experiment_runs").fetchall()