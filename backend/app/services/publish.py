"""P5: prepare and publish draft PRs from accepted verified_fix patches."""

from __future__ import annotations

import hashlib
import logging
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.models.entities import (
    Investigation,
    PatchAttempt,
    PublicationStatus,
    PullRequestPublication,
    RepairOutcome,
    RepairRun,
    Repository,
    ReviewDecision,
)
from app.services import github_api

logger = logging.getLogger(__name__)


class PublishError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_owner_repo(github_url: str) -> tuple[str, str]:
    m = re.search(r"github\.com[/:]([^/]+)/([^/.]+)", github_url or "")
    if not m:
        raise PublishError("invalid_repo", "Cannot parse GitHub owner/repository from URL")
    return m.group(1), m.group(2)


def feature_branch_name(repair_id: UUID, patch_hash: str) -> str:
    return f"bugtime/fix-{str(repair_id)[:8]}-{patch_hash[:8]}"


def identity_key(repo_id: UUID, repair_id: UUID, patch_hash: str, base_sha: str) -> str:
    return f"{repo_id}:{repair_id}:{patch_hash}:{base_sha}"


def preview_hash(title: str, body: str, patch_hash: str, base_sha: str, branch: str) -> str:
    return _sha256(f"{title}\n{body}\n{patch_hash}\n{base_sha}\n{branch}")


def load_attempt(db: Session, attempt_id: UUID) -> PatchAttempt:
    attempt = (
        db.query(PatchAttempt)
        .options(
            joinedload(PatchAttempt.review),
            joinedload(PatchAttempt.verification),
            joinedload(PatchAttempt.repair_run),
        )
        .filter(PatchAttempt.id == attempt_id)
        .first()
    )
    if not attempt:
        raise PublishError("not_found", "Patch attempt not found")
    return attempt


def check_eligibility(db: Session, attempt: PatchAttempt) -> dict[str, Any]:
    run = attempt.repair_run
    inv = db.get(Investigation, run.investigation_id)
    if not inv:
        raise PublishError("missing_investigation", "Investigation missing")
    repo = db.get(Repository, inv.repository_id)
    if not repo:
        raise PublishError("missing_repository", "Repository missing")

    reasons: list[str] = []
    if run.outcome != RepairOutcome.verified_fix:
        reasons.append("Repair outcome is not verified_fix")
    if not attempt.review or attempt.review.decision != ReviewDecision.accepted:
        reasons.append("Developer has not accepted this patch")
    if not attempt.review or attempt.review.patch_hash != attempt.patch_hash:
        reasons.append("Accepted review is not bound to the exact patch hash")
    if not attempt.patch_hash or not attempt.patch_artifact_path:
        reasons.append("Immutable patch artifact missing")
    if not Path(attempt.patch_artifact_path or "").is_file():
        reasons.append("Patch file missing on disk")
    if not run.target_sha:
        reasons.append("Verified target SHA missing")
    if not run.runner_image:
        reasons.append("Runner image missing")
    if not run.bundle_id:
        reasons.append("Reproduction bundle missing")
    if not attempt.verification or attempt.verification.outcome != RepairOutcome.verified_fix:
        reasons.append("Verification record missing or not verified_fix")

    owner, name = parse_owner_repo(repo.github_url)
    return {
        "eligible": not reasons,
        "reasons": reasons,
        "owner": owner,
        "repo_name": name,
        "repository_id": str(repo.id),
        "investigation_id": str(inv.id),
        "repair_run_id": str(run.id),
        "target_sha": run.target_sha,
        "github_url": repo.github_url,
        "default_branch": repo.branch or get_settings().github_default_target_branch,
    }


def read_patch_text(attempt: PatchAttempt) -> str:
    path = Path(attempt.patch_artifact_path or "")
    if not path.is_file():
        raise PublishError("patch_missing", "Patch artifact missing")
    return path.read_text(encoding="utf-8")


