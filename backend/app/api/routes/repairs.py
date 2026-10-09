from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_db, http_error
from app.core.config import get_settings
from app.models.entities import (
    CommitSearch,
    CommitSearchOutcome,
    Investigation,
    JobKind,
    PatchAttempt,
    PatchReview,
    RepairOutcome,
    RepairRun,
    RepairStatus,
    RepositoryIndex,
    ReproductionBundle,
    ReviewDecision,
)
from app.schemas.repair import (
    PatchAttemptDetail,
    PatchAttemptSummary,
    PatchReviewCreate,
    PatchReviewOut,
    RepairCreate,
    RepairDetail,
    RepairSummary,
    VerificationOut,
)
from app.services.activity import log_event
from app.services.jobs import create_and_enqueue
from app.services.model_provider import check_model_available, provider_configured

router = APIRouter(tags=["repairs"])


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _review_out(r: PatchReview | None) -> PatchReviewOut | None:
    if not r:
        return None
    return PatchReviewOut(
        id=str(r.id),
        patch_hash=r.patch_hash,
        decision=r.decision.value if hasattr(r.decision, "value") else str(r.decision),
        note=r.note,
        created_at=r.created_at,
    )


def _attempt_summary(a: PatchAttempt) -> PatchAttemptSummary:
    ver_outcome = None
    if a.verification and a.verification.outcome:
        ver_outcome = (
            a.verification.outcome.value
            if hasattr(a.verification.outcome, "value")
            else str(a.verification.outcome)
        )
    return PatchAttemptSummary(
        id=str(a.id),
        attempt_number=a.attempt_number,
        provider=a.provider,
        model=a.model,
        status=a.status,
        patch_hash=a.patch_hash,
        changed_files=a.changed_files,
        validation_ok=a.validation_ok,
        validation_error=a.validation_error,
        explanation=a.explanation,
        feedback=a.feedback,
        tokens_prompt=a.tokens_prompt,
        tokens_completion=a.tokens_completion,
        created_at=a.created_at,
        completed_at=a.completed_at,
        review=_review_out(a.review),
        verification_outcome=ver_outcome,
    )


