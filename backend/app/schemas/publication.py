from pydantic import BaseModel, Field


class PublicationOut(BaseModel):
    id: str
    repair_run_id: str
    patch_attempt_id: str
    investigation_id: str
    repository_id: str
    identity_key: str
    patch_hash: str
    verified_base_sha: str
    current_remote_sha: str | None = None
    owner: str
    repo_name: str
    target_branch: str
    feature_branch: str
    title: str
    body: str
    preview_hash: str
    status: str
    published_commit_sha: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    pr_draft: bool | None = None
    revalidation_outcome: str | None = None
    revalidation_summary: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    published_at: str | None = None
    unified_diff: str | None = None
    github_configured: bool = False


class PublicationUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    body: str | None = None


class PublishRequest(BaseModel):
    preview_hash: str


class GitHubStatusOut(BaseModel):
    configured: bool
    connected: bool | None = None
    login: str | None = None
    scopes_hint: str | None = None
    error: str | None = None
