from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from git import Repo
from git.exc import GitCommandError
from sqlalchemy.orm import Session, joinedload

from app.api.deps import http_error
from app.core.config import get_settings
from app.db.session import get_db
from app.models.entities import (
    CommitMetadata,
    CommitSearch,
    CommitSearchStatus,
    CommitSearchStep,
    Investigation,
    InvestigationStatus,
    JobKind,
    ReproductionConfirmation,
    Repository,
    RepositoryIndex,
)
from app.schemas.commit_search import (
    CommitDiffOut,
    CommitMetaOut,
    CommitSearchCreate,
    CommitSearchDetail,
    CommitSearchStepOut,
    CommitSearchSummary,
    DiffFileOut,
    RepoCommitOut,
)
from app.schemas.common import PaginatedResponse
from app.services.activity import log_event
from app.services.commit_search import (
    ancestry_has_merges,
    build_reproduction_bundle,
    ensure_commit_metadata,
    evidence_paths_for_investigation,
    is_ancestor,
    resolve_sha,
)
from app.services.jobs import create_and_enqueue
router = APIRouter(tags=["commit-searches"])


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _meta_out(m: CommitMetadata | None) -> CommitMetaOut | None:
    if not m:
        return None
    return CommitMetaOut(
        sha=m.sha,
        parent_shas=list(m.parent_shas or []),
        author_name=m.author_name,
        author_email=m.author_email,
        authored_at=_iso(m.authored_at),
        subject=m.subject,
    )


def _step_out(step: CommitSearchStep, meta: CommitMetadata | None = None) -> CommitSearchStepOut:
    return CommitSearchStepOut(
        id=str(step.id),
        sha=step.sha,
        phase=step.phase.value,
        classification=step.classification.value,
        classification_reason=step.classification_reason,
        attempt=step.attempt,
        execution_id=str(step.execution_id) if step.execution_id else None,
        duration_ms=step.duration_ms,
        created_at=_iso(step.created_at) or "",
        metadata=_meta_out(meta),
    )


def _summary(search: CommitSearch) -> CommitSearchSummary:
    return CommitSearchSummary(
        id=str(search.id),
        investigation_id=str(search.investigation_id),
        status=search.status.value,
        outcome=search.outcome.value if search.outcome else None,
        stage=search.stage,
        bad_sha=search.bad_sha,
        good_sha=search.good_sha,
        test_node_id=search.test_node_id,
        bundle_content_hash=search.bundle.content_hash if search.bundle else None,
        runner_image_digest=search.runner_image_digest,
        executions_used=search.executions_used,
        commits_tested=search.commits_tested,
        max_commits=search.max_commits,
        max_executions=search.max_executions,
        max_duration_seconds=search.max_duration_seconds,
        candidate_shas=list(search.candidate_shas or []),
        verified_sha=search.verified_sha,
        parent_sha=search.parent_sha,
        error_message=search.error_message,
        assumption_note=search.assumption_note,
        created_at=_iso(search.created_at) or "",
        started_at=_iso(search.started_at),
        completed_at=_iso(search.completed_at),
    )


def _repo_git(db: Session, repo: Repository) -> Repo:
    if repo.local_path and Path(repo.local_path).exists():
        return Repo(repo.local_path)
    settings = get_settings()
    if repo.is_demo:
        return Repo(Path(settings.demo_repo_path).resolve())
    raise http_error(400, "repo_unavailable", "Repository checkout is not available yet")


