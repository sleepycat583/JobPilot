from collections.abc import Sequence
from typing import Annotated, Any, Literal

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import NotRequired, TypedDict


WorkerName = Literal[
    "resume_worker",
    "jd_worker",
    "match_worker",
    "interview_worker",
    "chat_worker",
]

WorkerAction = Literal[
    "respond",
    "create_jd",
    "run_match",
    "start_interview",
    "submit_interview_answer",
    "continue_interview",
    "end_interview",
]


class RouteAudit(TypedDict):
    worker: WorkerName
    confidence: float
    reason: str


class ReadonlyContext(TypedDict, total=False):
    latest_user_message: str
    conversation_tail: list[dict[str, str]]
    selected_resume: dict[str, Any] | None
    selected_jd: dict[str, Any] | None
    active_interview: dict[str, Any] | None
    pending_interrupt: dict[str, Any] | None


class CareerGraphState(TypedDict, total=False):
    # Shared conversation channel required by the official supervisor graph.
    messages: Annotated[Sequence[BaseMessage], add_messages]
    remaining_steps: NotRequired[int]

    # API-owned input. Graph nodes must treat this field as immutable context.
    readonly_context: ReadonlyContext
    run_id: str

    # Supervisor-owned audit data. It is checkpointed but never sent through SSE.
    route_audit: RouteAudit | None

    # Worker-owned workspace. Workers may write only these three fields and messages.
    worker_name: WorkerName | None
    worker_result: dict[str, Any] | None
    visible_output: str | None

    # Output Sanitizer-owned field. This is the only graph text consumed by the API.
    public_output: str | None


WORKER_WRITABLE_FIELDS = frozenset({"messages", "worker_name", "worker_result", "visible_output"})
SUPERVISOR_WRITABLE_FIELDS = frozenset({"messages", "route_audit"})
OUTPUT_WRITABLE_FIELDS = frozenset({"public_output"})
