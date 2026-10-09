"""P3 repair orchestration: LangGraph stages + sandbox verification."""

from __future__ import annotations

import json
import logging
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from git import Repo
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.models.entities import (
    CommitSearch,
    CommitSearchOutcome,
    ExecutionRun,
    ExecutionStatus,
    Investigation,
    Job,
    JobStatus,
    PatchAttempt,
    RepairOutcome,
    RepairRun,
    RepairStatus,
    ReproductionBundle,
    RunType,
    TestOutcome,
    TestResult,
    VerificationComparison,
)
from app.services.activity import log_event
from app.services.commit_search import _matches_target_failure, _overlay_bundle
from app.services.model_provider import (
    ModelNotConfiguredError,
    ModelProviderError,
    PROMPT_VERSION,
    propose_repair,
    provider_configured,
)
from app.services.patch import (
    apply_files,
    list_existing_tests,
    validate_proposal,
)
from app.services.sandbox import (
    _make_writable_for_runner,
    _parse_junit,
    _parse_pytest_stdout,
    _prepare_checkout,
    _run_container,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RepairGraphState(TypedDict, total=False):
    repair_run_id: str
    job_id: str
    attempt_id: str | None
    feedback: str | None
    stop: bool
    outcome: str | None
    message: str | None


def _postgres_conninfo() -> str:
    settings = get_settings()
    url = settings.database_url
    # langgraph expects postgresql:// not postgresql+psycopg://
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _budget_exceeded(run: RepairRun) -> bool:
    if not run.started_at:
        return False
    elapsed = (_utcnow() - run.started_at).total_seconds()
    return elapsed >= run.max_duration_seconds


def _cancelled(db: Session, run: RepairRun) -> bool:
    db.refresh(run)
    return bool(run.cancel_requested)


def _finish(
    db: Session,
    run: RepairRun,
    job: Job,
    *,
    status: RepairStatus,
    outcome: RepairOutcome | None,
    message: str | None = None,
) -> None:
    run.status = status
    run.outcome = outcome
    run.stage = "completed" if status == RepairStatus.completed else status.value
    run.error_message = message
    run.completed_at = _utcnow()
    job.status = JobStatus.completed if status in {RepairStatus.completed, RepairStatus.cancelled} else JobStatus.failed
    if message and job.status == JobStatus.failed:
        job.error_message = message
    job.completed_at = _utcnow()
    db.commit()


def _run_pytest(
    db: Session,
    investigation: Investigation,
    *,
    sha: str,
    run_type: RunType,
    selected_tests: list[str] | None,
    checkout: Path,
    checkout_rel: str,
    artifacts_dir: Path,
    repair_id: uuid.UUID,
    bundle: ReproductionBundle | None = None,
) -> ExecutionRun:
    run = ExecutionRun(
        investigation_id=investigation.id,
        pinned_sha=sha,
        run_type=run_type,
        selected_tests=selected_tests or [],
        status=ExecutionStatus.preparing,
        stage="preparing",
        started_at=_utcnow(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    started = time.time()
    output_path = artifacts_dir / f"{run.id}-output.log"
    junit_host = artifacts_dir / f"{run.id}-junit.xml"
    _make_writable_for_runner(artifacts_dir)

    if bundle is not None:
        _overlay_bundle(checkout, bundle)
    _make_writable_for_runner(checkout)

    run.status = ExecutionStatus.running
    run.stage = "running"
    db.commit()

    container_junit = f"/data/repairs/{repair_id}/artifacts/{run.id}-junit.xml"
    command = ["-q", f"--junitxml={container_junit}", "--tb=short"]
    if selected_tests:
        command.extend(selected_tests)

    result = _run_container(
        db,
        run,
        checkout_rel=checkout_rel,
        command=command,
        output_path=output_path,
    )

    if not junit_host.exists() and result.junit_xml:
        junit_host.write_text(result.junit_xml, encoding="utf-8")
    parsed = _parse_junit(junit_host)
    if not parsed and selected_tests:
        parsed = _parse_pytest_stdout(result.stdout, selected_tests)

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
    if result.timed_out:
        run.status = ExecutionStatus.timed_out
        run.error_category = "timeout"
    elif result.cancelled:
        run.status = ExecutionStatus.cancelled
    elif result.error_category == "environment_setup_unsupported":
        run.status = ExecutionStatus.failed
        run.error_category = result.error_category
        run.error_message = result.error_message
    else:
        run.status = ExecutionStatus.completed
        run.error_category = result.error_category
    db.commit()
    db.refresh(run)
    return run


def _test_map(db: Session, execution_id: uuid.UUID) -> dict[str, dict]:
    rows = db.query(TestResult).filter(TestResult.execution_id == execution_id).all()
    return {
        r.test_identifier: {
            "test_identifier": r.test_identifier,
            "outcome": r.outcome.value if hasattr(r.outcome, "value") else str(r.outcome),
            "failure_message": r.failure_message,
            "traceback": r.traceback,
        }
        for r in rows
    }


def _collect_evidence_context(db: Session, investigation: Investigation, checkout: Path) -> list[dict]:
    from app.models.entities import Evidence, CodeChunk, SourceFile

    rows = (
        db.query(Evidence)
        .options(joinedload(Evidence.chunk).joinedload(CodeChunk.source_file))
        .filter(Evidence.investigation_id == investigation.id)
        .order_by(Evidence.rank.asc())
        .limit(12)
        .all()
    )
    out: list[dict] = []
    for ev in rows:
        path = ev.chunk.source_file.path
        file_path = checkout / path
        content = ""
        if file_path.is_file():
            content = file_path.read_text(encoding="utf-8", errors="replace")[:4000]
        out.append(
            {
                "path": path,
                "score": ev.semantic_score,
                "snippet": (ev.chunk.content or "")[:1500],
                "file_excerpt": content,
            }
        )
    return out


def _introducing_diff(repo_path: Path, introducing_sha: str | None, parent_sha: str | None) -> str:
    if not introducing_sha or not parent_sha:
        return ""
    try:
        repo = Repo(str(repo_path))
        return repo.git.diff(parent_sha, introducing_sha)[:12000]
    except Exception:  # noqa: BLE001
        return ""


def run_repair_job(db: Session, job_id: uuid.UUID) -> None:
    job = db.get(Job, job_id)
    if not job or not job.repair_run_id:
        return
    run = (
        db.query(RepairRun)
        .options(joinedload(RepairRun.attempts))
        .filter(RepairRun.id == job.repair_run_id)
        .first()
    )
    if not run:
        return
    if run.status in {
        RepairStatus.completed,
        RepairStatus.failed,
        RepairStatus.cancelled,
        RepairStatus.timed_out,
    }:
        job.status = JobStatus.completed
        job.completed_at = _utcnow()
        db.commit()
        return

    if not provider_configured():
        _finish(
            db,
            run,
            job,
            status=RepairStatus.failed,
            outcome=None,
            message="Model provider not configured.",
        )
        return

    job.status = JobStatus.running
    job.started_at = _utcnow()
    if not run.started_at:
        run.started_at = _utcnow()
    run.status = RepairStatus.preparing
    run.stage = "prepare"
    settings = get_settings()
    run.provider = settings.model_provider
    run.model = settings.model_name
    db.commit()

    config = {"configurable": {"thread_id": run.graph_thread_id}}
    initial: RepairGraphState = {
        "repair_run_id": str(run.id),
        "job_id": str(job.id),
        "attempt_id": None,
        "feedback": None,
        "stop": False,
    }

    checkpointer_cm = None
    checkpointer = None
    try:
        from langgraph.checkpoint.postgres import PostgresSaver

        checkpointer_cm = PostgresSaver.from_conn_string(_postgres_conninfo())
        checkpointer = checkpointer_cm.__enter__()
        checkpointer.setup()
    except Exception as pg_exc:  # noqa: BLE001
        logger.warning("Postgres checkpointer unavailable (%s); using MemorySaver", pg_exc)
        if checkpointer_cm is not None:
            try:
                checkpointer_cm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            checkpointer_cm = None
        checkpointer = MemorySaver()

    try:
        compiled = _build_graph().compile(checkpointer=checkpointer)
        compiled.invoke(initial, config)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Repair graph failed")
        db.rollback()
        run = db.get(RepairRun, run.id)
        job = db.get(Job, job.id)
        if run and job:
            _finish(
                db,
                run,
                job,
                status=RepairStatus.failed,
                outcome=None,
                message=str(exc)[:500],
            )
    finally:
        if checkpointer_cm is not None:
            try:
                checkpointer_cm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass


def _load_ctx(db: Session, repair_run_id: str) -> tuple[RepairRun, Investigation, CommitSearch, ReproductionBundle, Job]:
    run = db.get(RepairRun, uuid.UUID(repair_run_id))
    assert run
    inv = (
        db.query(Investigation)
        .options(joinedload(Investigation.repository))
        .filter(Investigation.id == run.investigation_id)
        .first()
    )
    assert inv
    search = db.get(CommitSearch, run.commit_search_id)
    assert search
    bundle = db.get(ReproductionBundle, run.bundle_id)
    assert bundle
    job = (
        db.query(Job)
        .filter(Job.repair_run_id == run.id)
        .order_by(Job.created_at.desc())
        .first()
    )
    assert job
    return run, inv, search, bundle, job


def _node_prepare(state: RepairGraphState) -> RepairGraphState:
    db = SessionLocal()
    try:
        run, inv, search, bundle, job = _load_ctx(db, state["repair_run_id"])
        if _cancelled(db, run):
            _finish(db, run, job, status=RepairStatus.cancelled, outcome=None, message="Cancelled")
            return {**state, "stop": True}
        if search.outcome != CommitSearchOutcome.verified_boundary or not search.verified_sha:
            _finish(
                db,
                run,
                job,
                status=RepairStatus.failed,
                outcome=None,
                message="Requires a verified_boundary commit search.",
            )
            return {**state, "stop": True}
        run.status = RepairStatus.preparing
        run.stage = "prepare"
        db.commit()
        log_event(
            db,
            stage="repair",
            message=f"Repair prepare: target={run.target_sha[:12]} introducing={ (run.introducing_sha or '')[:12]}",
            investigation_id=inv.id,
            repository_id=inv.repository_id,
        )
        return {**state, "stop": False}
    finally:
        db.close()


def _node_baseline(state: RepairGraphState) -> RepairGraphState:
    from app.db.session import SessionLocal as SL

    db = SL()
    try:
        run, inv, search, bundle, job = _load_ctx(db, state["repair_run_id"])
        if state.get("stop") or _cancelled(db, run) or _budget_exceeded(run):
            if _budget_exceeded(run) and not state.get("stop"):
                _finish(db, run, job, status=RepairStatus.timed_out, outcome=RepairOutcome.budget_exhausted)
            return {**state, "stop": True}

        run.status = RepairStatus.baseline
        run.stage = "baseline"
        db.commit()

        settings = get_settings()
        root = Path(settings.data_dir) / "repairs" / str(run.id)
        checkout = root / "checkout"
        artifacts = root / "artifacts"
        _make_writable_for_runner(root)
        _make_writable_for_runner(artifacts)
        _prepare_checkout(db, inv, run.target_sha, checkout)
        checkout_rel = f"repairs/{run.id}/checkout"

        # Confirm frozen reproduction fails once at baseline (store as before)
        repro = _run_pytest(
            db,
            inv,
            sha=run.target_sha,
            run_type=RunType.repair_probe,
            selected_tests=[bundle.test_node_id],
            checkout=checkout,
            checkout_rel=checkout_rel,
            artifacts_dir=artifacts,
            repair_id=run.id,
            bundle=bundle,
        )
        results = list(_test_map(db, repro.id).values())
        matched, reason = _matches_target_failure(bundle, results, "", "")
        hit = next((r for r in results if r["test_identifier"] == bundle.test_node_id), None)
        if not matched and (not hit or hit.get("outcome") not in {"failed", "error"}):
            _finish(
                db,
                run,
                job,
                status=RepairStatus.completed,
                outcome=RepairOutcome.baseline_invalid,
                message=f"Baseline reproduction did not fail: {reason}",
            )
            return {**state, "stop": True, "outcome": RepairOutcome.baseline_invalid.value}

        suite = _run_pytest(
            db,
            inv,
            sha=run.target_sha,
            run_type=RunType.repair_suite,
            selected_tests=None,
            checkout=checkout,
            checkout_rel=checkout_rel,
            artifacts_dir=artifacts,
            repair_id=run.id,
            bundle=None,
        )

        # Persist baseline ids onto run via a side channel file
        meta_path = root / "baseline.json"
        meta_path.write_text(
            json.dumps(
                {
                    "repro_id": str(repro.id),
                    "suite_id": str(suite.id),
                    "suite_tests": _test_map(db, suite.id),
                }
            ),
            encoding="utf-8",
        )
        return {**state, "stop": False}
    finally:
        db.close()


def _node_retrieve(state: RepairGraphState) -> RepairGraphState:
    from app.db.session import SessionLocal as SL

    db = SL()
    try:
        run, inv, search, bundle, job = _load_ctx(db, state["repair_run_id"])
        if state.get("stop") or _cancelled(db, run):
            if _cancelled(db, run):
                _finish(db, run, job, status=RepairStatus.cancelled, outcome=None, message="Cancelled")
            return {**state, "stop": True}
        run.status = RepairStatus.retrieving
        run.stage = "retrieve"
        db.commit()
        settings = get_settings()
        root = Path(settings.data_dir) / "repairs" / str(run.id)
        checkout = root / "checkout"
        if not checkout.exists():
            _prepare_checkout(db, inv, run.target_sha, checkout)
        evidence = _collect_evidence_context(db, inv, checkout)
        intro_diff = _introducing_diff(
            Path(inv.repository.local_path) if inv.repository.local_path else checkout,
            run.introducing_sha,
            search.parent_sha,
        )
        (root / "context.json").write_text(
            json.dumps({"evidence": evidence, "introducing_diff": intro_diff}),
            encoding="utf-8",
        )
        return {**state, "stop": False}
    finally:
        db.close()


def _node_propose(state: RepairGraphState) -> RepairGraphState:
    from app.db.session import SessionLocal as SL

    db = SL()
    try:
        run, inv, search, bundle, job = _load_ctx(db, state["repair_run_id"])
        if state.get("stop") or _cancelled(db, run) or _budget_exceeded(run):
            if _budget_exceeded(run) and not run.completed_at:
                _finish(db, run, job, status=RepairStatus.timed_out, outcome=RepairOutcome.budget_exhausted)
            return {**state, "stop": True}

        if run.attempt_count >= run.max_attempts:
            _finish(
                db,
                run,
                job,
                status=RepairStatus.completed,
                outcome=RepairOutcome.budget_exhausted,
                message="Maximum patch attempts reached.",
            )
            return {**state, "stop": True, "outcome": RepairOutcome.budget_exhausted.value}

        run.status = RepairStatus.proposing
        run.stage = "propose"
        run.attempt_count += 1
        db.commit()

        settings = get_settings()
        root = Path(settings.data_dir) / "repairs" / str(run.id)
        context_blob = json.loads((root / "context.json").read_text(encoding="utf-8"))
        baseline = json.loads((root / "baseline.json").read_text(encoding="utf-8"))
        repro_map = _test_map(db, uuid.UUID(baseline["repro_id"]))
        failure_output = ""
        for item in repro_map.values():
            failure_output += f"{item.get('failure_message') or ''}\n{item.get('traceback') or ''}\n"

        attempt = PatchAttempt(
            repair_run_id=run.id,
            attempt_number=run.attempt_count,
            provider=settings.model_provider,
            model=settings.model_name,
            prompt_version=PROMPT_VERSION,
            status="proposing",
            feedback=state.get("feedback"),
        )
        db.add(attempt)
        db.commit()
        db.refresh(attempt)

        try:
            proposal = propose_repair(
                {
                    "bug_description": inv.description,
                    "expected_behavior": inv.expected_behavior or "Reproduction test should pass.",
                    "target_sha": run.target_sha,
                    "introducing_sha": run.introducing_sha,
                    "introducing_diff": context_blob.get("introducing_diff"),
                    "evidence_files": context_blob.get("evidence"),
                    "reproduction_test": bundle.test_node_id,
                    "failure_output": failure_output[:6000],
                    "existing_tests": list((baseline.get("suite_tests") or {}).keys())[:40],
                    "previous_feedback": state.get("feedback"),
                }
            )
        except (ModelNotConfiguredError, ModelProviderError) as exc:
            attempt.status = "failed"
            attempt.validation_error = str(exc)
            attempt.completed_at = _utcnow()
            db.commit()
            _finish(db, run, job, status=RepairStatus.failed, outcome=None, message=str(exc))
            return {**state, "stop": True, "message": str(exc)}

        attempt.explanation = proposal.explanation
        attempt.assumptions = proposal.assumptions
        attempt.limitations = proposal.limitations
        attempt.source_references = proposal.source_references
        attempt.tokens_prompt = proposal.tokens_prompt
        attempt.tokens_completion = proposal.tokens_completion
        attempt.context_manifest = {
            "evidence_paths": [e.get("path") for e in context_blob.get("evidence") or []],
            "introducing_sha": run.introducing_sha,
            "target_sha": run.target_sha,
        }
        # Store raw proposal files for validate node
        (root / f"proposal-{attempt.id}.json").write_text(
            json.dumps(
                {
                    "files": proposal.files,
                    "new_test": proposal.new_test,
                    "explanation": proposal.explanation,
                }
            ),
            encoding="utf-8",
        )
        attempt.status = "proposed"
        db.commit()
        return {**state, "attempt_id": str(attempt.id), "stop": False}
    finally:
        db.close()


def _node_validate(state: RepairGraphState) -> RepairGraphState:
    from app.db.session import SessionLocal as SL

    db = SL()
    try:
        run, inv, search, bundle, job = _load_ctx(db, state["repair_run_id"])
        if state.get("stop") or not state.get("attempt_id"):
            return {**state, "stop": True}
        attempt = db.get(PatchAttempt, uuid.UUID(state["attempt_id"]))
        assert attempt
        run.status = RepairStatus.validating
        run.stage = "validate"
        db.commit()

        settings = get_settings()
        root = Path(settings.data_dir) / "repairs" / str(run.id)
        checkout = root / "checkout"
        # Fresh checkout for validation
        if checkout.exists():
            shutil.rmtree(checkout)
        _prepare_checkout(db, inv, run.target_sha, checkout)

        proposal = json.loads((root / f"proposal-{attempt.id}.json").read_text(encoding="utf-8"))
        existing = list_existing_tests(checkout)
        bundle_files = set(bundle.file_manifest or [])
        result = validate_proposal(
            checkout,
            proposal.get("files") or [],
            existing_tests=existing,
            bundle_files=bundle_files,
            max_files=settings.repair_max_files,
            max_lines=settings.repair_max_changed_lines,
            new_test=proposal.get("new_test"),
        )
        attempt.validation_ok = result.ok
        attempt.validation_error = result.error
        if not result.ok:
            attempt.status = "validation_failed"
            attempt.feedback = result.error
            attempt.completed_at = _utcnow()
            db.commit()
            # Count toward budget; decide whether to retry
            feedback = f"Validation failed: {result.error}"
            return {**state, "feedback": feedback, "stop": False, "message": "retry"}
        attempt.patch_hash = result.patch_hash
        attempt.changed_files = result.changed_files
        attempt.new_test_path = result.new_test_path
        artifact = root / "artifacts" / f"attempt-{attempt.attempt_number}.patch"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(result.unified_diff, encoding="utf-8")
        attempt.patch_artifact_path = str(artifact)
        attempt.status = "validated"
        db.commit()

        # Apply for verification workspace
        all_files = list(proposal.get("files") or [])
        if proposal.get("new_test"):
            all_files.append(
                {"path": proposal["new_test"]["path"], "content": proposal["new_test"]["content"]}
            )
        apply_files(checkout, all_files)
        return {**state, "stop": False, "message": "verify"}
    finally:
        db.close()


def _node_verify(state: RepairGraphState) -> RepairGraphState:
    from app.db.session import SessionLocal as SL

    db = SL()
    try:
        run, inv, search, bundle, job = _load_ctx(db, state["repair_run_id"])
        if state.get("stop") or not state.get("attempt_id"):
            return {**state, "stop": True}
        if state.get("message") == "retry":
            return state

        attempt = db.get(PatchAttempt, uuid.UUID(state["attempt_id"]))
        assert attempt
        if not attempt.validation_ok:
            return {**state, "feedback": attempt.validation_error, "message": "retry"}

        run.status = RepairStatus.verifying
        run.stage = "verify"
        db.commit()

        settings = get_settings()
        root = Path(settings.data_dir) / "repairs" / str(run.id)
        checkout = root / "checkout"
        artifacts = root / "artifacts"
        checkout_rel = f"repairs/{run.id}/checkout"
        baseline = json.loads((root / "baseline.json").read_text(encoding="utf-8"))

        # Reproduction twice after patch
        after_ids = []
        after_outcomes = []
        for i in range(2):
            repro = _run_pytest(
                db,
                inv,
                sha=run.target_sha,
                run_type=RunType.repair_probe,
                selected_tests=[bundle.test_node_id],
                checkout=checkout,
                checkout_rel=checkout_rel,
                artifacts_dir=artifacts,
                repair_id=run.id,
                bundle=bundle,
            )
            after_ids.append(str(repro.id))
            tmap = _test_map(db, repro.id)
            hit = tmap.get(bundle.test_node_id)
            after_outcomes.append(hit.get("outcome") if hit else "missing")

        suite = _run_pytest(
            db,
            inv,
            sha=run.target_sha,
            run_type=RunType.repair_suite,
            selected_tests=None,
            checkout=checkout,
            checkout_rel=checkout_rel,
            artifacts_dir=artifacts,
            repair_id=run.id,
            bundle=None,
        )
        suite_after = _test_map(db, suite.id)
        suite_before = baseline.get("suite_tests") or {}

        before_passing = {
            k for k, v in suite_before.items() if v.get("outcome") == "passed"
        }
        after_passing = {
            k for k, v in suite_after.items() if v.get("outcome") == "passed"
        }
        after_failed = {
            k for k, v in suite_after.items() if v.get("outcome") in {"failed", "error"}
        }
        before_failed = {
            k for k, v in suite_before.items() if v.get("outcome") in {"failed", "error"}
        }
        new_failures = sorted(after_failed - before_failed)
        missing_tests = sorted(set(suite_before) - set(suite_after))
        unchanged_failures = sorted(before_failed & after_failed)

        repro_ok = after_outcomes == ["passed", "passed"]
        suite_ok = suite.status == ExecutionStatus.completed and not new_failures and not missing_tests

        outcome = RepairOutcome.verification_failed
        summary = ""
        if not repro_ok:
            summary = f"Reproduction did not pass twice after patch: {after_outcomes}"
            outcome = RepairOutcome.verification_failed
        elif not suite_ok:
            if suite.status != ExecutionStatus.completed:
                outcome = RepairOutcome.partial_verification
                summary = "Reproduction passed but broader suite could not complete."
            else:
                outcome = RepairOutcome.verification_failed
                summary = f"Regressions or missing tests: new={new_failures} missing={missing_tests}"
        else:
            outcome = RepairOutcome.verified_fix
            summary = "Reproduction passes consistently; baseline suite completed; no new failures or missing tests."

        # Optional new regression test check
        proposal = json.loads((root / f"proposal-{attempt.id}.json").read_text(encoding="utf-8"))
        new_test = proposal.get("new_test")
        new_pre = new_post = None
        if new_test and new_test.get("node_id") and outcome == RepairOutcome.verified_fix:
            # Pre: clean checkout without patch
            clean = root / "checkout-pre"
            if clean.exists():
                shutil.rmtree(clean)
            _prepare_checkout(db, inv, run.target_sha, clean)
            apply_files(clean, [{"path": new_test["path"], "content": new_test["content"]}])
            # run only new test on unpatched app — expect fail
            # Use separate checkout_rel
            pre_rel = f"repairs/{run.id}/checkout-pre"
            pre_run = _run_pytest(
                db,
                inv,
                sha=run.target_sha,
                run_type=RunType.repair_probe,
                selected_tests=[new_test["node_id"]],
                checkout=clean,
                checkout_rel=pre_rel,
                artifacts_dir=artifacts,
                repair_id=run.id,
                bundle=None,
            )
            new_pre = str(pre_run.id)
            post_run = _run_pytest(
                db,
                inv,
                sha=run.target_sha,
                run_type=RunType.repair_probe,
                selected_tests=[new_test["node_id"]],
                checkout=checkout,
                checkout_rel=checkout_rel,
                artifacts_dir=artifacts,
                repair_id=run.id,
                bundle=None,
            )
            new_post = str(post_run.id)
            pre_map = _test_map(db, pre_run.id)
            post_map = _test_map(db, post_run.id)
            pre_hit = pre_map.get(new_test["node_id"], {})
            post_hit = post_map.get(new_test["node_id"], {})
            if pre_hit.get("outcome") not in {"failed", "error"} or post_hit.get("outcome") != "passed":
                outcome = RepairOutcome.verification_failed
                summary = "New regression test did not fail before and pass after the patch."

        cmp = VerificationComparison(
            attempt_id=attempt.id,
            baseline_repro_ids=[baseline["repro_id"]],
            baseline_suite_id=baseline["suite_id"],
            patched_repro_ids=after_ids,
            patched_suite_id=str(suite.id),
            new_test_pre_id=new_pre,
            new_test_post_id=new_post,
            reproduction_before=list(_test_map(db, uuid.UUID(baseline["repro_id"])).values()),
            reproduction_after=[{"outcome": o} for o in after_outcomes],
            suite_before=list(suite_before.values()),
            suite_after=list(suite_after.values()),
            new_failures=new_failures,
            missing_tests=missing_tests,
            unchanged_failures=unchanged_failures,
            outcome=outcome,
            summary=summary,
        )
        db.add(cmp)
        attempt.status = "verified" if outcome == RepairOutcome.verified_fix else "verification_failed"
        attempt.feedback = summary
        attempt.completed_at = _utcnow()
        db.commit()

        if outcome == RepairOutcome.verified_fix:
            run.selected_attempt_id = attempt.id
            run.limitations = attempt.limitations
            _finish(db, run, job, status=RepairStatus.completed, outcome=outcome, message=None)
            return {**state, "stop": True, "outcome": outcome.value}

        return {
            **state,
            "feedback": summary,
            "message": "retry",
            "stop": False,
            "outcome": outcome.value,
        }
    finally:
        db.close()


def _node_decide(state: RepairGraphState) -> RepairGraphState:
    from app.db.session import SessionLocal as SL

    db = SL()
    try:
        run, inv, search, bundle, job = _load_ctx(db, state["repair_run_id"])
        if state.get("stop"):
            return state
        run.status = RepairStatus.deciding
        run.stage = "decide"
        db.commit()

        if _cancelled(db, run):
            _finish(db, run, job, status=RepairStatus.cancelled, outcome=None, message="Cancelled")
            return {**state, "stop": True}
        if _budget_exceeded(run):
            _finish(db, run, job, status=RepairStatus.timed_out, outcome=RepairOutcome.budget_exhausted)
            return {**state, "stop": True, "outcome": RepairOutcome.budget_exhausted.value}
        if run.attempt_count >= run.max_attempts:
            _finish(
                db,
                run,
                job,
                status=RepairStatus.completed,
                outcome=RepairOutcome.budget_exhausted
                if state.get("outcome") != RepairOutcome.verified_fix.value
                else RepairOutcome.verified_fix,
                message=state.get("feedback") or "Attempt budget exhausted.",
            )
            return {**state, "stop": True}
        # Retry
        return {**state, "stop": False, "message": "propose"}
    finally:
        db.close()


def _route_after_decide(state: RepairGraphState) -> str:
    if state.get("stop"):
        return "end"
    if state.get("message") == "propose":
        return "propose"
    return "end"


def _route_after_validate(state: RepairGraphState) -> str:
    if state.get("stop"):
        return "end"
    if state.get("message") == "retry":
        return "decide"
    return "verify"


def _route_after_verify(state: RepairGraphState) -> str:
    if state.get("stop"):
        return "end"
    return "decide"


def _build_graph():
    g = StateGraph(RepairGraphState)
    g.add_node("prepare", _node_prepare)
    g.add_node("baseline", _node_baseline)
    g.add_node("retrieve", _node_retrieve)
    g.add_node("propose", _node_propose)
    g.add_node("validate", _node_validate)
    g.add_node("verify", _node_verify)
    g.add_node("decide", _node_decide)

    g.set_entry_point("prepare")
    g.add_edge("prepare", "baseline")
    g.add_edge("baseline", "retrieve")
    g.add_edge("retrieve", "propose")
    g.add_edge("propose", "validate")
    g.add_conditional_edges("validate", _route_after_validate, {"verify": "verify", "decide": "decide", "end": END})
    g.add_conditional_edges("verify", _route_after_verify, {"decide": "decide", "end": END})
    g.add_conditional_edges("decide", _route_after_decide, {"propose": "propose", "end": END})
    return g


# Late import used by nodes
from app.db.session import SessionLocal  # noqa: E402
