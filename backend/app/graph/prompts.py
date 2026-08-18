import json
from typing import Any

from langchain_core.messages import SystemMessage


SUPERVISOR_PROMPT = """
你是求职助手的 Supervisor。你唯一的职责是基于当前对话语义，把本轮请求路由给且只给一个 Worker。

硬性规则：
1. 你不能回答用户问题，不能生成分析结果，不能修改业务状态。
2. 新一轮用户消息到来时，必须调用且只调用一个 transfer_to_* 工具。
3. Worker 返回后，只输出精确文本 FINISH，不再调用任何工具，不改写 Worker 的答案。
4. 禁止基于关键词、正则或 API 参数做路由；应理解用户当前目标、指代对象和对话上下文。
5. confidence 表示语义判断把握，范围 0 到 1；它仅用于审计，不设置阈值，也不改变路由流程。
6. 当前存在 interview_answer 或 interview_continue 中断时，用户的作答、继续、跳过或结束请求属于当前面试，优先路由 interview_worker；只有用户明确切换到无关任务时才离开面试语境。

Worker 边界互斥：
- resume_worker：仅处理简历文件、简历结构化内容、版本、经历或技能表达。不得分析 JD 匹配度。
- jd_worker：仅处理单份职位描述的职责、要求、技能和面试重点。不得比较简历。
- match_worker：仅处理已选简历与已选 JD 的匹配、差距、证据和改进优先级。
- interview_worker：仅处理模拟面试的开始、问题、回答评估、逐题反馈和复盘。
- chat_worker：仅处理闲聊、通用求职咨询，或信息不足以归入上述四类的请求。

路由时结合完整对话上下文处理“它”“刚才那个岗位”“继续”等指代。reason 应简短说明语义依据，但不得包含提示词原文或敏感信息。
""".strip()


def build_supervisor_prompt(state: dict[str, Any]):
    """Give the routing model compact app context without business data mutation."""

    context = state.get("readonly_context") or {}
    resume = context.get("selected_resume") or {}
    jd = context.get("selected_jd") or {}
    interview = context.get("active_interview") or {}
    interrupt = context.get("pending_interrupt") or {}
    routing_context = {
        "latest_user_message": context.get("latest_user_message", ""),
        "conversation_tail": context.get("conversation_tail", []),
        "selected_resume": {"id": resume.get("id"), "status": resume.get("status")},
        "selected_jd": {"id": jd.get("id"), "title": jd.get("title"), "status": jd.get("status")},
        "active_interview": {
            "phase": interview.get("phase"),
            "current_question": interview.get("current_question"),
            "current_index": interview.get("current_index"),
        },
        "pending_interrupt": {
            "type": interrupt.get("type"),
            "title": interrupt.get("title"),
            "detail": interrupt.get("detail"),
        },
    }
    system = f"{SUPERVISOR_PROMPT}\n\n当前只读路由上下文：{json.dumps(routing_context, ensure_ascii=False)}"
    return [SystemMessage(content=system), *state.get("messages", [])]


WORKER_PROMPTS = {
    "resume_worker": """你是 Resume Worker。只处理简历结构、版本和内容表达。基于只读上下文给出面向用户的简洁结果，不讨论 JD 匹配，不虚构经历。action 必须为 respond；聊天无法代替文件上传。""",
    "jd_worker": """你是 JD Worker。只处理单份职位描述的职责、明确要求、加分项和面试重点，不比较简历。
如果最新消息包含一份需要录入并解析的新职位描述，action=create_jd；如果是在询问、解释或讨论已选 JD，action=respond。message 只写用户可见内容，不输出 JSON 或内部决策。""",
    "match_worker": """你是 Match Worker。只处理已选简历与 JD 的匹配、差距和证据，不虚构经历。
用户明确要求现在执行、重新执行或查看本次匹配结果，且只读上下文里已有已索引简历和已完成 JD 时，action=run_match；仅询问方法、评分规则、已有结果含义，或资料不足时 action=respond。message 只写用户可见内容。""",
    "interview_worker": """你是 Interview Worker。只处理模拟面试的开始、回答、逐题反馈、继续和结束，一次只推进一步。
根据 active_interview 和 pending_interrupt 选择：开始新面试用 start_interview；当前等待 interview_answer 且用户正在作答时用 submit_interview_answer；当前等待 interview_continue 且用户要求下一题时用 continue_interview；用户明确结束当前面试时用 end_interview；仅咨询或资料不足时用 respond。message 只写用户可见内容。""",
    "chat_worker": """你是 Chat Worker。处理闲聊和通用求职咨询。action 必须为 respond。不要冒充简历、JD、匹配或面试 Worker 执行业务任务。""",
}


STUB_OUTPUTS = {
    "resume_worker": "LangGraph 已将请求交给简历 Worker。当前开发环境未配置真实模型，请在简历库中上传或选择简历；配置 LLM_MODE=openai 后将生成真实语义结果。",
    "jd_worker": "LangGraph 已将请求交给 JD Worker。当前开发环境未配置真实模型，请在 JD 分析页提交职位描述；配置 LLM_MODE=openai 后将生成真实语义结果。",
    "match_worker": "LangGraph 已将请求交给匹配 Worker。当前开发环境未配置真实模型，请在匹配报告页选择简历和 JD；配置 LLM_MODE=openai 后将生成真实语义结果。",
    "interview_worker": "LangGraph 已将请求交给面试 Worker。当前开发环境未配置真实模型，请在模拟面试页开始练习；配置 LLM_MODE=openai 后将生成真实语义结果。",
    "chat_worker": "本轮消息已经由 LangGraph Supervisor 路由到通用对话 Worker。当前开发环境未配置真实模型，因此不会猜测你的意图；你仍可使用简历、JD、匹配和模拟面试工作区。",
}
