from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound
from git import Repo
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.models.entities import (
    CodeChunk,
    Evidence,
    ExecutionArtifact,
    ExecutionRun,
    ExecutionStatus,
    Investigation,
    Job,
    JobStatus,
    Repository,
    RunType,
    TestOutcome,
    TestResult,
)
from app.services.activity import log_event

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _docker_client():
    settings = get_settings()
    if settings.docker_host:
        return docker.DockerClient(base_url=settings.docker_host)
    return docker.from_env()


@dataclass
class ContainerResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    cancelled: bool
    container_id: str | None
    image_digest: str | None
    error_category: str | None = None
    error_message: str | None = None
    junit_xml: str | None = None


def _make_writable_for_runner(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o777)
    except OSError:
        pass
    for root, dirs, files in os.walk(path):
        for name in dirs:
            try:
                os.chmod(os.path.join(root, name), 0o777)
            except OSError:
                pass
        for name in files:
            try:
                os.chmod(os.path.join(root, name), 0o666)
            except OSError:
                pass


def _append_output(path: Path, text: str, limit: int) -> tuple[int, bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8", errors="replace")
    truncated = False
    existing = path.stat().st_size if path.exists() else 0
    remaining = max(0, limit - existing)
    if len(data) > remaining:
        data = data[:remaining]
        truncated = True
    with path.open("ab") as f:
        f.write(data)
    return existing + len(data), truncated or existing + len(data) >= limit


def _prepare_checkout(
    db: Session,
    investigation: Investigation,
    pinned_sha: str,
    dest: Path,
) -> None:
    repo = investigation.repository
    settings = get_settings()
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    source = Path(repo.local_path) if repo.local_path else None
    if source and source.exists() and (source / ".git").exists():
        # Clone the indexed local checkout and pin to the investigation SHA.
        cloned = Repo.clone_from(str(source), str(dest))
        try:
            cloned.git.checkout(pinned_sha)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Unable to checkout pinned commit {pinned_sha[:12]}: {exc}"
            ) from exc
        return

    if repo.is_demo:
        # Prefer the indexed local checkout (has full history for demo-repo-v2).
        source = Path(repo.local_path) if repo.local_path else None
        demo_src = Path(settings.demo_repo_path).resolve()
        origin = source if source and source.exists() and (source / ".git").exists() else demo_src
        if not origin.exists():
            raise RuntimeError(f"Demo repository path not found: {origin}")
        cloned = Repo.clone_from(str(origin), str(dest))
        try:
            cloned.git.checkout(pinned_sha)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Unable to checkout pinned commit {pinned_sha[:12]}: {exc}"
            ) from exc
        return

    # Fallback: clone from GitHub and checkout the pin
    cloned = Repo.clone_from(repo.github_url + ".git", str(dest))
    try:
        cloned.git.fetch("origin", pinned_sha)
        cloned.git.checkout(pinned_sha)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Unable to checkout pinned commit {pinned_sha[:12]}: {exc}"
        ) from exc


def _evidence_test_paths(db: Session, investigation_id: uuid.UUID) -> set[str]:
    rows = (
        db.query(CodeChunk)
        .join(Evidence, Evidence.chunk_id == CodeChunk.id)
        .options(joinedload(CodeChunk.source_file))
        .filter(Evidence.investigation_id == investigation_id)
        .all()
    )
    paths: set[str] = set()
    for chunk in rows:
        path = chunk.source_file.path.replace("\\", "/")
        if path.endswith(".py") and (
            "/tests/" in f"/{path}"
            or path.startswith("tests/")
            or path.startswith("test_")
            or path.endswith("_test.py")
        ):
            paths.add(path)
        # also prefer any path mentioned in evidence that looks like a test module
        if "test" in path and path.endswith(".py"):
            paths.add(path)
    return paths


