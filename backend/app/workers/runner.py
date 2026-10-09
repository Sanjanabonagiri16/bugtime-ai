"""RQ worker entrypoint with interrupted-job recovery."""

from __future__ import annotations

import logging
import os
import sys

from redis import Redis
from rq import Worker

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.entities import Job, JobKind, JobStatus
from app.services import qdrant_store
from app.services.jobs import create_and_enqueue, get_queue, queue_for_kind

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bugtime.worker")


def recover_interrupted_jobs(queue_names: set[str]) -> None:
    db = SessionLocal()
    try:
        running = db.query(Job).filter(Job.status == JobStatus.running).all()
        for job in running:
            qname = queue_for_kind(job.kind)
            if qname not in queue_names:
                continue
            job.status = JobStatus.interrupted
            db.commit()
            logger.info("Re-queueing interrupted job %s (%s)", job.id, job.kind)
            create_and_enqueue(
                db,
                kind=job.kind if isinstance(job.kind, JobKind) else JobKind(job.kind),
                repository_id=job.repository_id,
                investigation_id=job.investigation_id,
                execution_id=job.execution_id,
                commit_search_id=job.commit_search_id,
                repair_run_id=job.repair_run_id,
            )
        queued = db.query(Job).filter(Job.status == JobStatus.queued).all()
        for job in queued:
            qname = queue_for_kind(job.kind)
            if qname not in queue_names:
                continue
            try:
                get_queue(qname).enqueue(
                    "app.workers.tasks.process_job",
                    str(job.id),
                    job_timeout=60 * 60,
                )
            except Exception:  # noqa: BLE001
                logger.exception("Failed to re-enqueue queued job %s", job.id)
    finally:
        db.close()


def main() -> None:
    settings = get_settings()
    mode = os.environ.get("WORKER_MODE", "index")
    if mode == "exec":
        queues = ["bugtime-exec"]
    elif mode == "all":
        queues = ["bugtime", "bugtime-exec"]
    else:
        queues = ["bugtime"]
        try:
            qdrant_store.ensure_collection()
        except Exception:  # noqa: BLE001
            logger.warning("Qdrant unavailable at worker start")

    recover_interrupted_jobs(set(queues))
    redis = Redis.from_url(settings.redis_url)
    worker = Worker(queues, connection=redis)
    logger.info("Starting RQ worker on queues %s", queues)
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