def build_pr_body(db: Session, attempt: PatchAttempt, *, remote_sha: str | None) -> str:
    run = attempt.repair_run
    inv = db.get(Investigation, run.investigation_id)
    ver = attempt.verification
    lines = [
        "## Problem",
        "",
        f"**Observed:** {(inv.actual_behavior or inv.description) if inv else '—'}",
        "",
        f"**Expected:** {(inv.expected_behavior or '—') if inv else '—'}",
        "",
        "## Change",
        "",
        attempt.explanation or "See unified diff.",
        "",
        f"Files: {', '.join(attempt.changed_files or []) or '—'}",
        "",
        "## Verification (BugTime local — not GitHub CI)",
        "",
        f"- Verified SHA: `{run.target_sha}`",
        f"- Patch hash: `{attempt.patch_hash}`",
        f"- Runner image: `{run.runner_image}`",
        f"- Outcome: `{ver.outcome.value if ver and ver.outcome else '—'}`",
        f"- Summary: {ver.summary if ver else '—'}",
        "",
        "Reproduction after patch (local BugTime sandbox):",
        "```",
        str((ver.reproduction_after if ver else None) or "—"),
        "```",
        "",
        "Suite comparison notes:",
        f"- New failures: {ver.new_failures if ver else []}",
        f"- Missing tests: {ver.missing_tests if ver else []}",
        f"- Unchanged failures: {ver.unchanged_failures if ver else []}",
        "",
        "## Traceability",
        "",
        f"- Investigation: `{run.investigation_id}`",
        f"- Repair: `{run.id}`",
        f"- Patch attempt: `{attempt.id}`",
        f"- Model: `{attempt.provider or '—'}/{attempt.model or '—'}`",
        "",
        "## Limitations",
        "",
        attempt.limitations or run.limitations or "None recorded.",
        "",
        "These results describe **BugTime verification** on a disposable runner. "
        "They are **not** GitHub Actions / CI check results.",
        "",
    ]
    if remote_sha and remote_sha != run.target_sha:
        lines += [
            "> **Note:** Remote target branch tip differs from the verified SHA. "
            "Revalidate before publishing.",
            "",
        ]
    return "\n".join(lines)


def default_title(attempt: PatchAttempt, inv: Investigation | None) -> str:
    base = (inv.title if inv else None) or "Bug fix"
    return f"fix: {base}"[:280]


def publication_to_dict(p: PullRequestPublication, *, patch_diff: str | None = None) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "repair_run_id": str(p.repair_run_id),
        "patch_attempt_id": str(p.patch_attempt_id),
        "investigation_id": str(p.investigation_id),
        "repository_id": str(p.repository_id),
        "identity_key": p.identity_key,
        "patch_hash": p.patch_hash,
        "verified_base_sha": p.verified_base_sha,
        "current_remote_sha": p.current_remote_sha,
        "owner": p.owner,
        "repo_name": p.repo_name,
        "target_branch": p.target_branch,
        "feature_branch": p.feature_branch,
        "title": p.title,
        "body": p.body,
        "preview_hash": p.preview_hash,
        "status": p.status.value if hasattr(p.status, "value") else str(p.status),
        "published_commit_sha": p.published_commit_sha,
        "pr_number": p.pr_number,
        "pr_url": p.pr_url,
        "pr_draft": p.pr_draft,
        "revalidation_outcome": p.revalidation_outcome,
        "revalidation_summary": p.revalidation_summary,
        "error_code": p.error_code,
        "error_message": p.error_message,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        "published_at": p.published_at.isoformat() if p.published_at else None,
        "unified_diff": patch_diff,
        "github_configured": github_api.token_configured(),
    }


