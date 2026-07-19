"""实验运行数据访问。

本模块封装 `experiment_runs` 的写入与基础查询，供实验脚本和测试复用；
调用方传入业务 Session，本仓储不负责创建 Engine 或管理外层事务生命周期。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ExperimentRun


class ExperimentRunAlreadyExistsError(ValueError):
    """表示同一实验采样五元组已经存在，调用方必须显式决定如何处理。"""

    code = "EXPERIMENT_RUN_ALREADY_EXISTS"


class ExperimentRunRepository:
    """`experiment_runs` 仓储。

    做什么：
        把实验脚本的一次运行结果转换为 ORM 实体并持久化，避免脚本直接操作 SQL。
    关键参数：
        session: 调用方提供的业务 Session，用于控制事务边界。
    返回值：
        `create_run` 返回已写入主键的 `ExperimentRun` 实体。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_run(self, row: Mapping[str, Any]) -> ExperimentRun:
        """持久化一次实验运行记录。"""

        key = {
            "case_name": str(row["case_name"]),
            "architecture": str(row["architecture"]),
            "model_name": str(row["model_name"]),
            "prompt_version": str(row["prompt_version"]),
            "run_index": int(row["run_index"]),
        }
        if self._session.scalar(select(ExperimentRun).filter_by(**key)) is not None:
            raise ExperimentRunAlreadyExistsError(f"{ExperimentRunAlreadyExistsError.code}: {key}")

        entity = ExperimentRun(
            **key,
            status=str(row["status"]),
            schema_valid=bool(row["schema_valid"]),
            unsupported_skill_claims=(None if row.get("unsupported_skill_claims") is None else int(row["unsupported_skill_claims"])),
            llm_calls=int(row["llm_calls"]),
            estimated_tokens=int(row["estimated_tokens"]),
            latency_ms=float(row["latency_ms"]),
            error_codes=str(row["error_codes"]),
            output_json=None if row.get("output_json") is None else str(row["output_json"]),
            created_at=_coerce_datetime(row["created_at"]),
        )
        self._session.add(entity)
        self._session.commit()
        self._session.refresh(entity)
        return entity


def _coerce_datetime(value: Any) -> datetime:
    """把脚本传入的时间值规范化为 `datetime`。"""

    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError("created_at must be a datetime or ISO-8601 string")