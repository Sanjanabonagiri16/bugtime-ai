from pydantic import BaseModel, Field


class RepositoryCreate(BaseModel):
    github_url: str = Field(..., min_length=1)
    branch: str | None = None
    display_name: str | None = Field(default=None, max_length=100)


class SkippedFileSummary(BaseModel):
    path: str
    reason: str


class SourceFileSummary(BaseModel):
    id: str
    path: str
    language: str | None
    line_count: int
    indexing_status: str
    skip_reason: str | None
    source_type: str


class IndexState(BaseModel):
    id: str
    commit_sha: str | None
    status: str
    embedding_model: str
    file_count: int
    chunk_count: int
    skipped_count: int
    processed_files: int
    total_files_estimate: int | None
    error_message: str | None
    started_at: str | None
    completed_at: str | None


class RepositorySummary(BaseModel):
    id: str
    name: str
    owner: str
    github_url: str
    branch: str
    status: str
    indexed_files: int
    last_indexed_commit: str | None
    last_indexed_at: str | None
    is_demo: bool = False


class RepositoryDetail(RepositorySummary):
    active_index: IndexState | None
    skipped_files: list[SkippedFileSummary] = []
