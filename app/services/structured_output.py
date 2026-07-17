"""结构化输出重试服务。

本文件封装通用的“调用模型 -> 解析为 Pydantic 对象 -> 失败后按固定策略重试”的技术流程，
不包含任何 Supervisor、JD、匹配等具体业务逻辑。

`StructuredPromptContext` 与 `StructuredOutputResult` 放在这里而不是 `schemas/` 目录，
因为它们是本服务内部使用的技术性载体，不是文档 §2.5 定义的业务 Schema，
因此不进入统一 Schema 层。

重试语义固定如下：

| attempt | 含义 | Prompt 策略 | 失败行为 |
|---|---|---|---|
| 0 | 首次调用 | 完整业务 Prompt + 目标 JSON Schema | 记录第 1 条 `LLM_SCHEMA_INVALID`，构造字段级修复 Prompt |
| 1 | 第 1 次重试 | 字段级错误 + 合法 JSON Schema + 上次输出片段 + 完整任务上下文 | 记录第 2 条错误，切换到最小上下文 Prompt |
| 2 | 第 2 次也是最后一次重试 | 最小任务输入 + JSON Schema + 上次错误 + 截断输出，并通过 `bind(temperature=0)` 生成临时绑定模型 | 记录不可重试错误并停止调用，不再出现第 4 次调用 |

因此总调用次数上限是：首次调用 1 次 + 最多重试 2 次 = 最多 3 次模型调用。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from json import JSONDecodeError
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from app.schemas.state import ErrorEntry
from app.services.observability import redact_text

SchemaT = TypeVar("SchemaT", bound=BaseModel)
MAX_EXCERPT_LENGTH = 500


@dataclass(frozen=True)
class StructuredPromptContext:
    """结构化输出重试所需的 Prompt 上下文。

    参数：
        full_prompt: 首次调用与第一次重试使用的完整业务 Prompt。
        minimal_input: 第二次重试使用的最小任务输入，避免继续携带冗长上下文。
    """

    full_prompt: str
    minimal_input: str


@dataclass(frozen=True)
class StructuredOutputResult(Generic[SchemaT]):
    """结构化输出调用结果。

    参数：
        value: 成功时的目标 Pydantic 对象；三次失败后的降级路径返回 `None`。
        retry_count: 实际发生的重试次数，取值为 0/1/2。
        error_log: 本次调用产生的结构化错误记录列表，供调用方直接追加进 state。
        degraded: 是否已进入节点级降级路径。
    """

    value: SchemaT | None
    retry_count: int
    error_log: list[ErrorEntry]
    degraded: bool


def call_with_structured_output(
    chat_model: Any,
    schema: type[SchemaT],
    prompt_context: StructuredPromptContext,
    node_name: str,
) -> StructuredOutputResult[SchemaT]:
    """调用模型并按固定语义解析结构化输出。

    参数：
        chat_model: 具备 `invoke()`，且可选具备 `bind()` 的聊天模型对象。
        schema: 目标 Pydantic Schema 类型。
        prompt_context: 三阶段重试所需的 Prompt 上下文。
        node_name: 当前节点名，用于产出 ErrorEntry。

    返回：
        StructuredOutputResult[SchemaT]：成功时包含解析后的对象；三次失败时返回降级结果。
    """

    error_log: list[ErrorEntry] = []
    current_prompt = prompt_context.full_prompt
    last_raw_output: str | None = None
    bound_model = chat_model

    for attempt in range(3):
        if attempt == 2 and hasattr(chat_model, "bind"):
            bound_model = chat_model.bind(temperature=0)

        try:
            raw_response = bound_model.invoke(current_prompt)
            raw_text = _extract_response_text(raw_response)
            last_raw_output = raw_text
            parsed = schema.model_validate_json(raw_text)
            return StructuredOutputResult(
                value=parsed,
                retry_count=attempt,
                error_log=error_log,
                degraded=False,
            )
        except (ValidationError, JSONDecodeError, TypeError, ValueError) as exc:
            entry = _build_error_entry(
                node_name=node_name,
                attempt=attempt,
                retryable=attempt < 2,
                raw_output=last_raw_output,
                message=str(exc),
            )
            error_log.append(entry)

            if attempt == 2:
                return StructuredOutputResult(
                    value=None,
                    retry_count=2,
                    error_log=error_log,
                    degraded=True,
                )

            current_prompt = _build_retry_prompt(
                attempt=attempt,
                schema=schema,
                prompt_context=prompt_context,
                validation_message=str(exc),
                previous_output=last_raw_output,
            )

    return StructuredOutputResult(value=None, retry_count=2, error_log=error_log, degraded=True)


def _extract_response_text(raw_response: Any) -> str:
    """从模型响应对象中提取字符串内容。"""

    if isinstance(raw_response, str):
        return raw_response

    content = getattr(raw_response, "content", None)
    if isinstance(content, str):
        return content

    raise TypeError("Model response must be a string or expose string content")


def _build_retry_prompt(
    *,
    attempt: int,
    schema: type[SchemaT],
    prompt_context: StructuredPromptContext,
    validation_message: str,
    previous_output: str | None,
) -> str:
    """根据失败次数构造下一轮修复 Prompt。"""

    schema_json = schema.model_json_schema()
    previous_excerpt = _safe_excerpt(previous_output)

    if attempt == 0:
        return (
            "Return only a valid JSON object that matches the target schema.\n"
            f"Validation errors: {validation_message}\n"
            f"JSON Schema: {schema_json}\n"
            f"Previous output: {previous_excerpt}\n"
            f"Original task context:\n{prompt_context.full_prompt}"
        )

    return (
        "Return only a corrected JSON object. Use the minimal task input below.\n"
        "Temperature instruction: 0\n"
        f"Validation errors: {validation_message}\n"
        f"JSON Schema: {schema_json}\n"
        f"Previous output: {previous_excerpt}\n"
        f"Minimal task input:\n{prompt_context.minimal_input}"
    )


def _build_error_entry(
    *,
    node_name: str,
    attempt: int,
    retryable: bool,
    raw_output: str | None,
    message: str,
) -> ErrorEntry:
    """构造符合 ErrorEntry 契约的错误记录。"""

    return ErrorEntry(
        code="LLM_SCHEMA_INVALID",
        node=node_name,
        message=redact_text(message),
        retryable=retryable,
        attempt=attempt,
        timestamp=_utc_now_iso(),
        raw_output_excerpt=_safe_excerpt(raw_output),
    )


def _safe_excerpt(raw_output: str | None) -> str | None:
    """脱敏并截断原始输出，避免错误日志泄露或失控。"""

    if raw_output is None:
        return None
    # 先脱敏再限制总长度，避免截断前后的片段都进入 State 或日志。
    return redact_text(raw_output)[:MAX_EXCERPT_LENGTH]


def _utc_now_iso() -> str:
    """返回 UTC ISO 8601 时间戳。"""

    return datetime.now(timezone.utc).isoformat()
