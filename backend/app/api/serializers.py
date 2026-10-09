from __future__ import annotations

from app.models.entities import (
    ActivityEvent,
    Evidence,
    IndexStatus,
    Investigation,
    Repository,
    RepositoryIndex,
    SourceFile,
)
from app.schemas.investigation import EvidenceItem, InvestigationDetail, InvestigationSummary
from app.schemas.repository import (
    IndexState,
    RepositoryDetail,
    RepositorySummary,
    SkippedFileSummary,
)


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def index_state(index: RepositoryIndex | None) -> IndexState | None:
    if not index:
        return None
    return IndexState(
        id=str(index.id),
        commit_sha=index.commit_sha,
        status=index.status.value if hasattr(index.status, "value") else str(index.status),
        embedding_model=index.embedding_model,
        file_count=index.file_count,
        chunk_count=index.chunk_count,
        skipped_count=index.skipped_count,
        processed_files=index.processed_files,
        total_files_estimate=index.total_files_estimate,
        error_message=index.error_message,
        started_at=_iso(index.started_at),
        completed_at=_iso(index.completed_at),
    )


def repository_summary(repo: Repository, active: RepositoryIndex | None = None) -> RepositorySummary:
    active = active or next((i for i in repo.indexes if i.id == repo.active_index_id), None)
    if active is None and repo.indexes:
        active = max(repo.indexes, key=lambda i: i.created_at)
    return RepositorySummary(
        id=str(repo.id),
        name=repo.name,
        owner=repo.owner,
        github_url=repo.github_url,
        branch=repo.branch,
        status=repo.status.value if hasattr(repo.status, "value") else str(repo.status),
        indexed_files=active.file_count if active and active.status == IndexStatus.ready else 0,
        last_indexed_commit=active.commit_sha if active else None,
        last_indexed_at=_iso(active.completed_at) if active else None,
        is_demo=repo.is_demo,
    )


def repository_detail(repo: Repository, skipped: list[SourceFile] | None = None) -> RepositoryDetail:
    active = next((i for i in repo.indexes if i.id == repo.active_index_id), None)
    # Prefer latest in-progress index for status display
    latest = max(repo.indexes, key=lambda i: i.created_at) if repo.indexes else None
    display = latest if latest and latest.status != IndexStatus.ready else active or latest
    base = repository_summary(repo, active or display)
    return RepositoryDetail(
        **base.model_dump(),
        active_index=index_state(display),
        skipped_files=[
            SkippedFileSummary(path=f.path, reason=f.skip_reason or "skipped")
            for f in (skipped or [])
            if f.indexing_status == "skipped"
        ],
    )


def investigation_summary(inv: Investigation) -> InvestigationSummary:
    return InvestigationSummary(
        id=str(inv.id),
        title=inv.title,
        repository_id=str(inv.repository_id),
        repository_name=f"{inv.repository.owner}/{inv.repository.name}" if inv.repository else "",
        status=inv.status.value if hasattr(inv.status, "value") else str(inv.status),
        created_at=_iso(inv.created_at) or "",
        completed_at=_iso(inv.completed_at),
    )


def investigation_detail(inv: Investigation, evidence_count: int) -> InvestigationDetail:
    repo = inv.repository
    index = next((i for i in repo.indexes if i.id == inv.index_id), None) if repo else None
    return InvestigationDetail(
        id=str(inv.id),
        title=inv.title,
        repository_id=str(inv.repository_id),
        repository_name=repo.name if repo else "",
        repository_owner=repo.owner if repo else "",
        github_url=repo.github_url if repo else "",
        branch=repo.branch if repo else "",
        index_id=str(inv.index_id),
        commit_sha=index.commit_sha if index else None,
        status=inv.status.value if hasattr(inv.status, "value") else str(inv.status),
        description=inv.description,
        expected_behavior=inv.expected_behavior,
        actual_behavior=inv.actual_behavior,
        logs=inv.logs,
        error_message=inv.error_message,
        created_at=_iso(inv.created_at) or "",
        completed_at=_iso(inv.completed_at),
        evidence_count=evidence_count,
        index_version=str(inv.index_id)[:8],
        reranker_mode=getattr(inv, "reranker_mode", None) or "disabled",
        reranker_model_path=getattr(inv, "reranker_model_path", None),
        reranker_checkpoint_hash=getattr(inv, "reranker_checkpoint_hash", None),
        reranker_fallback=bool(getattr(inv, "reranker_fallback", False)),
    )


def evidence_item(ev: Evidence, *, owner: str, repo: str, commit: str | None) -> EvidenceItem:
    chunk = ev.chunk
    sf = chunk.source_file
    sha = commit or "HEAD"
    github = f"https://github.com/{owner}/{repo}/blob/{sha}/{sf.path}#L{chunk.start_line}-L{chunk.end_line}"
    snippet = chunk.content
    if len(snippet) > 600:
        snippet = snippet[:600] + "\n…"
    return EvidenceItem(
        id=str(ev.id),
        rank=ev.rank,
        file_path=sf.path,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
        source_type=sf.source_type,
        snippet=snippet,
        match_reason=ev.match_reason.value if hasattr(ev.match_reason, "value") else str(ev.match_reason),
        semantic_score=ev.semantic_score,
        exact_match=ev.exact_match,
        symbol_name=chunk.symbol_name,
        chunk_id=str(chunk.id),
        source_file_id=str(sf.id),
        github_url=github,
    )


def activity_out(event: ActivityEvent) -> dict:
    return {
        "id": str(event.id),
        "repository_id": str(event.repository_id) if event.repository_id else None,
        "investigation_id": str(event.investigation_id) if event.investigation_id else None,
        "stage": event.stage,
        "level": event.level,
        "message": event.message,
        "created_at": _iso(event.created_at),
    }