@router.get("/repositories/{repo_id}/commits", response_model=PaginatedResponse[RepoCommitOut])
def list_repository_commits(
    repo_id: uuid.UUID,
    anchor: str = Query(..., min_length=4, description="SHA to anchor history (newest)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    repo = db.get(Repository, repo_id)
    if not repo:
        raise http_error(404, "not_found", "Repository not found")
    try:
        git_repo = _repo_git(db, repo)
        tip = resolve_sha(git_repo, anchor)
    except Exception as exc:  # noqa: BLE001
        raise http_error(400, "invalid_sha", f"Unable to resolve anchor SHA: {exc}") from exc

    commits = list(git_repo.iter_commits(tip, max_count=page * page_size))
    total = len(list(git_repo.iter_commits(tip, max_count=500)))
    page_items = commits[(page - 1) * page_size : page * page_size]
    items: list[RepoCommitOut] = []
    for c in page_items:
        ensure_commit_metadata(db, repo.id, git_repo, c.hexsha)
        items.append(
            RepoCommitOut(
                sha=c.hexsha,
                short_sha=c.hexsha[:7],
                subject=c.summary,
                author_name=c.author.name if c.author else None,
                authored_at=datetime.fromtimestamp(c.authored_date, tz=timezone.utc).isoformat(),
                parent_shas=[p.hexsha for p in c.parents],
            )
        )
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.post(
    "/investigations/{investigation_id}/commit-searches",
    status_code=202,
    response_model=CommitSearchDetail,
)
def create_commit_search(
    investigation_id: uuid.UUID,
    body: CommitSearchCreate,
    db: Session = Depends(get_db),
):
    inv = (
        db.query(Investigation)
        .options(joinedload(Investigation.repository))
        .filter(Investigation.id == investigation_id)
        .first()
    )
    if not inv:
        raise http_error(404, "not_found", "Investigation not found")
    if inv.status != InvestigationStatus.evidence_ready:
        raise http_error(400, "not_ready", "Investigation must be evidence_ready")

    active = (
        db.query(CommitSearch)
        .filter(
            CommitSearch.investigation_id == investigation_id,
            CommitSearch.status.in_(
                [
                    CommitSearchStatus.queued,
                    CommitSearchStatus.validating,
                    CommitSearchStatus.searching,
                    CommitSearchStatus.verifying,
                ]
            ),
        )
        .first()
    )
    if active:
        raise http_error(
            409,
            "conflict",
            "A commit search is already running for this investigation",
        )

    try:
        confirmed_id = uuid.UUID(body.confirmed_execution_id)
    except ValueError as exc:
        raise http_error(422, "validation_error", "Invalid confirmed_execution_id") from exc

    confirmation = (
        db.query(ReproductionConfirmation)
        .filter(ReproductionConfirmation.execution_id == confirmed_id)
        .first()
    )
    if not confirmation:
        raise http_error(400, "not_confirmed", "Confirmed reproduction is required")

    if body.test_node_id not in (confirmation.matching_failed_tests or []):
        raise http_error(
            422,
            "validation_error",
            "Reproduction test must be one of the developer-confirmed failing tests",
            field_errors={"test_node_id": ["Must match a confirmed failing test"]},
        )

    index = db.get(RepositoryIndex, inv.index_id)
    bad_sha = index.commit_sha if index else None
    if not bad_sha:
        raise http_error(400, "missing_pin", "Investigation has no pinned commit")

    try:
        git_repo = _repo_git(db, inv.repository)
        good_sha = resolve_sha(git_repo, body.good_sha.strip())
        bad_full = resolve_sha(git_repo, bad_sha)
    except Exception as exc:  # noqa: BLE001
        raise http_error(400, "invalid_sha", f"Unable to resolve commit SHAs: {exc}") from exc

    if not is_ancestor(git_repo, good_sha, bad_full):
        raise http_error(
            400,
            "invalid_ancestry",
            "Candidate good commit must be an ancestor of the pinned bad commit",
        )

    if ancestry_has_merges(git_repo, good_sha, bad_full):
        # Persist a completed unsupported search for UI clarity
        raise http_error(
            400,
            "unsupported_history",
            "Merge history is not supported in this milestone.",
        )

    # Infer matcher defaults from confirmation if omitted
    exc_type = body.failure_exception_type
    frag = body.failure_message_fragment
    if not frag or not exc_type:
        from app.models.entities import ExecutionRun, TestResult, TestOutcome

        run = (
            db.query(ExecutionRun)
            .options(joinedload(ExecutionRun.test_results))
            .filter(ExecutionRun.id == confirmed_id)
            .first()
        )
        if run:
            failed = [
                t
                for t in (run.test_results or [])
                if t.test_identifier == body.test_node_id
                and t.outcome in {TestOutcome.failed, TestOutcome.error}
            ]
            if failed:
                if not frag and failed[0].failure_message:
                    frag = failed[0].failure_message[:200]
                if not exc_type and failed[0].traceback:
                    # Heuristic: first "ErrorType:" line
                    for line in (failed[0].traceback or "").splitlines():
                        if "Error" in line and ":" in line:
                            exc_type = line.split(":", 1)[0].strip().split()[-1]
                            break
                if not exc_type:
                    exc_type = "AssertionError"

    try:
        bundle = build_reproduction_bundle(
            db,
            inv,
            source_sha=bad_full,
            test_node_id=body.test_node_id,
            failure_exception_type=exc_type or "AssertionError",
            failure_message_fragment=frag,
        )
    except Exception as exc:  # noqa: BLE001
        raise http_error(400, "bundle_failed", f"Unable to build reproduction bundle: {exc}") from exc

    search = CommitSearch(
        investigation_id=inv.id,
        confirmed_execution_id=confirmed_id,
        bundle_id=bundle.id,
        bad_sha=bad_full,
        good_sha=good_sha,
        test_node_id=body.test_node_id,
        status=CommitSearchStatus.queued,
        stage="queued",
        max_commits=body.max_commits,
        max_executions=body.max_executions,
        max_duration_seconds=body.max_duration_seconds,
    )
    db.add(search)
    db.commit()
    db.refresh(search)

    create_and_enqueue(
        db,
        kind=JobKind.commit_search,
        repository_id=inv.repository_id,
        investigation_id=inv.id,
        commit_search_id=search.id,
    )
    log_event(
        db,
        stage="commit_search",
        message=(
            f"Commit search queued (good={good_sha[:12]}, bad={bad_full[:12]}). "
            "Assumes the bug was introduced once in this range."
        ),
        investigation_id=inv.id,
        repository_id=inv.repository_id,
    )

    search = (
        db.query(CommitSearch)
        .options(joinedload(CommitSearch.bundle), joinedload(CommitSearch.steps))
        .filter(CommitSearch.id == search.id)
        .first()
    )
    return _detail(db, search)


def _detail(db: Session, search: CommitSearch) -> CommitSearchDetail:
    base = _summary(search)
    verified = None
    parent = None
    if search.verified_sha:
        verified = (
            db.query(CommitMetadata)
            .filter(
                CommitMetadata.repository_id
                == db.get(Investigation, search.investigation_id).repository_id,
                CommitMetadata.sha == search.verified_sha,
            )
            .first()
        )
    if search.parent_sha:
        parent = (
            db.query(CommitMetadata)
            .filter(
                CommitMetadata.repository_id
                == db.get(Investigation, search.investigation_id).repository_id,
                CommitMetadata.sha == search.parent_sha,
            )
            .first()
        )
    steps = (
        db.query(CommitSearchStep)
        .filter(CommitSearchStep.search_id == search.id)
        .order_by(CommitSearchStep.created_at.asc())
        .limit(50)
        .all()
    )
    return CommitSearchDetail(
        **base.model_dump(),
        failure_exception_type=search.bundle.failure_exception_type if search.bundle else None,
        failure_message_fragment=search.bundle.failure_message_fragment if search.bundle else None,
        verified_metadata=_meta_out(verified),
        parent_metadata=_meta_out(parent),
        recent_steps=[_step_out(s) for s in steps],
    )


@router.get(
    "/investigations/{investigation_id}/commit-searches",
    response_model=list[CommitSearchSummary],
)
def list_commit_searches(investigation_id: uuid.UUID, db: Session = Depends(get_db)):
    rows = (
        db.query(CommitSearch)
        .options(joinedload(CommitSearch.bundle))
        .filter(CommitSearch.investigation_id == investigation_id)
        .order_by(CommitSearch.created_at.desc())
        .all()
    )
    return [_summary(r) for r in rows]


@router.get("/commit-searches/{search_id}", response_model=CommitSearchDetail)
def get_commit_search(search_id: uuid.UUID, db: Session = Depends(get_db)):
    search = (
        db.query(CommitSearch)
        .options(joinedload(CommitSearch.bundle), joinedload(CommitSearch.steps))
        .filter(CommitSearch.id == search_id)
        .first()
    )
    if not search:
        raise http_error(404, "not_found", "Commit search not found")
    return _detail(db, search)


@router.get("/commit-searches/{search_id}/steps", response_model=list[CommitSearchStepOut])
def list_commit_search_steps(search_id: uuid.UUID, db: Session = Depends(get_db)):
    search = db.get(CommitSearch, search_id)
    if not search:
        raise http_error(404, "not_found", "Commit search not found")
    inv = db.get(Investigation, search.investigation_id)
    steps = (
        db.query(CommitSearchStep)
        .filter(CommitSearchStep.search_id == search_id)
        .order_by(CommitSearchStep.created_at.asc())
        .all()
    )
    out: list[CommitSearchStepOut] = []
    for s in steps:
        meta = (
            db.query(CommitMetadata)
            .filter(
                CommitMetadata.repository_id == inv.repository_id,
                CommitMetadata.sha == s.sha,
            )
            .first()
        )
        out.append(_step_out(s, meta))
    return out


@router.get("/commit-searches/{search_id}/diff", response_model=CommitDiffOut)
def get_commit_search_diff(search_id: uuid.UUID, db: Session = Depends(get_db)):
    search = db.get(CommitSearch, search_id)
    if not search:
        raise http_error(404, "not_found", "Commit search not found")
    if not search.verified_sha or not search.parent_sha:
        raise http_error(400, "no_diff", "No verified candidate/parent available yet")

    inv = (
        db.query(Investigation)
        .options(joinedload(Investigation.repository))
        .filter(Investigation.id == search.investigation_id)
        .first()
    )
    git_repo = _repo_git(db, inv.repository)
    evidence = evidence_paths_for_investigation(db, inv.id)

    # Unified diffs per file
    try:
        diff_index = git_repo.commit(search.parent_sha).diff(
            search.verified_sha, create_patch=True
        )
    except Exception as exc:  # noqa: BLE001
        raise http_error(500, "diff_failed", str(exc)) from exc

    files: list[DiffFileOut] = []
    for d in diff_index:
        path = d.b_path or d.a_path or "unknown"
        status = d.change_type
        try:
            patch = d.diff.decode("utf-8", errors="replace") if d.diff else ""
        except Exception:  # noqa: BLE001
            patch = ""
        files.append(
            DiffFileOut(
                path=path,
                status=status,
                patch=patch,
                in_evidence=path in evidence or any(path.endswith(e) or e.endswith(path) for e in evidence),
            )
        )
    # Evidence hits first
    files.sort(key=lambda f: (not f.in_evidence, f.path))
    return CommitDiffOut(
        candidate_sha=search.verified_sha,
        parent_sha=search.parent_sha,
        files=files,
    )


@router.post("/commit-searches/{search_id}/cancel", response_model=CommitSearchDetail)
def cancel_commit_search(search_id: uuid.UUID, db: Session = Depends(get_db)):
    search = (
        db.query(CommitSearch)
        .options(joinedload(CommitSearch.bundle))
        .filter(CommitSearch.id == search_id)
        .first()
    )
    if not search:
        raise http_error(404, "not_found", "Commit search not found")
    if search.status in {
        CommitSearchStatus.completed,
        CommitSearchStatus.failed,
        CommitSearchStatus.cancelled,
        CommitSearchStatus.timed_out,
    }:
        return _detail(db, search)
    search.cancel_requested = True
    db.commit()
    log_event(
        db,
        stage="commit_search",
        message="Commit search cancellation requested",
        investigation_id=search.investigation_id,
    )
    return _detail(db, search)


@router.get("/commit-searches/{search_id}/report")
def commit_search_report(search_id: uuid.UUID, db: Session = Depends(get_db)):
    search = (
        db.query(CommitSearch)
        .options(joinedload(CommitSearch.bundle), joinedload(CommitSearch.steps))
        .filter(CommitSearch.id == search_id)
        .first()
    )
    if not search:
        raise http_error(404, "not_found", "Commit search not found")
    inv = db.get(Investigation, search.investigation_id)
    lines = [
        f"# BugTime commit search report",
        "",
        f"- Investigation: {inv.title if inv else search.investigation_id}",
        f"- Search ID: {search.id}",
        f"- Status: {search.status.value}",
        f"- Outcome: {search.outcome.value if search.outcome else '—'}",
        f"- Good SHA: `{search.good_sha}`",
        f"- Bad SHA: `{search.bad_sha}`",
        f"- Test: `{search.test_node_id}`",
        f"- Bundle hash: `{search.bundle.content_hash if search.bundle else '—'}`",
        f"- Runner digest: `{search.runner_image_digest or '—'}`",
        f"- Executions used: {search.executions_used}",
        "",
        "## Assumption",
        "",
        search.assumption_note,
        "",
        "## Result",
        "",
    ]
    if search.verified_sha:
        lines += [
            f"- Candidate: `{search.verified_sha}`",
            f"- Parent: `{search.parent_sha}`",
            "",
            "Verified failure boundary: the reproduction passes at the parent and fails at this commit "
            "under the recorded environment.",
            "",
        ]
    elif search.candidate_shas:
        lines.append("Candidates:")
        for sha in search.candidate_shas:
            lines.append(f"- `{sha}`")
        lines.append("")
    if search.error_message:
        lines += ["## Notes", "", search.error_message, ""]

    lines += ["## Steps", ""]
    for s in search.steps:
        lines.append(
            f"- `{s.sha[:12]}` · {s.phase.value} · {s.classification.value}"
            f" · attempt {s.attempt}"
            + (f" · {s.classification_reason}" if s.classification_reason else "")
        )

    return PlainTextResponse("\n".join(lines), media_type="text/markdown")
