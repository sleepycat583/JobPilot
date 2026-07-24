"""LangGraph 全局 State。

本文件只定义文档 §5.1 冻结的 State 结构，不包含任何业务节点逻辑。
"""

from operator import add
from typing import Annotated, Literal

from typing_extensions import NotRequired, TypedDict

from app.schemas.interview import InterviewState
from app.schemas.jd import JDParsed
from app.schemas.resume import MatchResult, MatchUnavailableResult
from app.schemas.router import RouterDecision
from app.schemas.review import ReviewStatus


class ExecutionEventMetadata(TypedDict, total=False):
    """执行轨迹的结构化扩展字段。

    业务节点在需要记录可被程序读取的执行上下文时使用本字段；`detail` 仅保留
    面向人的简短说明，不能被解析后用于业务分支。
    """

    business_attempt: int
    resume_id: str
    total_score: float
    original_task_queue: list[str]
    normalized_task_queue: list[str]


class ExecutionEvent(TypedDict):
    """执行轨迹事件。"""

    node: str
    event: Literal["enter", "success", "error", "interrupt", "resume"]
    timestamp: str
    detail: str
    metadata: NotRequired[ExecutionEventMetadata]


class ErrorEntry(TypedDict):
    """结构化错误记录。"""

    code: str
    node: str
    message: str
    retryable: bool
    attempt: int
    timestamp: str
    raw_output_excerpt: str | None


class JobAssistantState(TypedDict, total=False):
    """LangGraph 全局状态。

    三个业务产物字段必须相互独立，避免后续节点覆盖前序结果。
    """

    thread_id: str
    user_input: str
    messages: list[dict]
    route_decision: RouterDecision | None
    task_queue: list[str]

    jd_parsed: JDParsed | None
    match_result: MatchResult | MatchUnavailableResult | None
    interview_state: InterviewState | None
    interview_next_action: str | None
    interview_follow_up_of: str | None
    interview_completion_reason: Literal["target_reached", "topics_completed", "user_ended", "max_questions_reached"] | None
    resume_id: str | None

    review_status: ReviewStatus
    review_target: Literal["jd_parsed", "match_result", "interview_report"] | None
    review_feedback: str | None

    current_node: str
    execution_history: Annotated[list[ExecutionEvent], add]
    error_log: Annotated[list[ErrorEntry], add]
    retry_count: dict[str, int]

    conversation_summary: str
    summarized_message_count: int

    final_output: dict | None
