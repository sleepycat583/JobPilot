"""实验归档重编号与导入测试。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, text

from app.db import Base
from scripts.migrate_experiment_archive import build_archive_mapping, migrate_archive


def _make_archive(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE experiment_runs (id INTEGER PRIMARY KEY, case_name TEXT, architecture TEXT, "
        "run_index INTEGER, model_name TEXT, prompt_version TEXT, status TEXT, schema_valid INTEGER, "
        "unsupported_skill_claims INTEGER, llm_calls INTEGER, estimated_tokens INTEGER, latency_ms REAL, "
        "error_codes TEXT, output_json TEXT, created_at TEXT)"
    )
    for index, architecture in enumerate(("baseline", "multi_agent", "baseline"), 1):
        connection.execute(
            "INSERT INTO experiment_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (index, "case1", architecture, 1, "model", "v1", "success", 1, 0, 1, index, float(index), "[]", "{}", f"2026-01-0{index}T00:00:00+00:00"),
        )
    connection.commit()
    connection.close()


def test_archive_mapping_orders_by_timestamp_then_source_id(tmp_path: Path) -> None:
    source = tmp_path / "archive.sqlite3"
    _make_archive(source)
    assert build_archive_mapping(source) == [
        {"source_id": 1, "architecture": "baseline", "new_run_index": 1},
        {"source_id": 2, "architecture": "multi_agent", "new_run_index": 1},
        {"source_id": 3, "architecture": "baseline", "new_run_index": 2},
    ]


def test_archive_import_keeps_source_read_only_and_writes_remapped_rows(tmp_path: Path) -> None:
    source = tmp_path / "archive.sqlite3"
    target = tmp_path / "app.sqlite3"
    _make_archive(source)
    source_before = source.read_bytes()
    engine = create_engine(f"sqlite:///{target.as_posix()}")
    Base.metadata.create_all(engine)
    engine.dispose()

    mapping = migrate_archive(source, target)

    assert source.read_bytes() == source_before
    assert [item["new_run_index"] for item in mapping] == [1, 1, 2]
    engine = create_engine(f"sqlite:///{target.as_posix()}")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM experiment_runs")).scalar_one() == 3
        assert connection.execute(text("SELECT run_index FROM experiment_runs WHERE architecture='baseline' ORDER BY run_index")).all() == [(1,), (2,)]
    engine.dispose()