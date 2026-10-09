from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.schemas.common import HealthResponse, HealthService
from app.services.jobs import redis_health
from app.services import qdrant_store

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    services: list[HealthService] = []

    # Database
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        services.append(HealthService(name="postgres", status="ok"))
    except Exception as exc:  # noqa: BLE001
        services.append(HealthService(name="postgres", status="error", detail=str(exc)))

    ok, detail = redis_health()
    services.append(
        HealthService(name="redis", status="ok" if ok else "error", detail=None if ok else detail)
    )

    ok, detail = qdrant_store.healthcheck()
    services.append(
        HealthService(name="qdrant", status="ok" if ok else "error", detail=None if ok else detail)
    )

    overall = "ok" if all(s.status == "ok" for s in services) else "degraded"
    return HealthResponse(
        status=overall,
        services=services,
        embedding_model=settings.embedding_model,
        import_limits={
            "max_index_files": settings.max_index_files,
            "max_file_bytes": settings.max_file_bytes,
            "max_total_index_bytes": settings.max_total_index_bytes,
        },
    )
