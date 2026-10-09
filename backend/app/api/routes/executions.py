from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload

from app.api.deps import http_error
from app.db.session import get_db
from app.models.entities import (
    ExecutionRun,
    ExecutionStatus,
    Investigation,
    InvestigationStatus,
    JobKind,
    ReproductionConfirmation,
    RepositoryIndex,
    RunType,
    TestOutcome,
    TestResult,
)
from app.schemas.execution import (
    ConfirmationCreate,
    ConfirmationOut,
    ExecutionCreate,
    ExecutionDetail,
    ExecutionSummary,
    OutputPage,
    TestResultOut,
)
from app.services.activity import log_event
from app.services.jobs import create_and_enqueue

router = APIRouter(tags=["executions"])


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _counts(results: list[TestResult]) -> dict[str, int]:
    out = {"passed": 0, "failed": 0, "skipped": 0, "error_count": 0, "collected": 0}
    for r in results:
        if r.outcome == TestOutcome.passed:
            out["passed"] += 1
        elif r.outcome == TestOutcome.failed:
            out["failed"] += 1
        elif r.outcome == TestOutcome.skipped:
            out["skipped"] += 1
        elif r.outcome == TestOutcome.error:
            out["error_count"] += 1
        elif r.outcome == TestOutcome.collected:
            out["collected"] += 1
    return out


def _summary(run: ExecutionRun) -> ExecutionSummary:
    counts = _counts(run.test_results or [])
    return ExecutionSummary(
        id=str(run.id),
        investigation_id=str(run.investigation_id),
        pinned_sha=run.pinned_sha,
        run_type=run.run_type.value,
        selected_tests=list(run.selected_tests or []),
        status=run.status.value,
        stage=run.stage,
        exit_code=run.exit_code,
        duration_ms=run.duration_ms,
        error_category=run.error_category,
        error_message=run.error_message,
        runner_image=run.runner_image,
        runner_image_digest=run.runner_image_digest,
        created_at=_iso(run.created_at) or "",
        started_at=_iso(run.started_at),
        completed_at=_iso(run.completed_at),
        confirmed=run.confirmation is not None,
        **counts,
    )


def _detail(run: ExecutionRun) -> ExecutionDetail:
    base = _summary(run)
    confirmation = None
    if run.confirmation:
        confirmation = ConfirmationOut(
            id=str(run.confirmation.id),
            matching_failed_tests=list(run.confirmation.matching_failed_tests or []),
            explanation=run.confirmation.explanation,
            created_at=_iso(run.confirmation.created_at) or "",
        )
    return ExecutionDetail(
        **base.model_dump(),
        test_results=[
            TestResultOut(
                id=str(r.id),
                test_identifier=r.test_identifier,
                outcome=r.outcome.value,
                duration_ms=r.duration_ms,
                failure_message=r.failure_message,
                traceback=r.traceback,
                in_evidence=r.in_evidence,
            )
            for r in sorted(
                run.test_results or [],
                key=lambda x: (0 if x.in_evidence else 1, x.test_identifier),
            )
        ],
        confirmation=confirmation,
    )


def _get_inv(db: Session, inv_id: uuid.UUID) -> Investigation:
    inv = (
        db.query(Investigation)
        .options(joinedload(Investigation.repository))
        .filter(Investigation.id == inv_id)
        .first()
    )
    if not inv:
        raise http_error(404, "not_found", "Investigation not found")
    return inv


def _pinned_sha(db: Session, inv: Investigation) -> str:
    index = db.get(RepositoryIndex, inv.index_id)
    if not index or not index.commit_sha:
        raise http_error(409, "no_commit", "Investigation has no pinned commit SHA")
    return index.commit_sha


def _get_run(db: Session, run_id: uuid.UUID) -> ExecutionRun:
    run = (
        db.query(ExecutionRun)
        .options(
            joinedload(ExecutionRun.test_results),
            joinedload(ExecutionRun.confirmation),
            joinedload(ExecutionRun.artifacts),
        )
        .filter(ExecutionRun.id == run_id)
        .first()
    )
    if not run:
        raise http_error(404, "not_found", "Execution not found")
    return run