def _summary(run: RepairRun) -> RepairSummary:
    return RepairSummary(
        id=str(run.id),
        investigation_id=str(run.investigation_id),
        commit_search_id=str(run.commit_search_id),
        target_sha=run.target_sha,
        introducing_sha=run.introducing_sha,
        status=run.status.value if hasattr(run.status, "value") else str(run.status),
        outcome=run.outcome.value if run.outcome and hasattr(run.outcome, "value") else (run.outcome and str(run.outcome)),
        stage=run.stage,
        attempt_count=run.attempt_count,
        max_attempts=run.max_attempts,
        max_duration_seconds=run.max_duration_seconds,
        provider=run.provider,
        model=run.model,
        bundle_content_hash=run.bundle_content_hash,
        runner_image_digest=run.runner_image_digest,
        selected_attempt_id=str(run.selected_attempt_id) if run.selected_attempt_id else None,
        error_message=run.error_message,
        limitations=run.limitations,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


def _detail(run: RepairRun) -> RepairDetail:
    attempts = [_attempt_summary(a) for a in (run.attempts or [])]
    base = _summary(run)
    return RepairDetail(**base.model_dump(), attempts=attempts)


@router.post("/investigations/{investigation_id}/repairs", status_code=202, response_model=RepairDetail)
def start_repair(investigation_id: uuid.UUID, body: RepairCreate, db: Session = Depends(get_db)):
    if not provider_configured():
        raise http_error(400, "model_not_configured", "Model provider not configured.")
    available, availability_detail = check_model_available()
    if not available:
        raise http_error(400, "model_unavailable", availability_detail)

    inv = db.get(Investigation, investigation_id)
    if not inv:
        raise http_error(404, "not_found", "Investigation not found")

    active = (
        db.query(RepairRun)
        .filter(
            RepairRun.investigation_id == investigation_id,
            RepairRun.status.in_(
                [
                    RepairStatus.queued,
                    RepairStatus.preparing,
                    RepairStatus.baseline,
                    RepairStatus.retrieving,
                    RepairStatus.proposing,
                    RepairStatus.validating,
                    RepairStatus.verifying,
                    RepairStatus.deciding,
                ]
            ),
        )
        .first()
    )
    if active:
        raise http_error(409, "conflict", "A repair is already active for this investigation.")

    try:
        search_id = uuid.UUID(body.commit_search_id)
    except ValueError as exc:
        raise http_error(400, "invalid_id", "Invalid commit_search_id") from exc

    search = (
        db.query(CommitSearch)
        .options(joinedload(CommitSearch.bundle))
        .filter(CommitSearch.id == search_id, CommitSearch.investigation_id == investigation_id)
        .first()
    )
    if not search:
        raise http_error(404, "not_found", "Commit search not found")
    if search.outcome != CommitSearchOutcome.verified_boundary or not search.verified_sha:
        raise http_error(
            400,
            "precondition_failed",
            "Generate fix requires a verified_boundary commit search.",
        )

    bundle = db.get(ReproductionBundle, search.bundle_id)
    if not bundle:
        raise http_error(400, "precondition_failed", "Reproduction bundle missing.")

    settings = get_settings()
    index = db.get(RepositoryIndex, inv.index_id)
    target_sha = (index.commit_sha if index else None) or search.bad_sha
    run_id = uuid.uuid4()
    run = RepairRun(
        id=run_id,
        investigation_id=investigation_id,
        commit_search_id=search.id,
        bundle_id=bundle.id,
        target_sha=target_sha,
        introducing_sha=search.verified_sha,
        bundle_content_hash=bundle.content_hash,
        runner_image=search.runner_image,
        runner_image_digest=search.runner_image_digest,
        graph_thread_id=str(run_id),
        status=RepairStatus.queued,
        stage="queued",
        max_attempts=body.max_attempts or settings.repair_max_attempts,
        max_duration_seconds=body.max_duration_seconds or settings.repair_max_duration_seconds,
        max_tokens=settings.model_max_tokens,
        provider=settings.model_provider,
        model=settings.model_name,
    )
    db.add(run)
    db.commit()

    create_and_enqueue(
        db,
        kind=JobKind.repair,
        repository_id=inv.repository_id,
        investigation_id=inv.id,
        repair_run_id=run.id,
    )
    log_event(
        db,
        stage="repair",
        message=f"Repair queued (target {run.target_sha[:12]}, introducing { (run.introducing_sha or '')[:12]})",
        investigation_id=inv.id,
        repository_id=inv.repository_id,
    )
    run = (
        db.query(RepairRun)
        .options(joinedload(RepairRun.attempts))
        .filter(RepairRun.id == run.id)
        .first()
    )
    return _detail(run)


@router.get("/investigations/{investigation_id}/repairs", response_model=list[RepairSummary])
def list_repairs(investigation_id: uuid.UUID, db: Session = Depends(get_db)):
    rows = (
        db.query(RepairRun)
        .filter(RepairRun.investigation_id == investigation_id)
        .order_by(RepairRun.created_at.desc())
        .all()
    )
    return [_summary(r) for r in rows]


@router.get("/repairs/{repair_id}", response_model=RepairDetail)
def get_repair(repair_id: uuid.UUID, db: Session = Depends(get_db)):
    run = (
        db.query(RepairRun)
        .options(
            joinedload(RepairRun.attempts).joinedload(PatchAttempt.review),
            joinedload(RepairRun.attempts).joinedload(PatchAttempt.verification),
        )
        .filter(RepairRun.id == repair_id)
        .first()
    )
    if not run:
        raise http_error(404, "not_found", "Repair not found")
    return _detail(run)


@router.post("/repairs/{repair_id}/cancel", status_code=202, response_model=RepairDetail)
def cancel_repair(repair_id: uuid.UUID, db: Session = Depends(get_db)):
    run = (
        db.query(RepairRun)
        .options(joinedload(RepairRun.attempts))
        .filter(RepairRun.id == repair_id)
        .first()
    )
    if not run:
        raise http_error(404, "not_found", "Repair not found")
    run.cancel_requested = True
    if run.status in {
        RepairStatus.queued,
        RepairStatus.preparing,
        RepairStatus.baseline,
        RepairStatus.retrieving,
        RepairStatus.proposing,
        RepairStatus.validating,
        RepairStatus.verifying,
        RepairStatus.deciding,
    }:
        # Soft-cancel; worker observes between stages
        pass
    db.commit()
    log_event(
        db,
        stage="repair",
        message="Repair cancellation requested",
        investigation_id=run.investigation_id,
    )
    return _detail(run)


@router.get("/patch-attempts/{attempt_id}", response_model=PatchAttemptDetail)
def get_patch_attempt(attempt_id: uuid.UUID, db: Session = Depends(get_db)):
    attempt = (
        db.query(PatchAttempt)
        .options(joinedload(PatchAttempt.verification), joinedload(PatchAttempt.review))
        .filter(PatchAttempt.id == attempt_id)
        .first()
    )
    if not attempt:
        raise http_error(404, "not_found", "Patch attempt not found")

    diff = None
    if attempt.patch_artifact_path and Path(attempt.patch_artifact_path).is_file():
        diff = Path(attempt.patch_artifact_path).read_text(encoding="utf-8", errors="replace")

    ver = None
    if attempt.verification:
        v = attempt.verification
        ver = VerificationOut(
            id=str(v.id),
            outcome=v.outcome.value if v.outcome and hasattr(v.outcome, "value") else (str(v.outcome) if v.outcome else None),
            summary=v.summary,
            reproduction_before=v.reproduction_before,
            reproduction_after=v.reproduction_after,
            suite_before=v.suite_before,
            suite_after=v.suite_after,
            new_failures=v.new_failures,
            missing_tests=v.missing_tests,
            unchanged_failures=v.unchanged_failures,
            baseline_repro_ids=v.baseline_repro_ids,
            patched_repro_ids=v.patched_repro_ids,
            baseline_suite_id=v.baseline_suite_id,
            patched_suite_id=v.patched_suite_id,
        )

    base = _attempt_summary(attempt)
    return PatchAttemptDetail(
        **base.model_dump(),
        assumptions=attempt.assumptions,
        limitations=attempt.limitations,
        source_references=attempt.source_references,
        unified_diff=diff,
        new_test_path=attempt.new_test_path,
        verification=ver,
        prompt_version=attempt.prompt_version,
    )


@router.post("/patch-attempts/{attempt_id}/review", response_model=PatchReviewOut)
def review_patch(attempt_id: uuid.UUID, body: PatchReviewCreate, db: Session = Depends(get_db)):
    attempt = (
        db.query(PatchAttempt)
        .options(joinedload(PatchAttempt.review))
        .filter(PatchAttempt.id == attempt_id)
        .first()
    )
    if not attempt:
        raise http_error(404, "not_found", "Patch attempt not found")
    if not attempt.patch_hash:
        raise http_error(400, "no_patch", "Attempt has no immutable patch hash.")
    if body.patch_hash != attempt.patch_hash:
        raise http_error(400, "hash_mismatch", "Review patch_hash does not match the recorded patch.")
    if body.decision not in {"accepted", "rejected"}:
        raise http_error(400, "invalid_decision", "decision must be accepted or rejected")

    decision = ReviewDecision.accepted if body.decision == "accepted" else ReviewDecision.rejected
    if attempt.review:
        attempt.review.decision = decision
        attempt.review.note = body.note
        attempt.review.patch_hash = body.patch_hash
        db.commit()
        db.refresh(attempt.review)
        return _review_out(attempt.review)

    review = PatchReview(
        attempt_id=attempt.id,
        patch_hash=body.patch_hash,
        decision=decision,
        note=body.note,
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return _review_out(review)


@router.get("/patch-attempts/{attempt_id}/patch")
def download_patch(attempt_id: uuid.UUID, db: Session = Depends(get_db)):
    attempt = db.get(PatchAttempt, attempt_id)
    if not attempt or not attempt.patch_artifact_path:
        raise http_error(404, "not_found", "Patch not found")
    path = Path(attempt.patch_artifact_path)
    if not path.is_file():
        raise http_error(404, "not_found", "Patch artifact missing")
    text = path.read_text(encoding="utf-8", errors="replace")
    verified = (
        attempt.verification
        and attempt.verification.outcome == RepairOutcome.verified_fix
    )
    label = "verified" if verified else "UNVERIFIED"
    header = f"# BugTime AI patch ({label})\n# hash: {attempt.patch_hash}\n# attempt: {attempt.attempt_number}\n"
    return Response(
        content=header + text,
        media_type="text/plain",
        headers={
            "Content-Disposition": f'attachment; filename="bugtime-{attempt.patch_hash[:12]}.patch"'
        },
    )


@router.get("/repairs/{repair_id}/report")
def repair_report(repair_id: uuid.UUID, db: Session = Depends(get_db)):
    run = (
        db.query(RepairRun)
        .options(
            joinedload(RepairRun.attempts).joinedload(PatchAttempt.verification),
            joinedload(RepairRun.attempts).joinedload(PatchAttempt.review),
        )
        .filter(RepairRun.id == repair_id)
        .first()
    )
    if not run:
        raise http_error(404, "not_found", "Repair not found")

    lines = [
        "# BugTime AI — Repair report",
        "",
        f"- Repair ID: `{run.id}`",
        f"- Target SHA: `{run.target_sha}`",
        f"- Introducing SHA: `{run.introducing_sha}`",
        f"- Status: {run.status.value if hasattr(run.status, 'value') else run.status}",
        f"- Outcome: {run.outcome.value if run.outcome and hasattr(run.outcome, 'value') else run.outcome}",
        f"- Provider/model: {run.provider}/{run.model}",
        f"- Bundle hash: `{run.bundle_content_hash}`",
        f"- Runner digest: `{run.runner_image_digest}`",
        f"- Attempts: {run.attempt_count}/{run.max_attempts}",
        f"- Limitations: {run.limitations or 'n/a'}",
        "",
    ]
    for a in run.attempts or []:
        lines.extend(
            [
                f"## Attempt {a.attempt_number}",
                f"- Status: {a.status}",
                f"- Patch hash: `{a.patch_hash}`",
                f"- Files: {', '.join(a.changed_files or [])}",
                f"- Validation: {a.validation_ok} {a.validation_error or ''}",
                f"- Explanation: {a.explanation or ''}",
                f"- Feedback: {a.feedback or ''}",
            ]
        )
        if a.verification:
            lines.append(f"- Verification: {a.verification.outcome} — {a.verification.summary}")
        if a.review:
            lines.append(f"- Review: {a.review.decision.value} ({a.review.patch_hash[:12]})")
        lines.append("")
    return PlainTextResponse("\n".join(lines), media_type="text/markdown")
