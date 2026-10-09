from __future__ import annotations

from pydantic import BaseModel, Field


class CommitSearchCreate(BaseModel):
    good_sha: str = Field(min_length=4, max_length=64)
    test_node_id: str = Field(min_length=3, max_length=500)
    failure_exception_type: str | None = Field(default=None, max_length=200)
    failure_message_fragment: str | None = Field(default=None, max_length=2000)
    confirmed_execution_id: str
    max_commits: int = Field(default=20, ge=2, le=100)
    max_executions: int = Field(default=30, ge=4, le=100)
    max_duration_seconds: int = Field(default=600, ge=60, le=3600)


class CommitMetaOut(BaseModel):
    sha: str
    parent_shas: list[str] = []
    author_name: str | None = None
    author_email: str | None = None
    authored_at: str | None = None
    subject: str | None = None


class CommitSearchStepOut(BaseModel):
    id: str
    sha: str
    phase: str
    classification: str
    classification_reason: str | None = None
    attempt: int
    execution_id: str | None = None
    duration_ms: int | None = None
    created_at: str
    metadata: CommitMetaOut | None = None


class CommitSearchSummary(BaseModel):
    id: str
    investigation_id: str
    status: str
    outcome: str | None
    stage: str
    bad_sha: str
    good_sha: str
    test_node_id: str
    bundle_content_hash: str | None = None
    runner_image_digest: str | None = None
    executions_used: int
    commits_tested: int
    max_commits: int
    max_executions: int
    max_duration_seconds: int
    candidate_shas: list[str] = []
    verified_sha: str | None = None
    parent_sha: str | None = None
    error_message: str | None = None
    assumption_note: str
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None


class CommitSearchDetail(CommitSearchSummary):
    failure_exception_type: str | None = None
    failure_message_fragment: str | None = None
    verified_metadata: CommitMetaOut | None = None
    parent_metadata: CommitMetaOut | None = None
    recent_steps: list[CommitSearchStepOut] = []


class DiffFileOut(BaseModel):
    path: str
    status: str
    patch: str
    in_evidence: bool = False


class CommitDiffOut(BaseModel):
    candidate_sha: str
    parent_sha: str
    files: list[DiffFileOut]


class RepoCommitOut(BaseModel):
    sha: str
    short_sha: str
    subject: str | None
    author_name: str | None
    authored_at: str | None
    parent_shas: list[str] = []
