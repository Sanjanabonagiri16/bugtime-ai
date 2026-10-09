"""Server-side GitHub REST helpers. Never log or return the token."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class GitHubError(Exception):
    def __init__(self, code: str, message: str, *, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class GitHubStatus:
    configured: bool
    connected: bool | None
    login: str | None
    scopes_hint: str | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "connected": self.connected,
            "login": self.login,
            "scopes_hint": self.scopes_hint,
            "error": self.error,
            # Never include token
        }


def _headers() -> dict[str, str]:
    settings = get_settings()
    token = (settings.github_token or "").strip()
    if not token:
        raise GitHubError("github_not_configured", "GitHub token is not configured.")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "BugTimeAI-P5",
    }


def api_base() -> str:
    return (get_settings().github_api_base or "https://api.github.com").rstrip("/")


def token_configured() -> bool:
    return bool((get_settings().github_token or "").strip())


def check_connection() -> GitHubStatus:
    if not token_configured():
        return GitHubStatus(
            configured=False,
            connected=None,
            login=None,
            scopes_hint=None,
            error="GitHub token not configured",
        )
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(f"{api_base()}/user", headers=_headers())
        if resp.status_code == 401:
            return GitHubStatus(True, False, None, None, "Authentication failed")
        if resp.status_code == 403:
            return GitHubStatus(True, False, None, None, "Permission or rate limit denied")
        if resp.status_code >= 400:
            return GitHubStatus(
                True, False, None, None, f"GitHub API error ({resp.status_code})"
            )
        data = resp.json()
        scopes = resp.headers.get("x-oauth-scopes") or resp.headers.get("X-OAuth-Scopes")
        return GitHubStatus(
            configured=True,
            connected=True,
            login=data.get("login"),
            scopes_hint=scopes,
            error=None,
        )
    except httpx.HTTPError:
        logger.exception("GitHub connection check failed")
        return GitHubStatus(True, False, None, None, "Unable to reach GitHub API")


def _request(method: str, path: str, *, json_body: dict | None = None) -> httpx.Response:
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.request(
                method,
                f"{api_base()}{path}",
                headers=_headers(),
                json=json_body,
            )
    except httpx.HTTPError as exc:
        raise GitHubError("github_network", f"GitHub request failed: {exc}") from exc
    if resp.status_code == 401:
        raise GitHubError("github_auth", "GitHub authentication failed", status=401)
    if resp.status_code == 403:
        # rate limit?
        if resp.headers.get("x-ratelimit-remaining") == "0":
            raise GitHubError("github_rate_limit", "GitHub API rate limit exceeded", status=403)
        raise GitHubError("github_forbidden", "GitHub permission denied", status=403)
    if resp.status_code == 404:
        raise GitHubError("github_not_found", "GitHub resource not found", status=404)
    if resp.status_code == 422:
        detail = resp.text[:300]
        raise GitHubError("github_validation", f"GitHub rejected the request: {detail}", status=422)
    if resp.status_code >= 400:
        raise GitHubError(
            "github_error",
            f"GitHub API error ({resp.status_code})",
            status=resp.status_code,
        )
    return resp


def get_ref_sha(owner: str, repo: str, ref: str) -> str:
    # ref like heads/main
    resp = _request("GET", f"/repos/{owner}/{repo}/git/ref/{ref}")
    return resp.json()["object"]["sha"]


def get_branch_head(owner: str, repo: str, branch: str) -> str | None:
    try:
        return get_ref_sha(owner, repo, f"heads/{branch}")
    except GitHubError as exc:
        if exc.status == 404:
            return None
        raise


def get_commit(owner: str, repo: str, sha: str) -> dict[str, Any]:
    return _request("GET", f"/repos/{owner}/{repo}/git/commits/{sha}").json()


def create_blob(owner: str, repo: str, content: str, encoding: str = "utf-8") -> str:
    resp = _request(
        "POST",
        f"/repos/{owner}/{repo}/git/blobs",
        json_body={"content": content, "encoding": encoding},
    )
    return resp.json()["sha"]


def create_tree(owner: str, repo: str, base_tree: str, tree: list[dict[str, Any]]) -> str:
    resp = _request(
        "POST",
        f"/repos/{owner}/{repo}/git/trees",
        json_body={"base_tree": base_tree, "tree": tree},
    )
    return resp.json()["sha"]


def create_commit(
    owner: str,
    repo: str,
    *,
    message: str,
    tree_sha: str,
    parents: list[str],
) -> str:
    resp = _request(
        "POST",
        f"/repos/{owner}/{repo}/git/commits",
        json_body={"message": message, "tree": tree_sha, "parents": parents},
    )
    return resp.json()["sha"]


def create_ref(owner: str, repo: str, ref: str, sha: str) -> None:
    """Non-force create. Fails if ref exists."""
    _request(
        "POST",
        f"/repos/{owner}/{repo}/git/refs",
        json_body={"ref": ref, "sha": sha},
    )


def update_ref(owner: str, repo: str, ref: str, sha: str, *, force: bool = False) -> None:
    if force:
        raise GitHubError("force_forbidden", "Force updates are not allowed for BugTime publications")
    _request(
        "PATCH",
        f"/repos/{owner}/{repo}/git/refs/{ref}",
        json_body={"sha": sha, "force": False},
    )


def create_pull_request(
    owner: str,
    repo: str,
    *,
    title: str,
    body: str,
    head: str,
    base: str,
    draft: bool = True,
) -> dict[str, Any]:
    resp = _request(
        "POST",
        f"/repos/{owner}/{repo}/pulls",
        json_body={
            "title": title,
            "body": body,
            "head": head,
            "base": base,
            "draft": draft,
        },
    )
    return resp.json()


def get_pull_request(owner: str, repo: str, number: int) -> dict[str, Any]:
    return _request("GET", f"/repos/{owner}/{repo}/pulls/{number}").json()


def list_pulls_for_head(owner: str, repo: str, head: str) -> list[dict[str, Any]]:
    # head must be user:branch for search
    resp = _request("GET", f"/repos/{owner}/{repo}/pulls?state=all&head={head}&per_page=20")
    return resp.json()


def get_repo_file(owner: str, repo: str, path: str, ref: str) -> tuple[str, str] | None:
    """Return (content_text, blob_sha) or None if missing."""
    try:
        resp = _request("GET", f"/repos/{owner}/{repo}/contents/{path}?ref={ref}")
    except GitHubError as exc:
        if exc.status == 404:
            return None
        raise
    data = resp.json()
    if data.get("type") != "file":
        return None
    import base64

    content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    return content, data["sha"]