def _run_container(
    db: Session,
    run: ExecutionRun,
    *,
    checkout_rel: str,
    command: list[str],
    output_path: Path,
) -> ContainerResult:
    settings = get_settings()
    limit = settings.max_output_bytes
    client = _docker_client()
    image = settings.runner_image
    digest = None
    try:
        img = client.images.get(image)
        digest = img.id
    except ImageNotFound:
        return ContainerResult(
            exit_code=-1,
            stdout="",
            stderr="",
            timed_out=False,
            cancelled=False,
            container_id=None,
            image_digest=None,
            error_category="environment_setup_unsupported",
            error_message=(
                f"Runner image '{image}' is not available. "
                "Build the bugtime-pytest-runner image and retry."
            ),
        )

    container = None
    timed_out = False
    cancelled = False
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    try:
        container = client.containers.run(
            image=image,
            command=command,
            user="1000:1000",
            network_mode="none",
            mem_limit=settings.execution_memory_limit,
            nano_cpus=settings.execution_nano_cpus,
            pids_limit=settings.execution_pids_limit,
            read_only=True,
            volumes={
                settings.data_volume_name: {
                    "bind": "/data",
                    "mode": "rw",
                }
            },
            working_dir=f"/data/{checkout_rel}",
            environment={
                "PYTHONPATH": f"/data/{checkout_rel}",
                "PYTHONDONTWRITEBYTECODE": "1",
                "HOME": "/tmp",
            },
            tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
            detach=True,
            stdout=True,
            stderr=True,
        )
        run.container_id = container.id[:12]
        run.runner_image = image
        run.runner_image_digest = digest
        db.commit()

        deadline = time.time() + settings.execution_timeout_seconds
        while True:
            db.refresh(run)
            if run.cancel_requested:
                cancelled = True
                try:
                    container.kill()
                except APIError:
                    pass
                break
            container.reload()
            if container.status in {"exited", "dead", "removing"}:
                break
            if time.time() > deadline:
                timed_out = True
                try:
                    container.kill()
                except APIError:
                    pass
                break
            time.sleep(0.4)

        # Collect logs + optional junit from /tmp before removing container
        try:
            raw_out = container.logs(stdout=True, stderr=False)
            raw_err = container.logs(stdout=False, stderr=True)
            stdout = raw_out.decode("utf-8", errors="replace") if isinstance(raw_out, (bytes, bytearray)) else str(raw_out)
            stderr = raw_err.decode("utf-8", errors="replace") if isinstance(raw_err, (bytes, bytearray)) else str(raw_err)
        except Exception:  # noqa: BLE001
            stdout, stderr = "", ""

        junit_xml = None
        try:
            bits, _stat = container.get_archive("/tmp/junit.xml")
            import io
            import tarfile

            buf = io.BytesIO(b"".join(bits))
            with tarfile.open(fileobj=buf) as tar:
                member = tar.next()
                if member is not None:
                    f = tar.extractfile(member)
                    if f is not None:
                        junit_xml = f.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            junit_xml = None

        stdout_chunks.append(stdout)
        stderr_chunks.append(stderr)
        _append_output(output_path, stdout + (("\n" + stderr) if stderr else ""), limit)

        container.reload()
        exit_code = container.attrs.get("State", {}).get("ExitCode", -1)
        if cancelled:
            return ContainerResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                timed_out=False,
                cancelled=True,
                container_id=run.container_id,
                image_digest=digest,
                error_category="cancelled",
                error_message="Execution cancelled by user",
                junit_xml=junit_xml,
            )
        if timed_out:
            return ContainerResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
                cancelled=False,
                container_id=run.container_id,
                image_digest=digest,
                error_category="timeout",
                error_message=f"Execution exceeded {settings.execution_timeout_seconds}s timeout",
                junit_xml=junit_xml,
            )
        return ContainerResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=False,
            cancelled=False,
            container_id=run.container_id,
            image_digest=digest,
            junit_xml=junit_xml,
        )
    except DockerException as exc:
        return ContainerResult(
            exit_code=-1,
            stdout="",
            stderr=str(exc),
            timed_out=False,
            cancelled=False,
            container_id=None,
            image_digest=digest,
            error_category="internal_error",
            error_message=str(exc),
        )
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except (APIError, NotFound):
                pass


