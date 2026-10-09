from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db

__all__ = ["get_db", "http_error"]


def http_error(
    status_code: int,
    code: str,
    message: str,
    field_errors: dict[str, list[str]] | None = None,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message, "field_errors": field_errors}},
    )