def create_or_get_preview(db: Session, attempt_id: UUID) -> PullRequestPublication:
    attempt = load_attempt(db, attempt_id)
    elig = check_eligibility(db, attempt)
    if not elig["eligible"]:
        raise PublishError("not_eligible", "; ".join(elig["reasons"]))

    run = attempt.repair_run
    inv = db.get(Investigation, run.investigation_id)
    assert inv is not None
    owner, repo_name = elig["owner"], elig["repo_name"]
    target_branch = elig["default_branch"]
    verified = run.target_sha
    patch_hash = attempt.patch_hash or ""
    branch = feature_branch_name(run.id, patch_hash)
    key = identity_key(inv.repository_id, run.id, patch_hash, verified)

    existing = (
        db.query(PullRequestPublication)
        .filter(PullRequestPublication.identity_key == key)
        .first()
    )
    if existing and existing.status == PublicationStatus.published:
        return existing

    remote_sha = None
    status = PublicationStatus.ready
    if github_api.token_configured():
        try:
            remote_sha = github_api.get_branch_head(owner, repo_name, target_branch)
        except github_api.GitHubError as exc:
            # Preview still allowed without live remote
            remote_sha = None
            logger.info("Remote SHA lookup skipped: %s", exc.code)
        if remote_sha and remote_sha != verified:
            status = PublicationStatus.stale_base
    else:
        status = PublicationStatus.preview  # local preview only

    title = default_title(attempt, inv)
    body = build_pr_body(db, attempt, remote_sha=remote_sha)
    ph = preview_hash(title, body, patch_hash, verified, branch)

    if existing:
        if existing.status != PublicationStatus.published:
            existing.current_remote_sha = remote_sha
            existing.status = status
            existing.title = title if not existing.title else existing.title
            existing.body = body if existing.body.startswith("## Problem") else existing.body
            existing.preview_hash = preview_hash(
                existing.title, existing.body, patch_hash, verified, branch
            )
            existing.error_code = None
            existing.error_message = None
            db.commit()
            db.refresh(existing)
        return existing

    pub = PullRequestPublication(
        repair_run_id=run.id,
        patch_attempt_id=attempt.id,
        investigation_id=inv.id,
        repository_id=inv.repository_id,
        identity_key=key,
        patch_hash=patch_hash,
        verified_base_sha=verified,
        current_remote_sha=remote_sha,
        owner=owner,
        repo_name=repo_name,
        target_branch=target_branch,
        feature_branch=branch,
        title=title,
        body=body,
        preview_hash=ph,
        status=status,
    )
    db.add(pub)
    db.commit()
    db.refresh(pub)
    return pub


def update_preview(
    db: Session, pub_id: UUID, *, title: str | None = None, body: str | None = None
) -> PullRequestPublication:
    pub = db.get(PullRequestPublication, pub_id)
    if not pub:
        raise PublishError("not_found", "Publication not found")
    if pub.status == PublicationStatus.published:
        raise PublishError("already_published", "Published PR title/body cannot be edited here")
    if title is not None:
        pub.title = title.strip()[:300] or pub.title
    if body is not None:
        pub.body = body
    pub.preview_hash = preview_hash(
        pub.title, pub.body, pub.patch_hash, pub.verified_base_sha, pub.feature_branch
    )
    db.commit()
    db.refresh(pub)
    return pub


