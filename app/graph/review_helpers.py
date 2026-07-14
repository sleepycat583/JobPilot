"""Review 控制流的确定性辅助函数。

本模块由 Review Gate 和 Matcher 调用，只读取结构化执行历史，不调用 LLM 或外部服务。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.schemas.state import ExecutionEvent


def next_match_business_attempt(execution_history: list["ExecutionEvent"] | list[dict[str, Any]]) -> int:
    """计算下一次完成简历评分时应使用的业务 attempt 编号。

    参数：
        execution_history: LangGraph reducer 追加保存的结构化执行轨迹。

    返回：
        从 1 开始的下一次成功 `resume_matcher` 评分编号。

    仅成功产出评分结果的 Matcher 运行占用业务 attempt；节点错误、LLM 重试和其他
    节点事件不计入，避免把技术重试混同于用户触发的重新评分。
    """

    completed_matches = sum(
        event["node"] == "resume_matcher" and event["event"] == "success"
        for event in execution_history
    )
    return completed_matches + 1