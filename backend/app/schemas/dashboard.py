from pydantic import BaseModel

from app.schemas.investigation import InvestigationSummary
from app.schemas.repository import RepositorySummary


class DashboardResponse(BaseModel):
    ready_repositories: int
    active_indexing_jobs: int
    investigations_count: int
    recent_repositories: list[RepositorySummary]
    recent_investigations: list[InvestigationSummary]
