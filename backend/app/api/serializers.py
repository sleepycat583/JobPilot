from app.models import JDRecord, JobRecord, ResumeRecord
from app.schemas.contracts import ErrorDetail, JDRead, JobRead, ResumeRead
from app.services.store import loads


RETRYABLE_JOB_ERRORS = {
    "RESUME_MODEL_FAILED",
    "RESUME_INDEX_FAILED",
    "JD_MODEL_FAILED",
}


def serialize_job(record: JobRecord) -> JobRead:
    error = None
    if record.error_code:
        error = ErrorDetail(
            code=record.error_code,
            message=record.error_message or "任务执行失败",
            retryable=record.error_code in RETRYABLE_JOB_ERRORS,
        )
    return JobRead(
        id=record.id,
        kind=record.kind,
        status=record.status,
        progress=record.progress,
        result=loads(record.result_json),
        error=error,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def serialize_resume(record: ResumeRecord) -> ResumeRead:
    return ResumeRead(
        id=record.id,
        version=record.version,
        display_name=record.display_name,
        file_name=record.file_name,
        content_type=record.content_type,
        size_bytes=record.size_bytes,
        status=record.status,
        structured=loads(record.structured_json),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def serialize_jd(record: JDRecord) -> JDRead:
    return JDRead(
        id=record.id,
        title=record.title,
        source_text=record.source_text,
        status=record.status,
        parsed=loads(record.parsed_json),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
