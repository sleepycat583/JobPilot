from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


JobStatus = Literal["queued", "running", "completed", "failed"]
ThreadStatus = Literal["idle", "running", "interrupted", "completed", "failed"]


class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ErrorResponse(BaseModel):
    error: ErrorDetail


class JobAccepted(BaseModel):
    job_id: str
    resource_id: str | None = None
    status: JobStatus = "queued"
    status_url: str


class JobRetryAccepted(JobAccepted):
    """Response for re-queueing a failed local background task."""


class JobRead(BaseModel):
    id: str
    kind: str
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    result: dict[str, Any] | None = None
    error: ErrorDetail | None = None
    created_at: datetime
    updated_at: datetime


class ResumeRead(BaseModel):
    id: str
    version: int
    display_name: str
    file_name: str
    content_type: str
    size_bytes: int
    status: str
    structured: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class JDCreate(BaseModel):
    text: str = Field(min_length=20, max_length=30_000)


class JDRead(BaseModel):
    id: str
    title: str
    source_text: str
    status: str
    parsed: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class MessageItem(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class TaskStep(BaseModel):
    label: str
    status: Literal["pending", "active", "done"]


class TaskView(BaseModel):
    status: ThreadStatus
    title: str
    detail: str
    steps: list[TaskStep] = Field(default_factory=list)


class InterruptPayload(BaseModel):
    id: str
    type: Literal["low_match_score", "interview_answer", "interview_continue", "final_review"]
    title: str
    detail: str
    accepted_actions: list[str]
    data: dict[str, Any] = Field(default_factory=dict)


class ThreadState(BaseModel):
    thread_id: str
    status: ThreadStatus
    messages: list[MessageItem] = Field(default_factory=list)
    task: TaskView
    selected_resume_id: str | None = None
    selected_jd_id: str | None = None
    match_result: dict[str, Any] | None = None
    interview: dict[str, Any] | None = None
    pending_interrupt: InterruptPayload | None = None
    updated_at: datetime


class ThreadCreated(BaseModel):
    thread_id: str
    state_url: str
    events_url: str


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=30_000)
    resume_id: str | None = None
    jd_id: str | None = None


class RunAccepted(BaseModel):
    run_id: str
    thread_id: str
    status: Literal["running", "interrupted"]


class MatchCreate(BaseModel):
    thread_id: str
    resume_id: str | None = None
    jd_id: str | None = None
    strict: bool = False


class InterviewCreate(BaseModel):
    thread_id: str
    resume_id: str | None = None
    jd_id: str | None = None
    interview_type: Literal["综合面试", "技术专项", "项目深挖"] = "综合面试"
    question_count: int = Field(default=5, ge=3, le=5)
    feedback_mode: Literal["each", "final"] = "each"


class ResumeCommand(BaseModel):
    interrupt_id: str
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ExecutionEvent(BaseModel):
    event_id: int
    event: str
    stream_id: str
    timestamp: datetime
    data: dict[str, Any]
