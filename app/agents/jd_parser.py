"""JD 解析 Agent。

本节点由 Supervisor 分发后调用：它通过 LangChain structured output 抽取冻结的
``JDParsed``，执行原文证据门卫校验，并仅在用户授权时调用公司搜索 Tool。
它不负责简历匹配、评分或后续业务路由。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from app.schemas.jd import JDParseInput, JDParsed, SkillRequirement
from app.schemas.state import ErrorEntry, ExecutionEvent, JobAssistantState
from app.services.observability import redact_text
from app.tools.company_search import CompanySearchItem, SearchBackend, search_company_background

CONTENT_INSUFFICIENT_CODE = "JD_CONTENT_INSUFFICIENT"
EXTRACTION_UNAVAILABLE_CODE = "JD_EXTRACTION_UNAVAILABLE"
WEB_SEARCH_DEGRADED_CODE = "WEB_SEARCH_DEGRADED"
MAX_EXTRACTION_ATTEMPTS = 2
UNLOCATABLE_SKILLS_MESSAGE = "未能从JD原文中定位到可靠的技能要求,建议人工检查原始JD。"
EXTRACTION_UNAVAILABLE_MESSAGE = "请重新粘贴完整的职位描述，建议包含岗位名称、职责和任职要求；系统不会根据不完整信息猜测技能要求。"


def jd_parser_node(
    state: JobAssistantState,
    chat_model: BaseChatModel,
    search_backend: SearchBackend | None = None,
) -> dict[str, object]:
    """执行 JD 结构化抽取。

    参数：
        state: 当前 LangGraph 全局状态，需包含 `user_input` 或已写入的 `jd_text`。
        chat_model: 已构建好的聊天模型实例，由外部注入。
        search_backend: 可选公司搜索 backend；未注入时不会执行真实搜索。

    返回：
        仅包含 State update 的字典；业务上只写 `jd_parsed`，同时补充共享可观测字段。
    """

    jd_input = JDParseInput(
        jd_text=str(state.get("user_input", "")),
        allow_web_search=bool(state.get("allow_web_search", False)),
    )
    parsed, retry_count, extraction_errors = _extract_jd_with_retry(chat_model, jd_input)

    if parsed is None:
        jd_parsed = _build_technical_degraded_result(jd_input)
        return {
            "jd_parsed": jd_parsed,
            "current_node": "jd_parser",
            "retry_count": {"jd_parser": retry_count},
            "error_log": extraction_errors
            + [
                _build_error_entry(
                    code=EXTRACTION_UNAVAILABLE_CODE,
                    message="JD structured extraction was unavailable after all retries",
                    retryable=False,
                    attempt=retry_count,
                )
            ],
            "execution_history": [_build_event("jd_parser", "success", "technical_degraded_manual_review_required")],
        }

    gated = _apply_guardrails(parsed, jd_input.jd_text)
    error_log = extraction_errors
    execution_detail = "parsed"

    if _is_content_insufficient(gated):
        execution_detail = "content_insufficient"
        error_log.append(
            _build_error_entry(
                code=CONTENT_INSUFFICIENT_CODE,
                message="JD does not contain enough concrete requirements for resume matching",
                retryable=False,
                attempt=retry_count,
            )
        )

    if jd_input.allow_web_search and gated.company_name and search_backend is not None:
        search_result = search_company_background(gated.company_name, search_backend)
        gated = gated.model_copy(
            update={
                "company_context": [_format_company_context(item) for item in search_result.items],
            }
        )
        if search_result.degraded:
            error_log.append(
                _build_error_entry(
                    code=WEB_SEARCH_DEGRADED_CODE,
                    message=f"Company search degraded after {search_result.attempts} attempts",
                    retryable=False,
                    attempt=search_result.attempts - 1,
                )
            )
    else:
        gated = gated.model_copy(update={"company_context": [], "company_name": gated.company_name})

    return {
        "jd_parsed": gated,
        "current_node": "jd_parser",
        "retry_count": {"jd_parser": retry_count},
        "error_log": error_log,
        "execution_history": [_build_event("jd_parser", "success", execution_detail)],
    }


def _build_jd_prompt(jd_input: JDParseInput) -> str:
    """构造 JD 抽取 Prompt。"""

    return (
        "You are a JD parser. Extract only facts supported by the JD text.\n"
        "Every skill must include evidence quoted from the original JD text.\n"
        "Use priority=must only for explicit hard requirements such as 必须, 要求, 熟练掌握, 3年以上.\n"
        "Use priority=preferred for 优先, 加分, 熟悉者优先 and similar wording.\n"
        "Use priority=inferred only when clearly marking it as inferred from responsibilities.\n"
        "If the JD lacks concrete requirements, keep skills empty and explain in ambiguities.\n"
        f"Language: {jd_input.language}\n"
        f"Allow web search: {jd_input.allow_web_search}\n"
        f"JD text:\n{jd_input.jd_text}"
    )


def _extract_jd_with_retry(
    chat_model: BaseChatModel,
    jd_input: JDParseInput,
) -> tuple[JDParsed | None, int, list[ErrorEntry]]:
    """用 Pydantic 结构化输出抽取 JD，最多调用两次。

    参数：
        chat_model: 支持 ``with_structured_output`` 的 LangChain 聊天模型。
        jd_input: 已完成基础长度校验的 JD 输入。

    返回：
        ``(parsed, retry_count, errors)``。两次都失败时 ``parsed`` 为 ``None``，
        调用方据此构造可审计的最小降级对象，而不让异常中断 LangGraph。
    """
    structured_model = chat_model.with_structured_output(JDParsed)
    prompt = _build_jd_prompt(jd_input)
    errors: list[ErrorEntry] = []

    for attempt in range(MAX_EXTRACTION_ATTEMPTS):
        try:
            result = structured_model.invoke(prompt)
            if not isinstance(result, JDParsed):
                # 正常 LangChain 实现会返回 JDParsed；此处也保护不规范 Provider/Mock。
                result = JDParsed.model_validate(result)
            return result, attempt, errors
        # Provider 超时、网络断连和 Tool Calling 参数校验都会以不同异常类型暴露；
        # 节点必须把它们收敛为可审计降级结果，不能中断整张 LangGraph。
        except Exception as exc:
            errors.append(
                _build_error_entry(
                    code="LLM_SCHEMA_INVALID",
                    message=str(exc),
                    retryable=attempt < MAX_EXTRACTION_ATTEMPTS - 1,
                    attempt=attempt,
                )
            )
            if attempt == MAX_EXTRACTION_ATTEMPTS - 1:
                break
            # structured output 已绑定 Schema；重试只补充事实约束，不退回文本 JSON 解析。
            prompt = (
                f"{_build_jd_prompt(jd_input)}\n"
                "上次结构化提取未通过校验。请仅输出能由原文支持的字段："
                "缺失信息使用空列表或 null，不得虚构技能证据。"
            )

    return None, MAX_EXTRACTION_ATTEMPTS - 1, errors


def _apply_guardrails(parsed: JDParsed, jd_text: str) -> JDParsed:
    """对模型输出执行确定性门卫校验。"""

    filtered_skills: list[SkillRequirement] = []
    ambiguities = list(parsed.ambiguities)

    for skill in parsed.skills:
        if skill.evidence not in jd_text:
            ambiguities.append(f"Dropped skill {skill.name} because evidence was not found in JD text")
            continue

        priority = _normalize_priority(skill.priority, skill.evidence)
        filtered_skills.append(skill.model_copy(update={"priority": priority}))

    company_name = parsed.company_name if _has_explicit_company_name(parsed.company_name) else None
    company_context = parsed.company_context if company_name else []

    normalized = parsed.model_copy(
        update={
            "company_name": company_name,
            "company_context": company_context,
            "skills": filtered_skills,
            "ambiguities": ambiguities,
        }
    )

    if not normalized.skills and UNLOCATABLE_SKILLS_MESSAGE not in normalized.ambiguities:
        normalized = normalized.model_copy(
            update={"ambiguities": normalized.ambiguities + [UNLOCATABLE_SKILLS_MESSAGE]}
        )

    if not normalized.skills and not _has_meaningful_requirements(normalized):
        normalized = normalized.model_copy(
            update={
                "ambiguities": normalized.ambiguities
                + [f"{CONTENT_INSUFFICIENT_CODE}: 未识别到明确的岗位技能或职责要求"],
            }
        )

    return normalized


def _normalize_priority(priority: str, evidence: str) -> str:
    """根据原文证据对技能优先级做确定性校正。"""

    lowered = evidence.lower()
    if any(keyword in evidence for keyword in ("优先", "加分")) or "preferred" in lowered:
        return "preferred"
    return priority


def _has_explicit_company_name(company_name: str | None) -> bool:
    """判断公司名是否为明确可用值。"""

    return company_name is not None and bool(company_name.strip())


def _has_meaningful_requirements(parsed: JDParsed) -> bool:
    """判断结果中是否存在明确岗位要求。"""

    return any(
        [
            parsed.skills,
            parsed.responsibilities,
            parsed.experience_requirements,
            parsed.education_requirements,
            parsed.interview_focus,
        ]
    )


def _build_technical_degraded_result(jd_input: JDParseInput) -> JDParsed:
    """构造结构化抽取连续失败后的最小 JDParsed。

    降级路径不使用关键词或正则猜测岗位标题；需要语义判断的字段一律留空，
    避免将猜测当作结构化事实传入下游。
    """

    return JDParsed(
        job_title="unknown",
        seniority="unknown",
        company_name=None,
        responsibilities=[],
        skills=[],
        experience_requirements=[],
        education_requirements=[],
        interview_focus=[],
        company_context=[],
        ambiguities=[
            f"{EXTRACTION_UNAVAILABLE_CODE}: 自动解析未能可靠完成。",
            EXTRACTION_UNAVAILABLE_MESSAGE,
        ],
        source_language=jd_input.language,
    )


def _is_content_insufficient(parsed: JDParsed) -> bool:
    """判断当前结果是否属于内容不足降级。"""

    return any(item.startswith(f"{CONTENT_INSUFFICIENT_CODE}:") for item in parsed.ambiguities)


def _format_company_context(item: CompanySearchItem) -> str:
    """把公司搜索结果格式化为 `company_context` 条目。"""

    return f"{item.title} | {item.url} | {item.snippet} | {item.fetched_at}"


def _build_event(node: str, event: str, detail: str) -> ExecutionEvent:
    """构造执行轨迹事件。"""

    return {
        "node": node,
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "detail": detail,
    }


def _build_error_entry(code: str, message: str, retryable: bool, attempt: int) -> ErrorEntry:
    """构造符合 ErrorEntry 契约的错误记录。"""

    return {
        "code": code,
        "node": "jd_parser",
        "message": redact_text(message),
        "retryable": retryable,
        "attempt": attempt,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "raw_output_excerpt": None,
    }