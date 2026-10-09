"""P5 mocked eligibility, preview idempotency, and publish guards."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.models.entities import (
    PublicationStatus,
    PullRequestPublication,
    RepairOutcome,
    ReviewDecision,
)
from app.services.publish import (
    PublishError,
    check_eligibility,
    feature_branch_name,
    identity_key,
    preview_hash,
    publish,
)


def test_feature_branch_naming():
    rid = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    name = feature_branch_name(rid, "0123456789abcdef")
    assert name == "bugtime/fix-aaaaaaaa-01234567"


def test_preview_hash_stable():
    a = preview_hash("t", "b", "h", "sha", "branch")
    b = preview_hash("t", "b", "h", "sha", "branch")
    assert a == b
    assert a != preview_hash("t2", "b", "h", "sha", "branch")


def test_eligibility_requires_accepted_verified(tmp_path: Path):
    attempt = MagicMock()
    attempt.patch_hash = "abc"
    attempt.patch_artifact_path = str(tmp_path / "p.patch")
    Path(attempt.patch_artifact_path).write_text("diff", encoding="utf-8")
    attempt.review = MagicMock(decision=ReviewDecision.rejected, patch_hash="abc")
    attempt.verification = MagicMock(outcome=RepairOutcome.verified_fix)
    attempt.repair_run = MagicMock(
        outcome=RepairOutcome.verified_fix,
        investigation_id=uuid.uuid4(),
        target_sha="deadbeef",
        runner_image="img",
        bundle_id=uuid.uuid4(),
        id=uuid.uuid4(),
    )
    db = MagicMock()
    inv = MagicMock(repository_id=uuid.uuid4())
    repo = MagicMock(github_url="https://github.com/acme/demo", branch="main", id=inv.repository_id)
    db.get.side_effect = lambda model, key: inv if key == attempt.repair_run.investigation_id else repo

    elig = check_eligibility(db, attempt)
    assert elig["eligible"] is False
    assert any("accepted" in r.lower() for r in elig["reasons"])


def test_publish_rejects_stale_preview_hash():
    db = MagicMock()
    pub = MagicMock()
    pub.status = PublicationStatus.ready
    pub.preview_hash = "expected"
    pub.id = uuid.uuid4()
    db.get.return_value = pub
    with pytest.raises(PublishError) as ei:
        publish(db, pub.id, preview_hash_client="wrong")
    assert ei.value.code == "stale_preview"


def test_publish_idempotent_when_already_published():
    db = MagicMock()
    pub = MagicMock()
    pub.status = PublicationStatus.published
    pub.pr_url = "https://github.com/acme/demo/pull/1"
    pub.preview_hash = "x"
    pub.id = uuid.uuid4()
    db.get.return_value = pub
    out = publish(db, pub.id, preview_hash_client="x")
    assert out.pr_url.endswith("/pull/1")


def test_identity_key_includes_base():
    rid = uuid.uuid4()
    repair = uuid.uuid4()
    a = identity_key(rid, repair, "ph", "sha1")
    b = identity_key(rid, repair, "ph", "sha2")
    assert a != b


@patch("app.services.publish.github_api.token_configured", return_value=False)
def test_publish_without_token_reports_pending(_tok):
    db = MagicMock()
    pub = MagicMock()
    pub.status = PublicationStatus.preview
    pub.preview_hash = "hash"
    pub.id = uuid.uuid4()
    db.get.return_value = pub
    with pytest.raises(PublishError) as ei:
        publish(db, pub.id, preview_hash_client="hash")
    assert ei.value.code == "github_not_configured"
    assert "live publication pending" in ei.value.message


@patch("app.services.publish.github_api.token_configured", return_value=True)
@patch("app.services.publish.check_eligibility")
@patch("app.services.publish.load_attempt")
@patch("app.services.publish.read_patch_text", return_value="diff --git a/x b/x\n")
@patch("app.services.publish.github_api.get_branch_head")
@patch("app.services.publish.github_api.list_pulls_for_head", return_value=[])
@patch("app.services.publish.github_api.create_pull_request")
@patch("app.services.publish.github_api.get_pull_request")
def test_publish_recovers_when_branch_exists_with_recorded_sha(
    get_pr,
    create_pr,
    list_prs,
    get_head,
    read_patch,
    load_attempt,
    elig,
    _tok,
):
    """Branch created earlier; PR create failed — retry should open PR without force."""
    db = MagicMock()
    pub = MagicMock()
    pub.status = PublicationStatus.failed
    pub.preview_hash = "hash"
    pub.id = uuid.uuid4()
    pub.verified_base_sha = "basebasebase"
    pub.owner = "acme"
    pub.repo_name = "demo"
    pub.target_branch = "main"
    pub.feature_branch = "bugtime/fix-aaaa-01234567"
    pub.published_commit_sha = "commitcommit"
    pub.title = "fix: demo"
    pub.body = "body with patch hash abc"
    pub.patch_hash = "abcdefgh"
    db.get.return_value = pub

    elig.return_value = {"eligible": True, "reasons": []}
    load_attempt.return_value = MagicMock()

    def heads(owner, repo, branch):
        if branch == "main":
            return "basebasebase"
        if branch == pub.feature_branch:
            return "commitcommit"
        return None

    get_head.side_effect = heads
    create_pr.return_value = {"number": 7}
    get_pr.return_value = {
        "number": 7,
        "html_url": "https://github.com/acme/demo/pull/7",
        "draft": True,
        "head": {"sha": "commitcommit"},
    }

    out = publish(db, pub.id, preview_hash_client="hash")
    assert out.status == PublicationStatus.published
    create_pr.assert_called_once()
    assert out.pr_number == 7


def test_pr_body_marks_bugtime_not_ci():
    from app.services.publish import build_pr_body

    attempt = MagicMock()
    attempt.explanation = "fixed null"
    attempt.changed_files = ["a.py"]
    attempt.patch_hash = "ph"
    attempt.id = uuid.uuid4()
    attempt.provider = "openai"
    attempt.model = "gpt"
    attempt.limitations = None
    attempt.verification = MagicMock(
        outcome=RepairOutcome.verified_fix,
        summary="ok",
        reproduction_after={"passed": True},
        new_failures=[],
        missing_tests=[],
        unchanged_failures=[],
    )
    run = MagicMock()
    run.target_sha = "abc"
    run.runner_image = "img"
    run.investigation_id = uuid.uuid4()
    run.id = uuid.uuid4()
    run.limitations = None
    attempt.repair_run = run
    db = MagicMock()
    inv = MagicMock(actual_behavior="crash", expected_behavior="ok", description="d")
    db.get.return_value = inv
    body = build_pr_body(db, attempt, remote_sha="abc")
    assert "BugTime" in body
    assert "not GitHub CI" in body or "not** GitHub" in body.lower() or "not GitHub Actions" in body
