from __future__ import annotations

import logging
import uuid

from app.db.session import SessionLocal
from app.models.entities import Job, JobKind, JobStatus
from app.services.indexing import run_cleanup_job, run_indexing_job
from app.services.retrieval import run_retrieval_job
from app.services.commit_search import run_commit_search_job
from app.services.repair import run_repair_job
from app.services.sandbox import run_execution_job

logger = logging.getLogger(__name__)


def process_job(job_id: str) -> None:
    db = SessionLocal()
    try:
        job = db.get(Job, uuid.UUID(job_id))
        if not job:
            logger.error("Job %s not found", job_id)
            return
        if job.status == JobStatus.completed:
            return
        if job.kind in {JobKind.import_repo, JobKind.reindex}:
            run_indexing_job(db, job.id)
        elif job.kind == JobKind.cleanup:
            run_cleanup_job(db, job.id)
        elif job.kind == JobKind.retrieve:
            run_retrieval_job(db, job.id)
        elif job.kind in {JobKind.test_discovery, JobKind.reproduction}:
            run_execution_job(db, job.id)
        elif job.kind == JobKind.commit_search:
            run_commit_search_job(db, job.id)
        elif job.kind == JobKind.repair:
            run_repair_job(db, job.id)
        else:
            job.status = JobStatus.failed
            job.error_message = f"Unknown job kind: {job.kind}"
            db.commit()
    finally:
        db.close()
