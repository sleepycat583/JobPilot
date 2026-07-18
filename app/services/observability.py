"""结构化日志基础设施。

本模块由后续 Graph 节点观察器调用，负责 JSON Lines 输出、事件关联 ID 与脱敏；
不依赖 LangGraph State，也不改变任何节点的执行或异常语义。
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from langgraph.errors import GraphInterrupt

from app.constants import MAX_FORMAT_RETRIES

DEFAULT_LOG_DIR = "./logs"
DEFAULT_LOG_LEVEL = "INFO"
LOG_FILE_NAME = "job-assistant.jsonl"
LOG_RETENTION_DAYS = 14
MAX_REDACTED_TEXT_LENGTH = 500

_DEFAULT_EVENT_PUBLISHER: Callable[[dict[str, Any]], None] | None = None

_EMAIL_PATTERN = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|authorization|bearer\s+token|token|password|secret)\b\s*[:=]\s*([^\s,;]+)"
)
_AUTHORIZATION_BEARER_PATTERN = re.compile(r"(?i)\bauthorization\b\s*[:=]\s*bearer\s+([^\s,;]+)")


class JsonLineFormatter(logging.Formatter):
    """把字典日志载荷编码为单行 JSON，保证 stdout 与文件格式一致。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = record.msg if isinstance(record.msg, dict) else {"message": str(record.getMessage())}
        normalized = dict(payload)
        normalized.setdefault("timestamp", utc_now_iso())
        normalized.setdefault("level", record.levelname)
        return json.dumps(normalized, ensure_ascii=False, default=_json_default, separators=(",", ":"))


class SafeTimedRotatingFileHandler(TimedRotatingFileHandler):
    """文件不可写时静默降级，绝不向业务节点传播日志 I/O 异常。"""

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802 - logging 固定 API。
        del record


