from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload

from app.api.deps import http_error
from app.api.serializers import repository_detail, repository_summary
from app.core.config import get_settings
from app.db.session import get_db
from app.models.entities import (
    IndexStatus,
    JobKind,
    Repository,
    RepositoryIndex,
    RepositoryStatus,
    SourceFile,
)
from app.schemas.common import PaginatedResponse
from app.schemas.repository import RepositoryCreate, RepositoryDetail, RepositorySummary, SourceFileSummary
from app.services.activity import log_event
from app.services.github_url import parse_github_url
from app.services.jobs import create_and_enqueue

router = APIRouter(tags=["repositories"])


def _get_repo(db: Session, repo_id: uuid.UUID) -> Repository:
    repo = (
        db.query(Repository)
        .options(joinedload(Repository.indexes))
        .filter(Repository.id == repo_id)
        .first()
    )
    if not repo:
        raise http_error(404, "not_found", "Repository not found")
    return repo


@router.get("/repositories", response_model=PaginatedResponse[RepositorySummary])
def list_repositories(
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(Repository).options(joinedload(Repository.indexes))
    if q:
        like = f"%{q}%"
        query = query.filter(
            (Repository.name.ilike(like))
            | (Repository.owner.ilike(like))
            | (Repository.github_url.ilike(like))
        )
    total = query.count()
    items = (
        query.order_by(Repository.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return PaginatedResponse(
        items=[repository_summary(r) for r in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/repositories", status_code=202, response_model=RepositoryDetail)
def create_repository(body: RepositoryCreate, db: Session = Depends(get_db)):
    try:
        parsed = parse_github_url(body.github_url)
    except ValueError as exc:
        raise http_error(
            422,
            "validation_error",
            str(exc),
            field_errors={"github_url": [str(exc)]},
        ) from exc

    existing = (
        db.query(Repository)
        .filter(Repository.owner == parsed.owner, Repository.name == parsed.repo)
        .first()
    )
    if existing:
        raise http_error(409, "conflict", "Repository already imported")

    settings = get_settings()
    display = body.display_name.strip() if body.display_name else parsed.repo
    branch = body.branch.strip() if body.branch else "main"

    repo = Repository(
        name=display if body.display_name else parsed.repo,
        owner=parsed.owner,
        github_url=parsed.html_url,
        branch=branch,
        status=RepositoryStatus.pending,
        is_demo=False,
    )
    db.add(repo)
    db.flush()

    index = RepositoryIndex(
        repository_id=repo.id,
        status=IndexStatus.queued,
        embedding_model=settings.embedding_model,
        embedding_dimension=settings.embedding_dimension,
    )
    db.add(index)
    db.commit()
    db.refresh(repo)

    create_and_enqueue(db, kind=JobKind.import_repo, repository_id=repo.id)
    log_event(
        db,
        stage="queued",
        message="Import queued",
        repository_id=repo.id,
    )

    repo = _get_repo(db, repo.id)
    return repository_detail(repo)


@router.post("/repositories/demo", status_code=202, response_model=RepositoryDetail)
def load_demo_repository(db: Session = Depends(get_db)):
    settings = get_settings()
    # Prefer P2 demo fixture (v2) with linear history; keep older demos untouched.
    existing = (
        db.query(Repository)
        .filter(Repository.is_demo.is_(True), Repository.name == "demo-login-bug-v2")
        .first()
    )
    if existing:
        # Reindex existing demo
        index = RepositoryIndex(
            repository_id=existing.id,
            status=IndexStatus.queued,
            embedding_model=settings.embedding_model,
            embedding_dimension=settings.embedding_dimension,
        )
        db.add(index)
        existing.status = RepositoryStatus.pending
        db.commit()
        create_and_enqueue(db, kind=JobKind.reindex, repository_id=existing.id)
        return repository_detail(_get_repo(db, existing.id))

    repo = Repository(
        name="demo-login-bug-v2",
        owner="bugtime",
        github_url="https://github.com/bugtime/demo-login-bug-v2",
        branch="main",
        status=RepositoryStatus.pending,
        is_demo=True,
    )
    db.add(repo)
    db.flush()
    index = RepositoryIndex(
        repository_id=repo.id,
        status=IndexStatus.queued,
        embedding_model=settings.embedding_model,
        embedding_dimension=settings.embedding_dimension,
    )
    db.add(index)
    db.commit()
    create_and_enqueue(db, kind=JobKind.import_repo, repository_id=repo.id)
    log_event(
        db,
        stage="queued",
        message="Demo repository import queued",
        repository_id=repo.id,
    )
    return repository_detail(_get_repo(db, repo.id))


@router.get("/repositories/{repo_id}", response_model=RepositoryDetail)
def get_repository(repo_id: uuid.UUID, db: Session = Depends(get_db)):
    repo = _get_repo(db, repo_id)
    latest = max(repo.indexes, key=lambda i: i.created_at) if repo.indexes else None
    index_id = latest.id if latest else repo.active_index_id
    skipped = []
    if index_id:
        skipped = (
            db.query(SourceFile)
            .filter(SourceFile.index_id == index_id, SourceFile.indexing_status == "skipped")
            .limit(200)
            .all()
        )
    return repository_detail(repo, skipped)


@router.delete("/repositories/{repo_id}", status_code=202)
def delete_repository(repo_id: uuid.UUID, db: Session = Depends(get_db)):
    repo = _get_repo(db, repo_id)
    create_and_enqueue(db, kind=JobKind.cleanup, repository_id=repo.id)
    return {"status": "cleanup_queued", "id": str(repo.id)}


@router.post("/repositories/{repo_id}/reindex", status_code=202, response_model=RepositoryDetail)
def reindex_repository(repo_id: uuid.UUID, db: Session = Depends(get_db)):
    repo = _get_repo(db, repo_id)
    settings = get_settings()
    index = RepositoryIndex(
        repository_id=repo.id,
        status=IndexStatus.queued,
        embedding_model=settings.embedding_model,
        embedding_dimension=settings.embedding_dimension,
    )
    db.add(index)
    if repo.status != RepositoryStatus.ready:
        repo.status = RepositoryStatus.indexing
    db.commit()
    create_and_enqueue(db, kind=JobKind.reindex, repository_id=repo.id)
    log_event(db, stage="queued", message="Reindex queued", repository_id=repo.id)
    return repository_detail(_get_repo(db, repo.id))


@router.get("/repositories/{repo_id}/files", response_model=PaginatedResponse[SourceFileSummary])
def list_files(
    repo_id: uuid.UUID,
    q: str | None = None,
    index_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    repo = _get_repo(db, repo_id)
    target_index = index_id or repo.active_index_id
    if not target_index and repo.indexes:
        target_index = max(repo.indexes, key=lambda i: i.created_at).id
    if not target_index:
        return PaginatedResponse(items=[], total=0, page=page, page_size=page_size)

    query = db.query(SourceFile).filter(
        SourceFile.index_id == target_index,
        SourceFile.indexing_status == "indexed",
    )
    if q:
        query = query.filter(SourceFile.path.ilike(f"%{q}%"))
    total = query.count()
    files = (
        query.order_by(SourceFile.path.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return PaginatedResponse(
        items=[
            SourceFileSummary(
                id=str(f.id),
                path=f.path,
                language=f.language,
                line_count=f.line_count,
                indexing_status=f.indexing_status,
                skip_reason=f.skip_reason,
                source_type=f.source_type,
            )
            for f in files
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/files/{file_id}")
def get_file(file_id: uuid.UUID, db: Session = Depends(get_db)):
    sf = db.get(SourceFile, file_id)
    if not sf:
        raise http_error(404, "not_found", "File not found")
    return {
        "id": str(sf.id),
        "path": sf.path,
        "language": sf.language,
        "content": sf.content,
        "line_count": sf.line_count,
        "indexing_status": sf.indexing_status,
        "skip_reason": sf.skip_reason,
        "source_type": sf.source_type,
        "content_hash": sf.content_hash,
        "index_id": str(sf.index_id),
    }
