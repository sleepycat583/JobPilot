from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.errors import api_error
from app.api.serializers import serialize_job
from app.db import get_db
from app.models import JobRecord
from app.schemas.contracts import JobRead


router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: str, session: Session = Depends(get_db)) -> JobRead:
    record = session.get(JobRecord, job_id)
    if record is None:
        raise api_error(404, "JOB_NOT_FOUND", "任务不存在。")
    return serialize_job(record)
