from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session, joinedload

from app.api.deps import http_error
from app.api.serializers import (
    activity_out,
    evidence_item,
    investigation_detail,
    investigation_summary,
)
from app.db.session import get_db
from app.models.entities import (
    ActivityEvent,
    CodeChunk,
    Evidence,
    IndexStatus,
    Investigation,
    InvestigationStatus,
    JobKind,
    Repository,
    RepositoryIndex,
    RepositoryStatus,
)
from app.schemas.common import PaginatedResponse
from app.schemas.investigation import (
    EvidenceItem,
    InvestigationCreate,
    InvestigationDetail,
    InvestigationSummary,
)
from app.services.activity import log_event
from app.services.jobs import create_and_enqueue

router = APIRouter(tags=["investigations"])


def _get_inv(db: Session, inv_id: uuid.UUID) -> Investigation:
    inv = (
        db.query(Investigation)
        .options(
            joinedload(Investigation.repository).joinedload(Repository.indexes),
            joinedload(Investigation.evidence_items),
        )
        .filter(Investigation.id == inv_id)
        .first()
    )
    if not inv:
        raise http_error(404, "not_found", "Investigation not found")
    return inv


@router.get("/investigations", response_model=PaginatedResponse[InvestigationSummary])
def list_investigations(
    q: str | None = None,
    repository_id: uuid.UUID | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(Investigation).options(joinedload(Investigation.repository))
    if q:
        query = query.filter(Investigation.title.ilike(f"%{q}%"))
    if repository_id:
        query = query.filter(Investigation.repository_id == repository_id)
    if status:
        try:
            st = InvestigationStatus(status)
        except ValueError as exc:
            raise http_error(422, "validation_error", f"Invalid status: {status}") from exc
        query = query.filter(Investigation.status == st)
    total = query.count()
    items = (
        query.order_by(Investigation.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return PaginatedResponse(
        items=[investigation_summary(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/investigations", status_code=202, response_model=InvestigationDetail)
def create_investigation(body: InvestigationCreate, db: Session = Depends(get_db)):
    try:
        repo_id = uuid.UUID(body.repository_id)
    except ValueError as exc:
        raise http_error(
            422,
            "validation_error",
            "Invalid repository_id",
            field_errors={"repository_id": ["Invalid UUID"]},
        ) from exc

    repo = (
        db.query(Repository)
        .options(joinedload(Repository.indexes))
        .filter(Repository.id == repo_id)
        .first()
    )
    if not repo:
        raise http_error(404, "not_found", "Repository not found")
    if repo.status != RepositoryStatus.ready or not repo.active_index_id:
        raise http_error(
            409,
            "index_not_ready",
            "Repository does not have a ready index. Wait for indexing to finish.",
        )

    active = db.get(RepositoryIndex, repo.active_index_id)
    if not active or active.status != IndexStatus.ready:
        raise http_error(409, "index_not_ready", "Active index is not ready")

    inv = Investigation(
        repository_id=repo.id,
        index_id=active.id,
        title=body.title.strip(),
        description=body.description.strip(),
        expected_behavior=body.expected_behavior.strip() if body.expected_behavior else None,
        actual_behavior=body.actual_behavior.strip() if body.actual_behavior else None,
        logs=body.logs.strip() if body.logs else None,
        status=InvestigationStatus.queued,
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)

    create_and_enqueue(db, kind=JobKind.retrieve, investigation_id=inv.id)
    log_event(
        db,
        stage="queued",
        message="Retrieval queued",
        investigation_id=inv.id,
        repository_id=repo.id,
    )
    inv = _get_inv(db, inv.id)
    return investigation_detail(inv, evidence_count=0)


@router.get("/investigations/{inv_id}", response_model=InvestigationDetail)
def get_investigation(inv_id: uuid.UUID, db: Session = Depends(get_db)):
    inv = _get_inv(db, inv_id)
    count = db.query(Evidence).filter(Evidence.investigation_id == inv.id).count()
    return investigation_detail(inv, evidence_count=count)


@router.get("/investigations/{inv_id}/evidence", response_model=list[EvidenceItem])
def get_evidence(inv_id: uuid.UUID, db: Session = Depends(get_db)):
    inv = _get_inv(db, inv_id)
    items = (
        db.query(Evidence)
        .options(joinedload(Evidence.chunk).joinedload(CodeChunk.source_file))
        .filter(Evidence.investigation_id == inv.id)
        .order_by(Evidence.rank.asc())
        .all()
    )
    index = next((i for i in inv.repository.indexes if i.id == inv.index_id), None)
    commit = index.commit_sha if index else None
    return [
        evidence_item(
            ev,
            owner=inv.repository.owner,
            repo=inv.repository.name if not inv.repository.is_demo else inv.repository.name,
            commit=commit,
        )
        for ev in items
    ]


@router.get("/investigations/{inv_id}/events")
def get_events(inv_id: uuid.UUID, db: Session = Depends(get_db)):
    _get_inv(db, inv_id)
    events = (
        db.query(ActivityEvent)
        .filter(ActivityEvent.investigation_id == inv_id)
        .order_by(ActivityEvent.created_at.asc())
        .all()
    )
    return [activity_out(e) for e in events]


@router.post("/investigations/{inv_id}/retry", status_code=202, response_model=InvestigationDetail)
def retry_investigation(inv_id: uuid.UUID, db: Session = Depends(get_db)):
    inv = _get_inv(db, inv_id)
    if inv.status != InvestigationStatus.failed:
        raise http_error(409, "not_failed", "Only failed investigations can be retried")
    inv.status = InvestigationStatus.queued
    inv.error_message = None
    inv.completed_at = None
    db.commit()
    create_and_enqueue(db, kind=JobKind.retrieve, investigation_id=inv.id)
    log_event(
        db,
        stage="queued",
        message="Retry queued",
        investigation_id=inv.id,
        repository_id=inv.repository_id,
    )
    return investigation_detail(_get_inv(db, inv.id), evidence_count=0)


@router.get("/investigations/{inv_id}/report")
def export_report(inv_id: uuid.UUID, db: Session = Depends(get_db)):
    inv = _get_inv(db, inv_id)
    if inv.status != InvestigationStatus.evidence_ready:
        raise http_error(409, "not_ready", "Report is available when evidence is ready")

    evidence = (
        db.query(Evidence)
        .options(joinedload(Evidence.chunk).joinedload(CodeChunk.source_file))
        .filter(Evidence.investigation_id == inv.id)
        .order_by(Evidence.rank.asc())
        .all()
    )
    index = next((i for i in inv.repository.indexes if i.id == inv.index_id), None)
    commit = index.commit_sha if index else "unknown"

    lines = [
        f"# BugTime Investigation Report",
        "",
        f"**Title:** {inv.title}",
        f"**Repository:** {inv.repository.owner}/{inv.repository.name}",
        f"**Pinned commit:** `{commit}`",
        f"**Status:** {inv.status.value}",
        f"**Created:** {inv.created_at.isoformat() if inv.created_at else ''}",
        "",
        "## Bug report",
        "",
        inv.description,
        "",
    ]
    if inv.expected_behavior:
        lines += ["### Expected behavior", "", inv.expected_behavior, ""]
    if inv.actual_behavior:
        lines += ["### Actual behavior", "", inv.actual_behavior, ""]
    if inv.logs:
        lines += ["### Logs", "", "```", inv.logs, "```", ""]

    lines += ["## Evidence", ""]
    if not evidence:
        lines.append("No relevant evidence found.")
    else:
        for ev in evidence:
            chunk = ev.chunk
            sf = chunk.source_file
            lines += [
                f"### {ev.rank}. `{sf.path}` L{chunk.start_line}-L{chunk.end_line}",
                "",
                f"- Source type: {sf.source_type}",
                f"- Match reason: {ev.match_reason.value}",
                f"- Exact match: {ev.exact_match}",
                "",
                "```",
                chunk.content[:2000],
                "```",
                "",
            ]

    lines += [
        "## P0 limitations",
        "",
        "- Retrieved files are **candidates**, not a verified root cause.",
        "- P0 does not reproduce the bug, identify the breaking commit, generate patches, or run tests.",
        "- Similarity scores are ranking signals, not probabilities that a file caused the bug.",
        "",
        "_Generated by BugTime AI P0 — Trace the bug. Find the breaking commit. Verify the fix._",
        "",
    ]
    content = "\n".join(lines)
    return PlainTextResponse(
        content,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="bugtime-{inv.id}.md"'
        },
    )


@router.delete("/investigations/{inv_id}", status_code=204)
def delete_investigation(inv_id: uuid.UUID, db: Session = Depends(get_db)):
    inv = db.get(Investigation, inv_id)
    if not inv:
        raise http_error(404, "not_found", "Investigation not found")
    db.delete(inv)
    db.commit()
    return Response(status_code=204)
