from pydantic import BaseModel, Field


class ExecutionCreate(BaseModel):
    test_identifiers: list[str] = Field(..., min_length=1)


class ConfirmationCreate(BaseModel):
    matching_failed_tests: list[str] = Field(..., min_length=1)
    explanation: str = Field(..., min_length=5, max_length=2000)


class TestResultOut(BaseModel):
    id: str
    test_identifier: str
    outcome: str
    duration_ms: int | None
    failure_message: str | None
    traceback: str | None
    in_evidence: bool


class ConfirmationOut(BaseModel):
    id: str
    matching_failed_tests: list[str]
    explanation: str
    created_at: str


class ExecutionSummary(BaseModel):
    id: str
    investigation_id: str
    pinned_sha: str
    run_type: str
    selected_tests: list[str]
    status: str
    stage: str
    exit_code: int | None
    duration_ms: int | None
    error_category: str | None
    error_message: str | None
    runner_image: str | None
    runner_image_digest: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    error_count: int = 0
    collected: int = 0
    confirmed: bool = False


class ExecutionDetail(ExecutionSummary):
    test_results: list[TestResultOut] = []
    confirmation: ConfirmationOut | None = None


class OutputPage(BaseModel):
    content: str
    offset: int
    next_offset: int
    total_bytes: int
    truncated: bool
    eof: bool