def _parse_junit(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return []
    root = tree.getroot()
    suites = root.findall("testsuite")
    if root.tag == "testsuite":
        suites = [root]
    results: list[dict] = []
    for suite in suites:
        for case in suite.findall("testcase"):
            classname = case.attrib.get("classname", "")
            name = case.attrib.get("name", "")
            # pytest junit classname is dotted module path
            file_hint = classname.replace(".", "/") + ".py" if classname else ""
            nodeid = f"{file_hint}::{name}" if file_hint else name
            # Prefer file attribute when present (pytest 6+)
            file_attr = case.attrib.get("file")
            if file_attr:
                nodeid = f"{file_attr}::{name}"
            duration = float(case.attrib.get("time", "0") or 0) * 1000
            failure = case.find("failure")
            error = case.find("error")
            skipped = case.find("skipped")
            if failure is not None:
                outcome = "failed"
                msg = failure.attrib.get("message") or (failure.text or "")
                tb = failure.text or ""
            elif error is not None:
                outcome = "error"
                msg = error.attrib.get("message") or (error.text or "")
                tb = error.text or ""
            elif skipped is not None:
                outcome = "skipped"
                msg = skipped.attrib.get("message") or ""
                tb = ""
            else:
                outcome = "passed"
                msg = ""
                tb = ""
            results.append(
                {
                    "test_identifier": nodeid,
                    "outcome": outcome,
                    "duration_ms": int(duration),
                    "failure_message": msg[:4000] if msg else None,
                    "traceback": tb[:20000] if tb else None,
                }
            )
    return results


def _parse_collect_only(stdout: str) -> list[str]:
    ids: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("=") or line.startswith("["):
            continue
        # pytest --collect-only -q prints node ids
        if "::" in line and not line.startswith("ERROR"):
            # strip trailing status markers
            node = line.split()[0]
            if "::" in node:
                ids.append(node)
    return ids


def _parse_pytest_stdout(stdout: str, selected: list[str]) -> list[dict]:
    """Fallback when JUnit XML is unavailable."""
    failed: set[str] = set()
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("FAILED ") and "::" in line:
            node = line[len("FAILED ") :].split(" - ", 1)[0].strip()
            failed.add(node)
        if line.startswith("ERROR ") and "::" in line:
            node = line[len("ERROR ") :].split(" - ", 1)[0].strip()
            failed.add(node)
    results: list[dict] = []
    # Prefer selected identifiers; also include any FAILED nodes not selected
    seen: set[str] = set()
    for node in list(selected) + [n for n in failed if n not in selected]:
        if node in seen:
            continue
        seen.add(node)
        if node in failed:
            outcome = "failed"
            msg = None
            for line in stdout.splitlines():
                if line.strip().startswith("E ") and "assert" in line:
                    msg = line.strip()[2:].strip()
                    break
            results.append(
                {
                    "test_identifier": node,
                    "outcome": outcome,
                    "duration_ms": None,
                    "failure_message": msg,
                    "traceback": None,
                }
            )
        else:
            results.append(
                {
                    "test_identifier": node,
                    "outcome": "passed",
                    "duration_ms": None,
                    "failure_message": None,
                    "traceback": None,
                }
            )
    return results


def _categorize_pytest_exit(exit_code: int, collected: int, stdout: str, stderr: str) -> str | None:
    # pytest exit codes: 0 ok, 1 failed tests, 2 interrupted, 3 internal, 4 usage, 5 no tests
    combined = (stdout + "\n" + stderr).lower()
    if "modulenotfounderror" in combined or "no module named" in combined:
        return "environment_setup_unsupported"
    if "error during collection" in combined or "collection error" in combined:
        return "collection_error"
    if exit_code == 0:
        return None
    if exit_code == 1:
        return "assertion_failure"
    if exit_code == 2:
        return "interrupted"
    if exit_code == 3:
        return "internal_error"
    if exit_code == 4:
        return "invalid_command"
    if exit_code == 5:
        return "no_tests_collected"
    return "internal_error"


def run_execution_job(db: Session, job_id: uuid.UUID) -> None:
    job = db.get(Job, job_id)
    if not job or not job.execution_id:
        return
    job.status = JobStatus.running
    job.attempt += 1
    job.started_at = _utcnow()
    db.commit()

    run = db.get(ExecutionRun, job.execution_id)
    if not run:
        job.status = JobStatus.failed
        job.error_message = "Execution run missing"
        job.completed_at = _utcnow()
        db.commit()
        return

    investigation = (
        db.query(Investigation)
        .options(
            joinedload(Investigation.repository).joinedload(Repository.indexes),
            joinedload(Investigation.evidence_items),
        )
        .filter(Investigation.id == run.investigation_id)
        .first()
    )
    if not investigation:
        run.status = ExecutionStatus.failed
        run.error_category = "internal_error"
        run.error_message = "Investigation missing"
        job.status = JobStatus.failed
        job.completed_at = _utcnow()
        db.commit()
        return

    settings = get_settings()
    started = time.time()
    run.status = ExecutionStatus.preparing
    run.stage = "preparing"
    run.started_at = _utcnow()
    db.commit()
    log_event(
        db,
        stage="preparing",
        message=f"Preparing {run.run_type.value} at {run.pinned_sha[:12]}",
        investigation_id=investigation.id,
        repository_id=investigation.repository_id,
    )

    exec_root = Path(settings.data_dir) / "executions" / str(run.id)
    checkout_rel = f"executions/{run.id}/checkout"
    checkout = Path(settings.data_dir) / checkout_rel
    artifacts_dir = exec_root / "artifacts"
    _make_writable_for_runner(exec_root)
    _make_writable_for_runner(artifacts_dir)
    output_path = artifacts_dir / "output.log"
    junit_path = artifacts_dir / "junit.xml"

    try:
        _prepare_checkout(db, investigation, run.pinned_sha, checkout)
        _make_writable_for_runner(checkout)
    except Exception as exc:  # noqa: BLE001
        run.status = ExecutionStatus.failed
        run.stage = "finished"
        run.error_category = "internal_error"
        run.error_message = f"Checkout failed: {exc}"
        run.completed_at = _utcnow()
        run.duration_ms = int((time.time() - started) * 1000)
        job.status = JobStatus.failed
        job.error_message = run.error_message
        job.completed_at = _utcnow()
        db.commit()
        return

    evidence_paths = _evidence_test_paths(db, investigation.id)

    # Writable volume path (chmod'd for non-root runner) + /tmp fallback.
    container_junit = f"/data/executions/{run.id}/artifacts/junit.xml"
    if run.run_type == RunType.collection:
        run.stage = "collecting"
        run.status = ExecutionStatus.running
        db.commit()
        command = [
            "--collect-only",
            "-q",
            f"--junitxml={container_junit}",
        ]
        result = _run_container(
            db,
            run,
            checkout_rel=checkout_rel,
            command=command,
            output_path=output_path,
        )
    else:
        run.stage = "running"
        run.status = ExecutionStatus.running
        db.commit()
        command = [
            "-q",
            f"--junitxml={container_junit}",
            "--tb=short",
            *run.selected_tests,
        ]
        result = _run_container(
            db,
            run,
            checkout_rel=checkout_rel,
            command=command,
            output_path=output_path,
        )

    if result.junit_xml and not junit_path.exists():
        junit_path.write_text(result.junit_xml, encoding="utf-8")

    # Persist artifacts
    out_size = output_path.stat().st_size if output_path.exists() else 0
    db.add(
        ExecutionArtifact(
            execution_id=run.id,
            artifact_type="output",
            storage_path=str(output_path),
            byte_size=out_size,
            truncated=out_size >= settings.max_output_bytes,
        )
    )
    if junit_path.exists():
        db.add(
            ExecutionArtifact(
                execution_id=run.id,
                artifact_type="junit",
                storage_path=str(junit_path),
                byte_size=junit_path.stat().st_size,
                truncated=False,
            )
        )

    if result.error_category == "environment_setup_unsupported" and result.exit_code == -1 and not result.stdout:
        run.status = ExecutionStatus.failed
        run.stage = "finished"
        run.error_category = result.error_category
        run.error_message = result.error_message
        run.exit_code = result.exit_code
        run.duration_ms = int((time.time() - started) * 1000)
        run.completed_at = _utcnow()
        job.status = JobStatus.failed
        job.error_message = result.error_message
        job.completed_at = _utcnow()
        db.commit()
        return

    if result.cancelled:
        run.status = ExecutionStatus.cancelled
        run.stage = "finished"
        run.error_category = "cancelled"
        run.error_message = result.error_message
    elif result.timed_out:
        run.status = ExecutionStatus.timed_out
        run.stage = "finished"
        run.error_category = "timeout"
        run.error_message = result.error_message
    else:
        # Parse results
        if run.run_type == RunType.collection:
            node_ids = _parse_collect_only(result.stdout)
            if not node_ids:
                # fallback from junit
                for item in _parse_junit(junit_path):
                    node_ids.append(item["test_identifier"])
            # Clear prior collected results for this investigation's collection? keep per-run
            for node in node_ids:
                file_part = node.split("::", 1)[0]
                db.add(
                    TestResult(
                        execution_id=run.id,
                        test_identifier=node,
                        outcome=TestOutcome.collected,
                        in_evidence=file_part in evidence_paths,
                    )
                )
            category = _categorize_pytest_exit(
                result.exit_code, len(node_ids), result.stdout, result.stderr
            )
            if result.exit_code in (0, 1, 5) or node_ids:
                # collect-only often exits 0; 5 means none
                if not node_ids and result.exit_code != 0:
                    run.status = ExecutionStatus.failed
                    run.error_category = category or "no_tests_collected"
                    run.error_message = result.stderr or result.stdout or "No tests collected"
                else:
                    run.status = ExecutionStatus.completed
                    if category == "collection_error":
                        run.error_category = category
                        run.error_message = "Collection completed with errors"
            else:
                run.status = ExecutionStatus.failed
                run.error_category = category or "collection_error"
                run.error_message = result.stderr or result.stdout or "Collection failed"
        else:
            parsed = _parse_junit(junit_path)
            if not parsed:
                parsed = _parse_pytest_stdout(result.stdout, list(run.selected_tests or []))
            for item in parsed:
                db.add(
                    TestResult(
                        execution_id=run.id,
                        test_identifier=item["test_identifier"],
                        outcome=TestOutcome(item["outcome"]),
                        duration_ms=item["duration_ms"],
                        failure_message=item["failure_message"],
                        traceback=item["traceback"],
                        in_evidence=item["test_identifier"].split("::", 1)[0] in evidence_paths,
                    )
                )
            category = _categorize_pytest_exit(
                result.exit_code, len(parsed), result.stdout, result.stderr
            )
            # Completed run can contain failing tests
            if result.exit_code in (0, 1):
                run.status = ExecutionStatus.completed
                if result.exit_code == 1:
                    run.error_category = "assertion_failure"
            elif category == "environment_setup_unsupported":
                run.status = ExecutionStatus.failed
                run.error_category = category
                run.error_message = (
                    "Environment setup unsupported: missing dependencies or import errors. "
                    "Arbitrary dependency installation is not available in P1."
                )
            else:
                run.status = ExecutionStatus.failed
                run.error_category = category or "internal_error"
                run.error_message = result.stderr or result.stdout or "Execution failed"

    run.stage = "finished"
    run.exit_code = result.exit_code
    run.runner_image_digest = result.image_digest
    run.duration_ms = int((time.time() - started) * 1000)
    run.completed_at = _utcnow()
    job.status = JobStatus.completed if run.status == ExecutionStatus.completed else JobStatus.failed
    if run.status in {ExecutionStatus.cancelled, ExecutionStatus.timed_out}:
        job.status = JobStatus.failed
    job.error_message = run.error_message
    job.completed_at = _utcnow()
    db.commit()

    log_event(
        db,
        stage=run.stage,
        message=f"{run.run_type.value} {run.status.value} (exit {run.exit_code})",
        level="error" if run.status == ExecutionStatus.failed else "info",
        investigation_id=investigation.id,
        repository_id=investigation.repository_id,
    )

    # Cleanup checkout (keep artifacts)
    try:
        if checkout.exists():
            shutil.rmtree(checkout)
    except OSError:
        logger.warning("Failed to remove checkout %s", checkout)
