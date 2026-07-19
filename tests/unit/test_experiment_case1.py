"""Week2 Case 1 对比实验脚本的 SQLite smoke test。"""

import importlib.util
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, text


def _load_experiment_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "experiment_case1.py"
    spec = importlib.util.spec_from_file_location("experiment_case1", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeChatModel:
    """返回可通过 JD Schema 和 Worker 门卫的固定 JSON，不访问网络。"""

    def invoke(self, _: str) -> str:
        return json.dumps(
            {
                "job_title": "Python 后端工程师",
                "seniority": "mid",
                "company_name": None,
                "responsibilities": ["负责 RESTful API 设计与实现"],
                "skills": [
                    {"name": "Python", "category": "language", "priority": "must", "evidence": "熟练掌握 Python"},
                    {"name": "Redis", "category": "database", "priority": "must", "evidence": "PostgreSQL、Redis"},
                ],
                "experience_requirements": ["3 年及以上 Python 后端开发经验"],
                "education_requirements": ["本科及以上学历"],
                "interview_focus": ["缓存策略"],
                "company_context": [],
                "ambiguities": [],
                "source_language": "zh-CN",
            },
            ensure_ascii=False,
        )


def test_case1_experiment_smoke_writes_baseline_and_multi_agent_runs(tmp_path: Path) -> None:
    module = _load_experiment_module()
    database_path = tmp_path / "experiments.sqlite3"

    records = module.run_case1_experiment(
        FakeChatModel(),
        database_path=database_path,
        model_name="fake-model",
        repeats=3,
    )

    assert len(records) == 6
    assert {record["architecture"] for record in records} == {"baseline", "multi_agent"}
    assert {record["status"] for record in records} == {"success"}
    assert "normal schema success" in module.format_summary(records)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT architecture, COUNT(*), MIN(schema_valid), MIN(llm_calls) FROM experiment_runs GROUP BY architecture")
        ).fetchall()
    engine.dispose()
    assert rows == [("baseline", 3, 1, 1), ("multi_agent", 3, 1, 1)]


def test_case1_experiment_continues_run_index_for_existing_prefix(tmp_path: Path) -> None:
    module = _load_experiment_module()
    database_path = tmp_path / "experiments.sqlite3"

    first_records = module.run_case1_experiment(
        FakeChatModel(),
        database_path=database_path,
        model_name="fake-model",
        repeats=2,
    )
    second_records = module.run_case1_experiment(
        FakeChatModel(),
        database_path=database_path,
        model_name="fake-model",
        repeats=2,
    )

    assert [record["run_index"] for record in first_records if record["architecture"] == "baseline"] == [1, 2]
    assert [record["run_index"] for record in second_records if record["architecture"] == "baseline"] == [3, 4]
    assert [record["run_index"] for record in second_records if record["architecture"] == "multi_agent"] == [3, 4]