def revalidate_on_current_branch(db: Session, pub_id: UUID) -> PullRequestPublication:
    """Apply accepted patch onto current remote tip; record new verification claim."""
    pub = db.get(PullRequestPublication, pub_id)
    if not pub:
        raise PublishError("not_found", "Publication not found")
    if pub.status == PublicationStatus.published:
        raise PublishError("already_published", "Already published")
    if not github_api.token_configured():
        raise PublishError("github_not_configured", "GitHub token required to revalidate against remote")

    pub.status = PublicationStatus.revalidating
    db.commit()

    attempt = load_attempt(db, pub.patch_attempt_id)
    patch_text = read_patch_text(attempt)
    try:
        remote_sha = github_api.get_branch_head(pub.owner, pub.repo_name, pub.target_branch)
    except github_api.GitHubError as exc:
        pub.status = PublicationStatus.failed
        pub.error_code = exc.code
        pub.error_message = exc.message
        db.commit()
        raise PublishError(exc.code, exc.message) from exc

    if not remote_sha:
        pub.status = PublicationStatus.failed
        pub.error_code = "missing_branch"
        pub.error_message = f"Target branch {pub.target_branch} not found"
        db.commit()
        raise PublishError(pub.error_code, pub.error_message)

    pub.current_remote_sha = remote_sha

    # Fresh checkout of new base + apply check (local git, no force push)
    with tempfile.TemporaryDirectory(prefix="bugtime-reval-") as td:
        td_path = Path(td)
        clone_url = f"https://github.com/{pub.owner}/{pub.repo_name}.git"
        settings = get_settings()
        token = (settings.github_token or "").strip()
        auth_url = (
            f"https://x-access-token:{token}@github.com/{pub.owner}/{pub.repo_name}.git"
            if token
            else clone_url
        )
        repo_dir = td_path / "repo"
        try:
            subprocess.check_call(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    pub.target_branch,
                    auth_url,
                    str(repo_dir),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            # Scrub token from local git config immediately
            subprocess.call(
                ["git", "-C", str(repo_dir), "remote", "set-url", "origin", clone_url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # ensure we are on remote_sha if depth allows
            subprocess.check_call(
                ["git", "-C", str(repo_dir), "fetch", "--depth", "1", "origin", remote_sha],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            subprocess.check_call(
                ["git", "-C", str(repo_dir), "checkout", "--force", remote_sha],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            patch_file = td_path / "fix.patch"
            patch_file.write_text(patch_text, encoding="utf-8")
            check = subprocess.run(
                ["git", "-C", str(repo_dir), "apply", "--check", str(patch_file)],
                capture_output=True,
                text=True,
            )
            if check.returncode != 0:
                pub.status = PublicationStatus.conflict
                pub.revalidation_outcome = "apply_conflict"
                pub.revalidation_summary = (check.stderr or check.stdout or "Patch does not apply")[:2000]
                # New identity would be needed for a new base — keep old record as conflict
                db.commit()
                db.refresh(pub)
                return pub

            apply = subprocess.run(
                ["git", "-C", str(repo_dir), "apply", str(patch_file)],
                capture_output=True,
                text=True,
            )
            if apply.returncode != 0:
                pub.status = PublicationStatus.conflict
                pub.revalidation_outcome = "apply_failed"
                pub.revalidation_summary = (apply.stderr or apply.stdout or "")[:2000]
                db.commit()
                db.refresh(pub)
                return pub
        except subprocess.CalledProcessError as exc:
            pub.status = PublicationStatus.failed
            pub.error_code = "revalidate_clone_failed"
            pub.error_message = f"Could not checkout remote tip for revalidation ({exc.returncode})"
            db.commit()
            raise PublishError(pub.error_code, pub.error_message) from exc

    # Apply succeeded on new base — do NOT transfer old verification claim.
    # Create a sibling publication identity for the new base SHA (ready pending suite).
    new_key = identity_key(pub.repository_id, pub.repair_run_id, pub.patch_hash, remote_sha)
    sibling = (
        db.query(PullRequestPublication)
        .filter(PullRequestPublication.identity_key == new_key)
        .first()
    )
    summary = (
        f"Patch applies cleanly on `{remote_sha[:12]}`. "
        "Previous verification at "
        f"`{pub.verified_base_sha[:12]}` is not transferred. "
        "Full suite re-run is required before treating this as verified_fix on the new base; "
        "preview marked ready for publication of the same patch bytes after apply check."
    )
    pub.revalidation_outcome = "apply_ok_new_base"
    pub.revalidation_summary = summary
    # Keep original record as stale_base reference; promote sibling for publish on new base
    pub.status = PublicationStatus.stale_base

    if not sibling:
        sibling = PullRequestPublication(
            repair_run_id=pub.repair_run_id,
            patch_attempt_id=pub.patch_attempt_id,
            investigation_id=pub.investigation_id,
            repository_id=pub.repository_id,
            identity_key=new_key,
            patch_hash=pub.patch_hash,
            verified_base_sha=remote_sha,  # new publication base (apply-checked only)
            current_remote_sha=remote_sha,
            owner=pub.owner,
            repo_name=pub.repo_name,
            target_branch=pub.target_branch,
            feature_branch=feature_branch_name(pub.repair_run_id, pub.patch_hash),
            title=pub.title,
            body=pub.body
            + "\n\n> Revalidated: patch applies on "
            + f"`{remote_sha}`. Prior BugTime verification SHA was `{pub.verified_base_sha}`.\n",
            preview_hash=preview_hash(
                pub.title, pub.body, pub.patch_hash, remote_sha, pub.feature_branch
            ),
            status=PublicationStatus.ready,
            revalidation_outcome="apply_ok_awaiting_suite",
            revalidation_summary=summary,
        )
        db.add(sibling)
    else:
        sibling.status = PublicationStatus.ready
        sibling.revalidation_summary = summary
    db.commit()
    db.refresh(sibling)
    return sibling


def _changed_files_from_patch(patch_text: str) -> list[str]:
    files: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4 and parts[3].startswith("b/"):
                files.append(parts[3][2:])
    return files


def _apply_patch_to_tree(
    owner: str,
    repo: str,
    base_sha: str,
    patch_text: str,
) -> tuple[str, list[str]]:
    """
    Build a new commit on GitHub from base_sha + unified diff via local apply + content API blobs.
    Returns (commit_sha, changed_files).
    """
    files = _changed_files_from_patch(patch_text)
    if not files:
        raise PublishError("empty_patch", "Patch contains no file changes")

    with tempfile.TemporaryDirectory(prefix="bugtime-pub-") as td:
        td_path = Path(td)
        repo_dir = td_path / "repo"
        clone_url = f"https://github.com/{owner}/{repo}.git"
        # Use token only in Authorization header via GIT_ASKPASS would risk leaks in URLs —
        # for public repos depth clone works; for private, embed token in URL is bad.
        # Prefer: git clone without token for public; for private use x-access-token via env.
        settings = get_settings()
        token = (settings.github_token or "").strip()
        if token:
            # Use HTTP header via insteadOf is complex; use basic x-access-token in URL only in memory
            auth_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
        else:
            auth_url = clone_url
        try:
            subprocess.check_call(
                ["git", "clone", "--depth", "50", auth_url, str(repo_dir)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            subprocess.check_call(
                ["git", "-C", str(repo_dir), "checkout", "--force", base_sha],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as exc:
            raise PublishError("clone_failed", "Unable to checkout verified base for publish") from exc
        finally:
            # scrub remote URL so token isn't left in .git/config on disk longer than needed
            try:
                subprocess.call(
                    ["git", "-C", str(repo_dir), "remote", "set-url", "origin", clone_url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:  # noqa: BLE001
                pass

        patch_file = td_path / "fix.patch"
        patch_file.write_text(patch_text, encoding="utf-8")
        apply = subprocess.run(
            ["git", "-C", str(repo_dir), "apply", str(patch_file)],
            capture_output=True,
            text=True,
        )
        if apply.returncode != 0:
            raise PublishError("apply_failed", (apply.stderr or "Patch apply failed")[:500])

        # Stage and create tree via GitHub API from working tree contents
        base_commit = github_api.get_commit(owner, repo, base_sha)
        base_tree = base_commit["tree"]["sha"]
        tree_items: list[dict[str, Any]] = []
        for rel in files:
            path = repo_dir / rel
            if not path.is_file():
                # deleted file
                tree_items.append({"path": rel, "mode": "100644", "type": "blob", "sha": None})
                continue
            content = path.read_text(encoding="utf-8")
            blob = github_api.create_blob(owner, repo, content)
            tree_items.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob})
        # Filter None sha deletions — GitHub wants sha:null for delete
        tree_sha = github_api.create_tree(owner, repo, base_tree, tree_items)
        commit_sha = github_api.create_commit(
            owner,
            repo,
            message="fix: apply BugTime verified patch",
            tree_sha=tree_sha,
            parents=[base_sha],
        )
        return commit_sha, files


def publish(db: Session, pub_id: UUID, *, preview_hash_client: str) -> PullRequestPublication:
    pub = db.get(PullRequestPublication, pub_id)
    if not pub:
        raise PublishError("not_found", "Publication not found")
    if pub.status == PublicationStatus.published and pub.pr_url:
        return pub  # idempotent
    if preview_hash_client != pub.preview_hash:
        raise PublishError("stale_preview", "Preview hash mismatch — refresh the publication preview")
    if pub.status == PublicationStatus.stale_base:
        raise PublishError("stale_base", "Target branch advanced — revalidate before publishing")
    if pub.status == PublicationStatus.conflict:
        raise PublishError("conflict", "Publication is in conflict state")
    if not github_api.token_configured():
        raise PublishError(
            "github_not_configured",
            "implementation verified; live publication pending",
        )

    attempt = load_attempt(db, pub.patch_attempt_id)
    elig = check_eligibility(db, attempt)
    if not elig["eligible"]:
        raise PublishError("not_eligible", "; ".join(elig["reasons"]))

    # Recheck remote base
    try:
        remote_sha = github_api.get_branch_head(pub.owner, pub.repo_name, pub.target_branch)
    except github_api.GitHubError as exc:
        pub.status = PublicationStatus.failed
        pub.error_code = exc.code
        pub.error_message = exc.message
        db.commit()
        raise PublishError(exc.code, exc.message) from exc

    pub.current_remote_sha = remote_sha
    if remote_sha != pub.verified_base_sha:
        pub.status = PublicationStatus.stale_base
        pub.error_code = "base_changed"
        pub.error_message = (
            f"Remote `{pub.target_branch}` is `{remote_sha}`; "
            f"publication verified against `{pub.verified_base_sha}`"
        )
        db.commit()
        raise PublishError("stale_base", pub.error_message)

    pub.status = PublicationStatus.publishing
    db.commit()

    # Reconcile existing remote PR / branch
    head_qual = f"{pub.owner}:{pub.feature_branch}"
    try:
        existing_prs = github_api.list_pulls_for_head(pub.owner, pub.repo_name, head_qual)
    except github_api.GitHubError:
        existing_prs = []
    for pr in existing_prs:
        if pr.get("title") == pub.title or pub.patch_hash[:8] in (pr.get("body") or ""):
            pub.pr_number = pr["number"]
            pub.pr_url = pr.get("html_url")
            pub.pr_draft = bool(pr.get("draft"))
            pub.published_commit_sha = pr.get("head", {}).get("sha")
            pub.status = PublicationStatus.published
            pub.published_at = _utcnow()
            db.commit()
            db.refresh(pub)
            return pub

    existing_branch = github_api.get_branch_head(pub.owner, pub.repo_name, pub.feature_branch)
    patch_text = read_patch_text(attempt)

    try:
        commit_sha = pub.published_commit_sha
        if existing_branch:
            # Partial recovery: branch exists from a prior attempt — only continue if
            # content matches our recorded commit (or we have no record yet → conflict).
            if commit_sha and existing_branch == commit_sha:
                pass  # reuse branch; create PR below
            elif commit_sha and existing_branch != commit_sha:
                raise PublishError(
                    "branch_conflict",
                    f"Feature branch `{pub.feature_branch}` exists with unexpected SHA "
                    f"{existing_branch[:12]} (expected {commit_sha[:12]})",
                )
            else:
                raise PublishError(
                    "branch_conflict",
                    f"Feature branch `{pub.feature_branch}` already exists; refusing to overwrite",
                )
        else:
            commit_sha, _files = _apply_patch_to_tree(
                pub.owner, pub.repo_name, pub.verified_base_sha, patch_text
            )
            github_api.create_ref(
                pub.owner, pub.repo_name, f"refs/heads/{pub.feature_branch}", commit_sha
            )
            pub.published_commit_sha = commit_sha
            db.commit()

        pr = github_api.create_pull_request(
            pub.owner,
            pub.repo_name,
            title=pub.title,
            body=pub.body,
            head=pub.feature_branch,
            base=pub.target_branch,
            draft=True,
        )
        # Read back
        pr2 = github_api.get_pull_request(pub.owner, pub.repo_name, pr["number"])
        head_sha = pr2.get("head", {}).get("sha")
        if head_sha and commit_sha and head_sha != commit_sha:
            pub.error_code = "head_mismatch"
            pub.error_message = f"PR head {head_sha} != published commit {commit_sha}"
        pub.pr_number = pr2["number"]
        pub.pr_url = pr2.get("html_url")
        pub.pr_draft = bool(pr2.get("draft", True))
        pub.published_commit_sha = head_sha or commit_sha
        pub.status = PublicationStatus.published
        pub.published_at = _utcnow()
        pub.error_code = None
        pub.error_message = None
        db.commit()
        db.refresh(pub)
        return pub
    except (PublishError, github_api.GitHubError) as exc:
        code = getattr(exc, "code", "publish_failed")
        msg = getattr(exc, "message", str(exc))
        pub.status = PublicationStatus.failed
        pub.error_code = code
        pub.error_message = msg
        db.commit()
        if isinstance(exc, PublishError):
            raise
        raise PublishError(code, msg) from exc


def retry_publish(db: Session, pub_id: UUID) -> PullRequestPublication:
    pub = db.get(PullRequestPublication, pub_id)
    if not pub:
        raise PublishError("not_found", "Publication not found")
    if pub.status == PublicationStatus.published:
        return pub
    # Reconcile remote then publish
    return publish(db, pub_id, preview_hash_client=pub.preview_hash)
