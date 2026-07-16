"""结构化日志基础设施。

本模块由后续 Graph 节点观察器调用，负责 JSON Lines 输出、事件关联 ID 与脱敏；
不依赖 LangGraph State，也不改变任何节点的执行或异常语义。
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

DEFAULT_LOG_DIR = "./logs"
DEFAULT_LOG_LEVEL = "INFO"
LOG_FILE_NAME = "job-assistant.jsonl"
LOG_RETENTION_DAYS = 14
MAX_REDACTED_TEXT_LENGTH = 500

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