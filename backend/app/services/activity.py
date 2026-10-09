import uuid

from sqlalchemy.orm import Session

from app.models.entities import ActivityEvent


def log_event(
    db: Session,
    *,
    stage: str,
    message: str,
    level: str = "info",
    repository_id: uuid.UUID | None = None,
    investigation_id: uuid.UUID | None = None,
) -> ActivityEvent:
    event = ActivityEvent(
        repository_id=repository_id,
        investigation_id=investigation_id,
        stage=stage,
        level=level,
        message=message,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
