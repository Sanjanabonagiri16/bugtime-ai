from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class ErrorBody(BaseModel):
    code: str
    message: str
    field_errors: dict[str, list[str]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class ActivityEventOut(BaseModel):
    id: str
    repository_id: str | None
    investigation_id: str | None
    stage: str
    level: str
    message: str
    created_at: str

    model_config = {"from_attributes": True}


class HealthService(BaseModel):
    name: str
    status: str
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str
    services: list[HealthService]
    embedding_model: str
    import_limits: dict[str, int] = Field(default_factory=dict)
