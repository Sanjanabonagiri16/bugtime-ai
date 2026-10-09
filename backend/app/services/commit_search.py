from __future__ import annotations

import hashlib
import logging
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from git import Repo
from git.exc import GitCommandError
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.models.entities import (
    CommitMetadata,
    CommitSearch,
    CommitSearchOutcome,
    CommitSearchStatus,
    CommitSearchStep,
    Evidence,
    ExecutionRun,
    ExecutionStatus,
    Investigation,
    Job,
    JobStatus,
    ReproductionBundle,
    RunType,
    StepClassification,
    StepPhase,
    TestOutcome,
    TestResult,
)
from app.services.activity import log_event
from app.services.sandbox import (
    _append_output,
    _make_writable_for_runner,
    _parse_junit,
    _parse_pytest_stdout,
    _prepare_checkout,
    _run_container,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def resolve_sha(repo: Repo, rev: str) -> str:
    return repo.commit(rev).hexsha


def ensure_commit_metadata(db: Session, repository_id: uuid.UUID, git_repo: Repo, sha: str) -> CommitMetadata:
    existing = (
        db.query(CommitMetadata)
        .filter(CommitMetadata.repository_id == repository_id, CommitMetadata.sha == sha)
        .first()
    )
    if existing:
        return existing
    c = git_repo.commit(sha)
    parents = [p.hexsha for p in c.parents]
    meta = CommitMetadata(
        repository_id=repository_id,
        sha=sha,
        parent_shas=parents,
        author_name=c.author.name if c.author else None,
        author_email=c.author.email if c.author else None,
        authored_at=datetime.fromtimestamp(c.authored_date, tz=timezone.utc),
        subject=c.summary,
    )
    db.add(meta)
    db.commit()
    db.refresh(meta)
    return meta


def ancestry_has_merges(git_repo: Repo, good_sha: str, bad_sha: str) -> bool:
    try:
        commits = list(git_repo.iter_commits(f"{good_sha}..{bad_sha}"))
    except GitCommandError:
        return True
    return any(len(c.parents) > 1 for c in commits)


def is_ancestor(git_repo: Repo, maybe_ancestor: str, commit: str) -> bool:
    try:
        git_repo.git.merge_base("--is-ancestor", maybe_ancestor, commit)
        return True
    except GitCommandError:
        return False


def build_reproduction_bundle(
    db: Session,
    investigation: Investigation,
    source_sha: str,
    test_node_id: str,
    failure_exception_type: str | None,
    failure_message_fragment: str | None,
) -> ReproductionBundle:
    """Freeze selected test file (+ test-only helpers) from the failing revision."""
    settings = get_settings()
    tmp = Path(settings.data_dir) / "bundles" / f"build-{uuid.uuid4()}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        _prepare_checkout(db, investigation, source_sha, tmp)
        test_file = test_node_id.split("::", 1)[0]
        manifest: list[str] = []
        hasher = hashlib.sha256()

        def add_file(rel: str) -> None:
            src = tmp / rel
            if not src.is_file():
                return
            if rel in manifest:
                return
            manifest.append(rel)
            data = src.read_bytes()
            hasher.update(rel.encode())
            hasher.update(b"\0")
            hasher.update(data)

        add_file(test_file)
        # Test-only helpers living beside the test module
        test_dir = Path(test_file).parent
        for name in ("conftest.py", "helpers.py", "fixtures.py"):
            add_file(str(test_dir / name))
        # Root conftest is also test-only
        add_file("conftest.py")

        if test_file not in manifest:
            raise RuntimeError(f"Selected test file missing at {source_sha[:12]}: {test_file}")

        content_hash = hasher.hexdigest()
        artifact_rel = f"bundles/{content_hash}"
        artifact = Path(settings.data_dir) / artifact_rel
        if artifact.exists():
            shutil.rmtree(artifact)
        artifact.mkdir(parents=True, exist_ok=True)
        for rel in manifest:
            dest = artifact / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(tmp / rel, dest)

        bundle = ReproductionBundle(
            investigation_id=investigation.id,
            source_sha=source_sha,
            test_node_id=test_node_id,
            file_manifest=manifest,
            artifact_path=str(artifact),
            content_hash=content_hash,
            failure_exception_type=failure_exception_type,
            failure_message_fragment=failure_message_fragment,
        )
        db.add(bundle)
        db.commit()
        db.refresh(bundle)
        return bundle
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _overlay_bundle(checkout: Path, bundle: ReproductionBundle) -> list[str]:
    overlayed: list[str] = []
    src_root = Path(bundle.artifact_path)
    for rel in bundle.file_manifest or []:
        src = src_root / rel
        if not src.is_file():
            continue
        dest = checkout / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        overlayed.append(rel)
    return overlayed


def _matches_target_failure(
    bundle: ReproductionBundle,
    results: list[dict],
    stdout: str,
    stderr: str,
) -> tuple[bool, str]:
    """Return (is_target_failure, reason)."""
    node = bundle.test_node_id
    hit = next((r for r in results if r["test_identifier"] == node), None)
    if not hit:
        return False, "selected test did not produce a result"
    if hit["outcome"] not in {"failed", "error"}:
        return False, f"selected test outcome was {hit['outcome']}"

    msg = (hit.get("failure_message") or "") + "\n" + (hit.get("traceback") or "")
    combined = msg + "\n" + stdout + "\n" + stderr

    frag = (bundle.failure_message_fragment or "").strip()
    exc = (bundle.failure_exception_type or "").strip()
    if frag and frag not in combined:
        return False, "failure message did not match configured fragment"
    if exc:
        short = exc.rsplit(".", 1)[-1]
        if short not in combined and exc not in combined:
            # Pytest assertion rewriting often omits the literal "AssertionError"
            # and only shows "E   assert …" / "assert False is True".
            assertion_rewrite = short == "AssertionError" and (
                "assert " in combined
                or "\nE   assert" in combined
                or combined.lstrip().startswith("assert ")
            )
            if not assertion_rewrite:
                return False, f"exception type {exc} not observed"
    return True, "target failure matched"


def _classify_probe(
    bundle: ReproductionBundle,
    *,
    exit_code: int,
    results: list[dict],
    stdout: str,
    stderr: str,
    timed_out: bool,
    cancelled: bool,
    error_category: str | None,
) -> tuple[StepClassification, str]:
    if cancelled:
        return StepClassification.abort, "cancelled"
    if timed_out:
        return StepClassification.skip, "timeout"
    if error_category == "environment_setup_unsupported":
        return StepClassification.skip, "missing dependencies / unsupported environment"
    combined = (stdout + "\n" + stderr).lower()
    if "error during collection" in combined or "collection error" in combined:
        return StepClassification.skip, "collection error"
    if exit_code == 5 or "no tests collected" in combined:
        return StepClassification.skip, "missing test / no tests collected"
    if error_category in {"collection_error", "no_tests_collected"}:
        return StepClassification.skip, error_category

    node = bundle.test_node_id
    hit = next((r for r in results if r["test_identifier"] == node), None)
    if hit is None:
        return StepClassification.skip, "selected test missing from results"

    if hit["outcome"] == "passed":
        return StepClassification.good, "selected test passed"

    if hit["outcome"] in {"failed", "error"}:
        matched, reason = _matches_target_failure(bundle, results, stdout, stderr)
        if matched:
            return StepClassification.bad, reason
        return StepClassification.skip, f"unrelated failure: {reason}"

    return StepClassification.skip, f"untestable outcome {hit['outcome']}"


def _run_probe(
    db: Session,
    search: CommitSearch,
    investigation: Investigation,
    bundle: ReproductionBundle,
    sha: str,
    phase: StepPhase,
    attempt: int,
) -> CommitSearchStep:
    settings = get_settings()
    run = ExecutionRun(
        investigation_id=investigation.id,
        pinned_sha=sha,
        run_type=RunType.commit_probe,
        selected_tests=[bundle.test_node_id],
        status=ExecutionStatus.preparing,
        stage="preparing",
        started_at=_utcnow(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    started = time.time()
    exec_root = Path(settings.data_dir) / "executions" / str(run.id)
    checkout_rel = f"executions/{run.id}/checkout"
    checkout = Path(settings.data_dir) / checkout_rel
    artifacts_dir = exec_root / "artifacts"
    _make_writable_for_runner(exec_root)
    _make_writable_for_runner(artifacts_dir)
    output_path = artifacts_dir / "output.log"

    try:
        _prepare_checkout(db, investigation, sha, checkout)
        overlayed = _overlay_bundle(checkout, bundle)
        _make_writable_for_runner(checkout)
        note = f"Overlayed bundle files: {', '.join(overlayed)}" if overlayed else "No overlay files"
        log_event(
            db,
            stage="commit_probe",
            message=f"Probe {sha[:12]} ({phase.value}): {note}",
            investigation_id=investigation.id,
            repository_id=investigation.repository_id,
        )
    except Exception as exc:  # noqa: BLE001
        run.status = ExecutionStatus.failed
        run.error_category = "internal_error"
        run.error_message = f"Checkout failed: {exc}"
        run.completed_at = _utcnow()
        run.duration_ms = int((time.time() - started) * 1000)
        db.commit()
        step = CommitSearchStep(
            search_id=search.id,
            sha=sha,
            phase=phase,
            classification=StepClassification.skip,
            classification_reason=run.error_message,
            attempt=attempt,
            execution_id=run.id,
            duration_ms=run.duration_ms,
        )
        db.add(step)
        search.executions_used += 1
        db.commit()
        db.refresh(step)
        return step

    run.status = ExecutionStatus.running
    run.stage = "running"
    db.commit()

    container_junit = f"/data/executions/{run.id}/artifacts/junit.xml"
    command = ["-q", f"--junitxml={container_junit}", "--tb=short", bundle.test_node_id]
    result = _run_container(
        db,
        run,
        checkout_rel=checkout_rel,
        command=command,
        output_path=output_path,
    )

    # Persist digest on search (fixed for the run)
    if run.runner_image_digest and not search.runner_image_digest:
        search.runner_image = run.runner_image
        search.runner_image_digest = run.runner_image_digest
    db.commit()

    junit_path = artifacts_dir / "junit.xml"
    if result.junit_xml:
        junit_path.write_text(result.junit_xml, encoding="utf-8")
    parsed = _parse_junit(junit_path)
    if not parsed:
        parsed = _parse_pytest_stdout(result.stdout, [bundle.test_node_id])

    for item in parsed:
        db.add(
            TestResult(
                execution_id=run.id,
                test_identifier=item["test_identifier"],
                outcome=TestOutcome(item["outcome"]),
                duration_ms=item.get("duration_ms"),
                failure_message=item.get("failure_message"),
                traceback=item.get("traceback"),
                in_evidence=False,
            )
        )

    run.exit_code = result.exit_code
    run.duration_ms = int((time.time() - started) * 1000)
    run.completed_at = _utcnow()
    run.stage = "finished"
    if result.cancelled:
        run.status = ExecutionStatus.cancelled
    elif result.timed_out:
        run.status = ExecutionStatus.timed_out
    elif result.error_category == "environment_setup_unsupported":
        run.status = ExecutionStatus.failed
        run.error_category = result.error_category
        run.error_message = result.error_message
    else:
        run.status = ExecutionStatus.completed
        run.error_category = result.error_category
        run.error_message = result.error_message

    classification, reason = _classify_probe(
        bundle,
        exit_code=result.exit_code,
        results=parsed,
        stdout=result.stdout,
        stderr=result.stderr,
        timed_out=result.timed_out,
        cancelled=result.cancelled or search.cancel_requested,
        error_category=result.error_category,
    )

    step = CommitSearchStep(
        search_id=search.id,
        sha=sha,
        phase=phase,
        classification=classification,
        classification_reason=reason,
        attempt=attempt,
        execution_id=run.id,
        duration_ms=run.duration_ms,
    )
    db.add(step)
    search.executions_used += 1
    if phase == StepPhase.search:
        search.commits_tested += 1
    db.commit()
    db.refresh(step)

    # Cleanup checkout to save disk; keep artifacts/logs
    shutil.rmtree(checkout, ignore_errors=True)
    return step


def _budget_exceeded(search: CommitSearch, started_at: float) -> str | None:
    if search.cancel_requested:
        return "cancelled"
    if search.executions_used >= search.max_executions:
        return "budget_exhausted"
    if time.time() - started_at >= search.max_duration_seconds:
        return "timed_out"
    return None


def _finish(
    db: Session,
    search: CommitSearch,
    job: Job,
    *,
    status: CommitSearchStatus,
    outcome: CommitSearchOutcome | None,
    error: str | None = None,
) -> None:
    search.status = status
    search.outcome = outcome
    search.error_message = error
    search.completed_at = _utcnow()
    search.stage = status.value
    job.status = JobStatus.completed if status == CommitSearchStatus.completed else JobStatus.failed
    if status == CommitSearchStatus.cancelled:
        job.status = JobStatus.completed
    job.completed_at = _utcnow()
    job.error_message = error
    db.commit()


def run_commit_search_job(db: Session, job_id: uuid.UUID) -> None:
    job = db.get(Job, job_id)
    if not job or not job.commit_search_id:
        return
    job.status = JobStatus.running
    job.attempt += 1
    job.started_at = _utcnow()
    db.commit()

    search = (
        db.query(CommitSearch)
        .options(joinedload(CommitSearch.bundle), joinedload(CommitSearch.steps))
        .filter(CommitSearch.id == job.commit_search_id)
        .first()
    )
    if not search:
        job.status = JobStatus.failed
        job.error_message = "Commit search missing"
        job.completed_at = _utcnow()
        db.commit()
        return

    investigation = (
        db.query(Investigation)
        .options(joinedload(Investigation.repository))
        .filter(Investigation.id == search.investigation_id)
        .first()
    )
    if not investigation:
        _finish(
            db,
            search,
            job,
            status=CommitSearchStatus.failed,
            outcome=None,
            error="Investigation missing",
        )
        return

    bundle = search.bundle
    started_at = time.time()
    search.started_at = _utcnow()
    search.status = CommitSearchStatus.validating
    search.stage = "validating"
    db.commit()
    log_event(
        db,
        stage="commit_search",
        message="Validating search endpoints with frozen reproduction bundle",
        investigation_id=investigation.id,
        repository_id=investigation.repository_id,
    )

    # --- Validate good endpoint (2 runs) ---
    good_classes: list[StepClassification] = []
    for attempt in (1, 2):
        budget = _budget_exceeded(search, started_at)
        if budget:
            status = (
                CommitSearchStatus.cancelled
                if budget == "cancelled"
                else CommitSearchStatus.timed_out
                if budget == "timed_out"
                else CommitSearchStatus.failed
            )
            outcome = (
                None
                if budget == "cancelled"
                else CommitSearchOutcome.budget_exhausted
                if budget == "budget_exhausted"
                else None
            )
            _finish(db, search, job, status=status, outcome=outcome, error=budget)
            return
        step = _run_probe(
            db, search, investigation, bundle, search.good_sha, StepPhase.validate_good, attempt
        )
        good_classes.append(step.classification)
        if step.classification == StepClassification.abort:
            _finish(
                db,
                search,
                job,
                status=CommitSearchStatus.cancelled,
                outcome=None,
                error="cancelled",
            )
            return

    if good_classes[0] != good_classes[1]:
        _finish(
            db,
            search,
            job,
            status=CommitSearchStatus.completed,
            outcome=CommitSearchOutcome.inconsistent_reproduction,
            error="Inconsistent reproduction at candidate good commit",
        )
        return
    if good_classes[0] != StepClassification.good:
        _finish(
            db,
            search,
            job,
            status=CommitSearchStatus.completed,
            outcome=CommitSearchOutcome.invalid_range,
            error="Invalid search range: candidate good commit did not pass",
        )
        return

    # --- Validate bad endpoint (2 runs) ---
    bad_classes: list[StepClassification] = []
    for attempt in (1, 2):
        budget = _budget_exceeded(search, started_at)
        if budget:
            status = (
                CommitSearchStatus.cancelled
                if budget == "cancelled"
                else CommitSearchStatus.timed_out
                if budget == "timed_out"
                else CommitSearchStatus.failed
            )
            _finish(
                db,
                search,
                job,
                status=status,
                outcome=CommitSearchOutcome.budget_exhausted if budget == "budget_exhausted" else None,
                error=budget,
            )
            return
        step = _run_probe(
            db, search, investigation, bundle, search.bad_sha, StepPhase.validate_bad, attempt
        )
        bad_classes.append(step.classification)
        if step.classification == StepClassification.abort:
            _finish(
                db,
                search,
                job,
                status=CommitSearchStatus.cancelled,
                outcome=None,
                error="cancelled",
            )
            return

    if bad_classes[0] != bad_classes[1]:
        _finish(
            db,
            search,
            job,
            status=CommitSearchStatus.completed,
            outcome=CommitSearchOutcome.inconsistent_reproduction,
            error="Inconsistent reproduction at pinned bad commit",
        )
        return
    if bad_classes[0] != StepClassification.bad:
        _finish(
            db,
            search,
            job,
            status=CommitSearchStatus.completed,
            outcome=CommitSearchOutcome.invalid_range,
            error="Invalid search range: pinned bad commit did not exhibit target failure",
        )
        return

    # --- Bisect search ---
    search.status = CommitSearchStatus.searching
    search.stage = "searching"
    db.commit()

    settings = get_settings()
    work = Path(settings.data_dir) / "commit-searches" / str(search.id) / "workspace"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    _prepare_checkout(db, investigation, search.bad_sha, work)
    git_repo = Repo(work)

    # Persist metadata for endpoints
    ensure_commit_metadata(db, investigation.repository_id, git_repo, search.good_sha)
    ensure_commit_metadata(db, investigation.repository_id, git_repo, search.bad_sha)

    try:
        # Manual binary search over linear ancestry (good..bad]
        remaining = [
            c.hexsha
            for c in git_repo.iter_commits(f"{search.good_sha}..{search.bad_sha}")
        ]
        remaining.reverse()  # oldest first after good
        if search.max_commits and len(remaining) > search.max_commits:
            # Keep evenly spaced sample? Spec: default 20 distinct commits budget.
            # Prefer testing within budget via bisect; still allow full list for navigation.
            pass

        lo = search.good_sha  # known good
        known_bad = search.bad_sha
        skipped: list[str] = []
        candidate: str | None = None

        # Use git bisect plumbing for classification adapter
        git_repo.git.bisect("start")
        git_repo.git.bisect("bad", known_bad)
        git_repo.git.bisect("good", lo)

        while True:
            budget = _budget_exceeded(search, started_at)
            if budget:
                try:
                    git_repo.git.bisect("reset")
                except GitCommandError:
                    pass
                status = (
                    CommitSearchStatus.cancelled
                    if budget == "cancelled"
                    else CommitSearchStatus.timed_out
                    if budget == "timed_out"
                    else CommitSearchStatus.failed
                )
                _finish(
                    db,
                    search,
                    job,
                    status=status,
                    outcome=CommitSearchOutcome.budget_exhausted
                    if budget == "budget_exhausted"
                    else None,
                    error=budget,
                )
                return

            # Current bisect commit is HEAD after start/good/bad
            try:
                current = git_repo.head.commit.hexsha
            except Exception:  # noqa: BLE001
                break

            git_dir = Path(git_repo.git_dir)
            if not (git_dir / "BISECT_LOG").exists() and not (git_dir / "BISECT_START").exists():
                break

            ensure_commit_metadata(db, investigation.repository_id, git_repo, current)
            step = _run_probe(
                db, search, investigation, bundle, current, StepPhase.search, 1
            )
            if step.classification == StepClassification.abort:
                try:
                    git_repo.git.bisect("reset")
                except GitCommandError:
                    pass
                _finish(
                    db,
                    search,
                    job,
                    status=CommitSearchStatus.cancelled,
                    outcome=None,
                    error="cancelled",
                )
                return

            # Adapter: do not pass raw pytest codes to git
            try:
                if step.classification == StepClassification.good:
                    out = git_repo.git.bisect("good")
                elif step.classification == StepClassification.bad:
                    out = git_repo.git.bisect("bad")
                else:
                    skipped.append(current)
                    out = git_repo.git.bisect("skip")
            except GitCommandError as exc:
                out = str(exc)

            lowered = out.lower()
            if "is the first bad commit" in lowered or "first bad commit" in lowered:
                for line in out.splitlines():
                    parts = line.strip().split()
                    if parts and len(parts[0]) >= 7:
                        try:
                            candidate = git_repo.commit(parts[0]).hexsha
                            break
                        except Exception:  # noqa: BLE001
                            continue
                break

            if "bisect run cannot continue" in lowered or "no valid paths" in lowered:
                break

            # Safety: avoid infinite loop if HEAD does not move
            try:
                new_head = git_repo.head.commit.hexsha
            except Exception:  # noqa: BLE001
                break
            if new_head == current and "bisecting" not in lowered:
                # Likely finished without standard message
                candidate = current
                break

        try:
            if candidate is None:
                bad_steps = [
                    s
                    for s in (
                        db.query(CommitSearchStep)
                        .filter(
                            CommitSearchStep.search_id == search.id,
                            CommitSearchStep.phase == StepPhase.search,
                            CommitSearchStep.classification == StepClassification.bad,
                        )
                        .all()
                    )
                ]
                if bad_steps:
                    # Pick oldest bad by git rev-list order
                    ordered = [
                        c.hexsha
                        for c in git_repo.iter_commits(f"{search.good_sha}..{search.bad_sha}")
                    ]
                    ordered.reverse()
                    for sha in ordered:
                        if any(s.sha == sha for s in bad_steps):
                            candidate = sha
                            break
                    if not candidate:
                        candidate = bad_steps[0].sha
        finally:
            try:
                git_repo.git.bisect("reset")
            except GitCommandError:
                pass

        if not candidate:
            # Inconclusive from skips
            search.candidate_shas = skipped or []
            _finish(
                db,
                search,
                job,
                status=CommitSearchStatus.completed,
                outcome=CommitSearchOutcome.inconclusive,
                error="Skipped revisions prevented a unique result",
            )
            return

        # If skips left multiple possible first-bads, git usually still picks one;
        # when skip creates ambiguity, report all candidates.
        # Approximate: if many skips, mark inconclusive with candidate list.
        if skipped and len(skipped) >= 3:
            # Still attempt verification of git's candidate; if verify fails → inconclusive
            pass

        search.candidate_shas = [candidate]
        search.verified_sha = candidate
        parents = git_repo.commit(candidate).parents
        if not parents:
            _finish(
                db,
                search,
                job,
                status=CommitSearchStatus.completed,
                outcome=CommitSearchOutcome.inconclusive,
                error="Candidate has no parent to verify",
            )
            return
        parent_sha = parents[0].hexsha
        search.parent_sha = parent_sha
        ensure_commit_metadata(db, investigation.repository_id, git_repo, candidate)
        ensure_commit_metadata(db, investigation.repository_id, git_repo, parent_sha)

        # --- Verify boundary ---
        search.status = CommitSearchStatus.verifying
        search.stage = "verifying"
        db.commit()
        log_event(
            db,
            stage="commit_search",
            message=f"Verifying boundary at {candidate[:12]} (parent {parent_sha[:12]})",
            investigation_id=investigation.id,
            repository_id=investigation.repository_id,
        )

        parent_results: list[StepClassification] = []
        for attempt in (1, 2):
            budget = _budget_exceeded(search, started_at)
            if budget:
                _finish(
                    db,
                    search,
                    job,
                    status=CommitSearchStatus.timed_out
                    if budget == "timed_out"
                    else CommitSearchStatus.cancelled
                    if budget == "cancelled"
                    else CommitSearchStatus.failed,
                    outcome=CommitSearchOutcome.budget_exhausted
                    if budget == "budget_exhausted"
                    else None,
                    error=budget,
                )
                return
            step = _run_probe(
                db, search, investigation, bundle, parent_sha, StepPhase.verify_parent, attempt
            )
            parent_results.append(step.classification)

        cand_results: list[StepClassification] = []
        for attempt in (1, 2):
            budget = _budget_exceeded(search, started_at)
            if budget:
                _finish(
                    db,
                    search,
                    job,
                    status=CommitSearchStatus.timed_out
                    if budget == "timed_out"
                    else CommitSearchStatus.cancelled
                    if budget == "cancelled"
                    else CommitSearchStatus.failed,
                    outcome=CommitSearchOutcome.budget_exhausted
                    if budget == "budget_exhausted"
                    else None,
                    error=budget,
                )
                return
            step = _run_probe(
                db, search, investigation, bundle, candidate, StepPhase.verify_candidate, attempt
            )
            cand_results.append(step.classification)

        parent_ok = parent_results == [StepClassification.good, StepClassification.good]
        cand_ok = cand_results == [StepClassification.bad, StepClassification.bad]

        if parent_ok and cand_ok:
            search.verified_sha = candidate
            search.candidate_shas = [candidate]
            _finish(
                db,
                search,
                job,
                status=CommitSearchStatus.completed,
                outcome=CommitSearchOutcome.verified_boundary,
            )
            log_event(
                db,
                stage="commit_search",
                message=f"Verified failure boundary at {candidate[:12]}",
                investigation_id=investigation.id,
                repository_id=investigation.repository_id,
            )
            return

        # Keep candidate, report inconclusive
        candidates = list({candidate, *skipped})
        search.candidate_shas = candidates
        _finish(
            db,
            search,
            job,
            status=CommitSearchStatus.completed,
            outcome=CommitSearchOutcome.inconclusive,
            error="Verification failed or parent untestable; preserving candidate",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Commit search failed")
        _finish(
            db,
            search,
            job,
            status=CommitSearchStatus.failed,
            outcome=None,
            error=str(exc),
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def evidence_paths_for_investigation(db: Session, investigation_id: uuid.UUID) -> set[str]:
    from app.models.entities import CodeChunk

    rows = (
        db.query(CodeChunk)
        .join(Evidence, Evidence.chunk_id == CodeChunk.id)
        .options(joinedload(CodeChunk.source_file))
        .filter(Evidence.investigation_id == investigation_id)
        .all()
    )
    return {c.source_file.path.replace("\\", "/") for c in rows if c.source_file}
