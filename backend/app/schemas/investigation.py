from pydantic import BaseModel, Field


class InvestigationCreate(BaseModel):
    repository_id: str
    title: str = Field(..., min_length=5, max_length=120)
    description: str = Field(..., min_length=20, max_length=10000)
    expected_behavior: str | None = Field(default=None, max_length=5000)
    actual_behavior: str | None = Field(default=None, max_length=5000)
    logs: str | None = Field(default=None, max_length=20000)


class InvestigationSummary(BaseModel):
    id: str
    title: str
    repository_id: str
    repository_name: str
    status: str
    created_at: str
    completed_at: str | None = None


class EvidenceItem(BaseModel):
    id: str
    rank: int
    file_path: str
    start_line: int
    end_line: int
    source_type: str
    snippet: str
    match_reason: str
    semantic_score: float | None
    exact_match: bool
    symbol_name: str | None
    chunk_id: str
    source_file_id: str
    github_url: str


class InvestigationDetail(BaseModel):
    id: str
    title: str
    repository_id: str
    repository_name: str
    repository_owner: str
    github_url: str
    branch: str
    index_id: str
    commit_sha: str | None
    status: str
    description: str
    expected_behavior: str | None
    actual_behavior: str | None
    logs: str | None
    error_message: str | None
    created_at: str
    completed_at: str | None
    evidence_count: int
    index_version: str
    reranker_mode: str = "disabled"
    reranker_model_path: str | None = None
    reranker_checkpoint_hash: str | None = None
    reranker_fallback: bool = False
