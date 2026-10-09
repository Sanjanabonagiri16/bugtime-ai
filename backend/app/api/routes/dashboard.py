from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload

from app.api.serializers import investigation_summary, repository_summary
from app.db.session import get_db
from app.models.entities import IndexStatus, Investigation, Repository, RepositoryIndex, RepositoryStatus
from app.schemas.dashboard import DashboardResponse

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardResponse)
def get_dashboard(db: Session = Depends(get_db)) -> DashboardResponse:
    ready = (
        db.query(Repository)
        .filter(Repository.status == RepositoryStatus.ready)
        .count()
    )
    active_indexing = (
        db.query(RepositoryIndex)
        .filter(
            RepositoryIndex.status.in_(
                [
                    IndexStatus.queued,
                    IndexStatus.fetching,
                    IndexStatus.scanning,
                    IndexStatus.chunking,
                    IndexStatus.embedding,
                    IndexStatus.saving,
                ]
            )
        )
        .count()
    )
    investigations_count = db.query(Investigation).count()

    recent_repos = (
        db.query(Repository)
        .options(joinedload(Repository.indexes))
        .order_by(Repository.updated_at.desc())
        .limit(5)
        .all()
    )
    recent_inv = (
        db.query(Investigation)
        .options(joinedload(Investigation.repository))
        .order_by(Investigation.created_at.desc())
        .limit(5)
        .all()
    )

    return DashboardResponse(
        ready_repositories=ready,
        active_indexing_jobs=active_indexing,
        investigations_count=investigations_count,
        recent_repositories=[repository_summary(r) for r in recent_repos],
        recent_investigations=[investigation_summary(i) for i in recent_inv],
    )