def configure_structured_logger(
    *,
    log_dir: str | Path = DEFAULT_LOG_DIR,
    level: str = DEFAULT_LOG_LEVEL,
    logger_name: str = "job_assistant",
    include_stdout: bool = True,
) -> logging.Logger:
    """配置 stdout 与按日滚动 JSONL 文件 handler。

    参数：log_dir 为日志目录；level 为标准 logging 级别；include_stdout 供测试或嵌入式调用关闭。
    返回：可直接以字典作为 `logger.info/error` 参数的独立 logger。
    """
    logger = logging.getLogger(logger_name)
    logger.setLevel(_resolve_log_level(level))
    logger.propagate = False
    _close_handlers(logger)
    formatter = JsonLineFormatter()

    if include_stdout:
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setFormatter(formatter)
        logger.addHandler(stdout_handler)

    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    file_handler = SafeTimedRotatingFileHandler(
        directory / LOG_FILE_NAME,
        when="midnight",
        interval=1,
        backupCount=LOG_RETENTION_DAYS,
        encoding="utf-8",
        utc=True,
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def configure_event_publisher(event_publisher: Callable[[dict[str, Any]], None] | None) -> None:
    """配置 observer 使用的默认事件总线发布入口。

    参数：
        event_publisher: 供 observer 额外发布 SSE 同源事件的可调用对象；传 `None` 表示关闭发布。
    返回：
        无；仅更新模块级默认发布器，供 `observe_node` 在未显式注入时读取。
    """

    global _DEFAULT_EVENT_PUBLISHER
    _DEFAULT_EVENT_PUBLISHER = event_publisher


def build_log_event(
    *,
    event: str,
    session_id: str,
    thread_id: str,
    node: str | None = None,
    node_kind: str | None = None,
    node_run_id: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    duration_ms: int | None = None,
    input_summary: str | None = None,
    success: bool | None = None,
    error_code: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """构造稳定、脱敏的单条结构化日志事件。

    每次调用生成唯一 event_id；同一次节点调用由调用方传入同一 node_run_id 关联。
    """
    return {
        "timestamp": utc_now_iso(),
        "event": event,
        "event_id": str(uuid4()),
        "session_id": session_id,
        "thread_id": thread_id,
        "node": node,
        "node_kind": node_kind,
        "node_run_id": node_run_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": duration_ms,
        "input_summary": redact_text(input_summary or ""),
        "success": success,
        "error_code": error_code,
        **{key: redact_text(value) if isinstance(value, str) else value for key, value in extra.items()},
    }


def build_safe_input_summary(state: Mapping[str, Any], node: str) -> str:
    """只从状态元数据提取节点输入摘要，绝不包含原始 JD、简历或回答文本。"""
    parts = [f"node={node}"]
    user_input = state.get("user_input")
    if isinstance(user_input, str):
        parts.append(f"user_input_length={len(user_input)}")
    resume_version = state.get("resume_version")
    if isinstance(resume_version, str):
        parts.append(f"resume_version={redact_text(resume_version)}")
    parts.extend((f"has_jd={state.get('jd_parsed') is not None}", f"has_match={state.get('match_result') is not None}"))
    return "; ".join(parts)


def redact_text(value: str) -> str:
    """脱敏手机号、邮箱和常见凭证，并限制可能包含用户输入的文本长度。"""
    redacted = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", value)
    redacted = _PHONE_PATTERN.sub("[REDACTED_PHONE]", redacted)
    redacted = _AUTHORIZATION_BEARER_PATTERN.sub("Authorization: Bearer [REDACTED_SECRET]", redacted)
    redacted = _SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=[REDACTED_SECRET]", redacted)
    if len(redacted) > MAX_REDACTED_TEXT_LENGTH:
        return f"{redacted[:MAX_REDACTED_TEXT_LENGTH]}...[TRUNCATED]"
    return redacted


def utc_now_iso() -> str:
    """返回日志使用的 UTC ISO 8601 时间戳。"""
    return datetime.now(timezone.utc).isoformat()


def new_node_run_id() -> str:
    """生成关联一次节点开始与终态日志的 UUIDv4。"""
    return str(uuid4())


def observe_node(node_name: str, node_kind: str, function: Any, logger: logging.Logger | None = None, *, capture_exceptions: bool = True) -> Any:
    """包装 Graph 节点，记录生命周期并将节点返回的 ErrorEntry 同源写入 JSONL。

    未处理异常会转换为普通 State update，由 Builder 的错误守卫路由至 `error_node`；
    `GraphInterrupt` 是 HITL 控制流，记录中断后必须原样传播。
    """
    event_logger = logger or configure_structured_logger()

    def wrapper(state: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        configurable = (config or {}).get("configurable", {})
        session_id = str(configurable.get("session_id", "session-unassigned"))
        thread_id = str(configurable.get("thread_id", state.get("thread_id", "thread-unassigned")))
        started_at = utc_now_iso()
        started_perf = time.perf_counter()
        node_run_id = new_node_run_id()
        input_summary = build_safe_input_summary(state, node_name)
        started_event = build_log_event(event="agent_node_started", session_id=session_id, thread_id=thread_id, node=node_name, node_kind=node_kind, node_run_id=node_run_id, started_at=started_at, input_summary=input_summary, success=None)
        event_logger.info(started_event)
        _publish_bus_event(started_event, mapped_event="node_started", detail=str(started_event.get("input_summary", "")))
        try:
            update = dict(function(state))
        except GraphInterrupt:
            interrupted_event = build_log_event(event="agent_node_interrupted", session_id=session_id, thread_id=thread_id, node=node_name, node_kind=node_kind, node_run_id=node_run_id, started_at=started_at, ended_at=utc_now_iso(), duration_ms=_elapsed_ms(started_perf), input_summary=input_summary, success=None)
            event_logger.info(interrupted_event)
            _publish_bus_event(interrupted_event, mapped_event="interrupt_required", detail=str(interrupted_event.get("input_summary", "")))
            raise
        except Exception as exc:
            if not capture_exceptions:
                raise
            entry = {
                "code": "UNHANDLED_NODE_EXCEPTION", "node": node_name, "message": redact_text(str(exc)),
                "retryable": False, "attempt": int(state.get("retry_count", {}).get(node_name, 0)),
                "timestamp": utc_now_iso(), "raw_output_excerpt": None,
            }
            _log_error(event_logger, entry, session_id, thread_id, node_kind, node_run_id, started_at, started_perf, input_summary)
            return {"current_node": node_name, "error_log": [entry], "execution_history": [_execution_error_event(node_name, entry)]}

        for entry in update.get("error_log", []):
            if isinstance(entry, dict):
                _normalize_error_entry(entry)
                failed_event = _log_error(event_logger, entry, session_id, thread_id, node_kind, node_run_id, started_at, started_perf, input_summary)
                if bool(entry.get("retryable")) and int(entry.get("attempt", 0)) < MAX_FORMAT_RETRIES:
                    # 当前 node_retrying 只能在节点返回后按 error_log 顺序统一派生，
                    # 无法在下一次模型调用开始前实时发出；这是现有 structured-output 返回时机带来的约束。
                    _publish_bus_event(failed_event, mapped_event="node_retrying", detail=str(failed_event.get("message") or failed_event.get("error_code") or ""))
        finished_event = build_log_event(event="agent_node_finished", session_id=session_id, thread_id=thread_id, node=node_name, node_kind=node_kind, node_run_id=node_run_id, started_at=started_at, ended_at=utc_now_iso(), duration_ms=_elapsed_ms(started_perf), input_summary=input_summary, success=True)
        event_logger.info(finished_event)
        _publish_bus_event(finished_event, mapped_event="node_finished", detail=str(finished_event.get("input_summary", "")))
        return update

    return wrapper


def _normalize_error_entry(entry: dict[str, Any]) -> None:
    """就地脱敏 ErrorEntry，使 State 与 JSONL 共用同一个安全对象。"""
    for field_name in ("message", "raw_output_excerpt"):
        value = entry.get(field_name)
        if isinstance(value, str):
            entry[field_name] = redact_text(value)


def _log_error(logger: logging.Logger, entry: Mapping[str, Any], session_id: str, thread_id: str, node_kind: str, node_run_id: str, started_at: str, started_perf: float, input_summary: str) -> dict[str, Any]:
    event = build_log_event(event="agent_node_failed", session_id=session_id, thread_id=thread_id, node=str(entry["node"]), node_kind=node_kind, node_run_id=node_run_id, started_at=started_at, ended_at=utc_now_iso(), duration_ms=_elapsed_ms(started_perf), input_summary=input_summary, success=False, error_code=str(entry["code"]), attempt=entry["attempt"], message=str(entry["message"]), raw_output_excerpt=entry.get("raw_output_excerpt"), error_entry=entry)
    logger.warning(event)
    return event


def _execution_error_event(node_name: str, entry: Mapping[str, Any]) -> dict[str, str]:
    return {"node": node_name, "event": "error", "timestamp": str(entry["timestamp"]), "detail": str(entry["code"])}


def _elapsed_ms(started_perf: float) -> int:
    return round((time.perf_counter() - started_perf) * 1000)


def _close_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def _resolve_log_level(level: str) -> int:
    normalized = logging.getLevelName(level.upper())
    if not isinstance(normalized, int):
        raise ValueError(f"Unsupported log level: {level}")
    return normalized


def _json_default(value: Any) -> str:
    return str(value)


def _publish_bus_event(source_event: Mapping[str, Any], *, mapped_event: str, detail: str) -> None:
    """尽力把日志同源事件额外发布到事件总线。

    为什么这样做：
        事件总线是附加的 SSE 基础设施，发布失败不得影响 Graph 正常执行或 JSONL 日志写入。
        因此这里必须静默降级，只保留原有日志与业务语义。
    """

    if _DEFAULT_EVENT_PUBLISHER is None:
        return
    try:
        payload = dict(source_event)
        payload["event"] = mapped_event
        payload["detail"] = redact_text(detail)
        _DEFAULT_EVENT_PUBLISHER(payload)
    except Exception:
        return