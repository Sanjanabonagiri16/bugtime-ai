from datetime import datetime

from pydantic import BaseModel, Field


class RepairCreate(BaseModel):
    commit_search_id: str
    max_attempts: int = Field(default=3, ge=1, le=5)
    max_duration_seconds: int = Field(default=600, ge=60, le=1800)


class PatchReviewCreate(BaseModel):
    decision: str  # accepted | rejected
    note: str | None = None
    patch_hash: str


class VerificationOut(BaseModel):
    id: str
    outcome: str | None
    summary: str | None
    reproduction_before: list | None
    reproduction_after: list | None
    suite_before: list | None
    suite_after: list | None
    new_failures: list | None
    missing_tests: list | None
    unchanged_failures: list | None
    baseline_repro_ids: list | None
    patched_repro_ids: list | None
    baseline_suite_id: str | None
    patched_suite_id: str | None


class PatchReviewOut(BaseModel):
    id: str
    patch_hash: str
    decision: str
    note: str | None
    created_at: datetime


class PatchAttemptSummary(BaseModel):
    id: str
    attempt_number: int
    provider: str | None
    model: str | None
    status: str
    patch_hash: str | None
    changed_files: list | None
    validation_ok: bool | None
    validation_error: str | None
    explanation: str | None
    feedback: str | None
    tokens_prompt: int | None
    tokens_completion: int | None
    created_at: datetime
    completed_at: datetime | None
    review: PatchReviewOut | None = None
    verification_outcome: str | None = None


class PatchAttemptDetail(PatchAttemptSummary):
    assumptions: str | None
    limitations: str | None
    source_references: list | None
    unified_diff: str | None
    new_test_path: str | None
    verification: VerificationOut | None
    prompt_version: str


class RepairSummary(BaseModel):
    id: str
    investigation_id: str
    commit_search_id: str
    target_sha: str
    introducing_sha: str | None
    status: str
    outcome: str | None
    stage: str
    attempt_count: int
    max_attempts: int
    max_duration_seconds: int
    provider: str | None
    model: str | None
    bundle_content_hash: str
    runner_image_digest: str | None
    selected_attempt_id: str | None
    error_message: str | None
    limitations: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class RepairDetail(RepairSummary):
    attempts: list[PatchAttemptSummary]
