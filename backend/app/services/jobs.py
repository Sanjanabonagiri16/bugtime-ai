from __future__ import annotations

import logging
import uuid

from redis import Redis
from rq import Queue
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import Job, JobKind, JobStatus

logger = logging.getLogger(__name__)

INDEX_KINDS = {
    JobKind.import_repo,
    JobKind.reindex,
    JobKind.cleanup,
    JobKind.retrieve,
}
EXEC_KINDS = {
    JobKind.test_discovery,
    JobKind.reproduction,
    JobKind.commit_search,
    JobKind.repair,
}


def get_queue(name: str = "bugtime") -> Queue:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url)
    return Queue(name, connection=redis)


def queue_for_kind(kind: JobKind) -> str:
    return "bugtime-exec" if kind in EXEC_KINDS else "bugtime"


def enqueue_job(db: Session, job: Job) -> Job:
    db.add(job)
    db.commit()
    db.refresh(job)
    try:
        queue = get_queue(queue_for_kind(job.kind))
        queue.enqueue(
            "app.workers.tasks.process_job",
            str(job.id),
            job_timeout=60 * 60,
            result_ttl=3600,
            failure_ttl=86400,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to enqueue job %s", job.id)
        job.status = JobStatus.failed
        job.error_message = f"Queue unavailable: {exc}"
        db.commit()
    return job


def create_and_enqueue(
    db: Session,
    *,
    kind: JobKind,
    repository_id: uuid.UUID | None = None,
    investigation_id: uuid.UUID | None = None,
    execution_id: uuid.UUID | None = None,
    commit_search_id: uuid.UUID | None = None,
    repair_run_id: uuid.UUID | None = None,
) -> Job:
    job = Job(
        kind=kind,
        repository_id=repository_id,
        investigation_id=investigation_id,
        execution_id=execution_id,
        commit_search_id=commit_search_id,
        repair_run_id=repair_run_id,
        status=JobStatus.queued,
    )
    return enqueue_job(db, job)


def redis_health() -> tuple[bool, str]:
    try:
        settings = get_settings()
        redis = Redis.from_url(settings.redis_url)
        redis.ping()
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