@router.post(
    "/investigations/{inv_id}/test-discovery",
    status_code=202,
    response_model=ExecutionDetail,
)
def start_test_discovery(inv_id: uuid.UUID, db: Session = Depends(get_db)):
    inv = _get_inv(db, inv_id)
    if inv.status not in {
        InvestigationStatus.evidence_ready,
        InvestigationStatus.failed,
        InvestigationStatus.retrieving,
        InvestigationStatus.queued,
    }:
        pass
    sha = _pinned_sha(db, inv)
    run = ExecutionRun(
        investigation_id=inv.id,
        pinned_sha=sha,
        run_type=RunType.collection,
        selected_tests=[],
        status=ExecutionStatus.queued,
        stage="queued",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    create_and_enqueue(
        db,
        kind=JobKind.test_discovery,
        investigation_id=inv.id,
        repository_id=inv.repository_id,
        execution_id=run.id,
    )
    log_event(
        db,
        stage="queued",
        message="Test discovery queued",
        investigation_id=inv.id,
        repository_id=inv.repository_id,
    )
    return _detail(_get_run(db, run.id))


@router.post(
    "/investigations/{inv_id}/executions",
    status_code=202,
    response_model=ExecutionDetail,
)
def start_execution(
    inv_id: uuid.UUID,
    body: ExecutionCreate,
    db: Session = Depends(get_db),
):
    inv = _get_inv(db, inv_id)
    sha = _pinned_sha(db, inv)

    # Validate against latest successful collection for this pin
    collection = (
        db.query(ExecutionRun)
        .options(joinedload(ExecutionRun.test_results))
        .filter(
            ExecutionRun.investigation_id == inv.id,
            ExecutionRun.run_type == RunType.collection,
            ExecutionRun.pinned_sha == sha,
            ExecutionRun.status == ExecutionStatus.completed,
        )
        .order_by(ExecutionRun.created_at.desc())
        .first()
    )
    if not collection:
        raise http_error(
            409,
            "discovery_required",
            "Run test discovery for this pinned commit before executing tests.",
        )
    known = {
        r.test_identifier
        for r in collection.test_results
        if r.outcome == TestOutcome.collected
    }
    unknown = [t for t in body.test_identifiers if t not in known]
    if unknown:
        raise http_error(
            422,
            "invalid_tests",
            "One or more test identifiers were not collected for this commit.",
            field_errors={"test_identifiers": unknown},
        )

    run = ExecutionRun(
        investigation_id=inv.id,
        pinned_sha=sha,
        run_type=RunType.reproduction,
        selected_tests=body.test_identifiers,
        status=ExecutionStatus.queued,
        stage="queued",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    create_and_enqueue(
        db,
        kind=JobKind.reproduction,
        investigation_id=inv.id,
        repository_id=inv.repository_id,
        execution_id=run.id,
    )
    log_event(
        db,
        stage="queued",
        message=f"Reproduction queued ({len(body.test_identifiers)} tests)",
        investigation_id=inv.id,
        repository_id=inv.repository_id,
    )
    return _detail(_get_run(db, run.id))


@router.get(
    "/investigations/{inv_id}/executions",
    response_model=list[ExecutionSummary],
)
def list_executions(inv_id: uuid.UUID, db: Session = Depends(get_db)):
    _get_inv(db, inv_id)
    runs = (
        db.query(ExecutionRun)
        .options(
            joinedload(ExecutionRun.test_results),
            joinedload(ExecutionRun.confirmation),
        )
        .filter(ExecutionRun.investigation_id == inv_id)
        .order_by(ExecutionRun.created_at.desc())
        .all()
    )
    return [_summary(r) for r in runs]


@router.get("/executions/{run_id}", response_model=ExecutionDetail)
def get_execution(run_id: uuid.UUID, db: Session = Depends(get_db)):
    return _detail(_get_run(db, run_id))


@router.get("/executions/{run_id}/output", response_model=OutputPage)
def get_output(
    run_id: uuid.UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(16000, ge=1, le=100_000),
    db: Session = Depends(get_db),
):
    run = _get_run(db, run_id)
    artifact = next((a for a in run.artifacts if a.artifact_type == "output"), None)
    if not artifact:
        return OutputPage(
            content="",
            offset=offset,
            next_offset=offset,
            total_bytes=0,
            truncated=False,
            eof=True,
        )
    path = Path(artifact.storage_path)
    if not path.exists():
        return OutputPage(
            content="",
            offset=offset,
            next_offset=offset,
            total_bytes=0,
            truncated=artifact.truncated,
            eof=True,
        )
    data = path.read_bytes()
    chunk = data[offset : offset + limit]
    next_off = offset + len(chunk)
    return OutputPage(
        content=chunk.decode("utf-8", errors="replace"),
        offset=offset,
        next_offset=next_off,
        total_bytes=len(data),
        truncated=artifact.truncated,
        eof=next_off >= len(data),
    )


@router.post("/executions/{run_id}/cancel", status_code=202, response_model=ExecutionDetail)
def cancel_execution(run_id: uuid.UUID, db: Session = Depends(get_db)):
    run = _get_run(db, run_id)
    if run.status not in {
        ExecutionStatus.queued,
        ExecutionStatus.preparing,
        ExecutionStatus.running,
    }:
        raise http_error(409, "not_active", "Execution is not active")
    run.cancel_requested = True
    db.commit()
    return _detail(_get_run(db, run_id))


@router.post(
    "/executions/{run_id}/confirmation",
    status_code=201,
    response_model=ExecutionDetail,
)
def confirm_reproduction(
    run_id: uuid.UUID,
    body: ConfirmationCreate,
    db: Session = Depends(get_db),
):
    run = _get_run(db, run_id)
    if run.run_type != RunType.reproduction:
        raise http_error(409, "not_reproduction", "Only reproduction runs can be confirmed")
    if run.status != ExecutionStatus.completed:
        raise http_error(409, "not_completed", "Confirm only completed runs")
    failed = {
        r.test_identifier
        for r in run.test_results
        if r.outcome in {TestOutcome.failed, TestOutcome.error}
    }
    if not failed:
        raise http_error(409, "no_failures", "No failed tests to confirm")
    unknown = [t for t in body.matching_failed_tests if t not in failed]
    if unknown:
        raise http_error(
            422,
            "invalid_tests",
            "Confirmation must reference failed tests from this run",
            field_errors={"matching_failed_tests": unknown},
        )
    if run.confirmation:
        run.confirmation.matching_failed_tests = body.matching_failed_tests
        run.confirmation.explanation = body.explanation.strip()
    else:
        db.add(
            ReproductionConfirmation(
                execution_id=run.id,
                matching_failed_tests=body.matching_failed_tests,
                explanation=body.explanation.strip(),
            )
        )
    db.commit()
    log_event(
        db,
        stage="confirmation",
        message="Developer confirmed bug reproduction",
        investigation_id=run.investigation_id,
    )
    return _detail(_get_run(db, run_id))
