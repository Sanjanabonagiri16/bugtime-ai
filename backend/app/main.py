from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import (
    commit_searches,
    dashboard,
    evaluations,
    executions,
    health,
    investigations,
    publications,
    repairs,
    repositories,
)
from app.core.config import get_settings
from app.services import qdrant_store

settings = get_settings()

app = FastAPI(
    title="BugTime AI",
    description="Trace the bug. Find the breaking commit. Verify the fix.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_: Request, exc: StarletteHTTPException):
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": "http_error", "message": str(exc.detail), "field_errors": None}},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    field_errors: dict[str, list[str]] = {}
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", []) if p != "body")
        field_errors.setdefault(loc or "body", []).append(err.get("msg", "Invalid value"))
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request validation failed",
                "field_errors": field_errors,
            }
        },
    )


api = FastAPI()  # unused placeholder kept for clarity
app.include_router(health.router, prefix="/api/v1")
app.include_router(dashboard.router, prefix="/api/v1")
app.include_router(repositories.router, prefix="/api/v1")
app.include_router(investigations.router, prefix="/api/v1")
app.include_router(executions.router, prefix="/api/v1")
app.include_router(commit_searches.router, prefix="/api/v1")
app.include_router(repairs.router, prefix="/api/v1")
app.include_router(evaluations.router, prefix="/api/v1")
app.include_router(publications.router, prefix="/api/v1")


@app.on_event("startup")
def on_startup() -> None:
    try:
        qdrant_store.ensure_collection()
    except Exception:  # noqa: BLE001
        # Health endpoint will report Qdrant issues; do not block API boot.
        pass
