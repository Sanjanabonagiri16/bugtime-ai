from __future__ import annotations

import hashlib
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from git import Repo
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import (
    IndexStatus,
    Job,
    JobStatus,
    Repository,
    RepositoryIndex,
    RepositoryStatus,
    SourceFile,
    CodeChunk,
)
from app.services.activity import log_event
from app.services.chunking import (
    chunk_file,
    language_for_path,
    should_skip_path,
    source_type_for_path,
)
from app.services.embeddings import embed_texts
from app.services import qdrant_store

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _set_index_status(db: Session, index: RepositoryIndex, status: IndexStatus) -> None:
    index.status = status
    db.commit()


def _verify_public_repo(owner: str, repo: str) -> None:
    url = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            resp = client.get(url, headers={"Accept": "application/vnd.github+json"})
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Unable to reach GitHub: {exc}") from exc
    if resp.status_code == 404:
        raise RuntimeError(
            "Repository not found or private. P0 supports public GitHub repositories only."
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"GitHub API error ({resp.status_code}): {resp.text[:200]}")
    data = resp.json()
    if data.get("private"):
        raise RuntimeError("Private repositories are not supported in P0.")


def _prepare_checkout(
    db: Session,
    repository: Repository,
    index: RepositoryIndex,
    *,
    is_demo: bool,
) -> Path:
    settings = get_settings()
    repos_root = Path(settings.repos_dir)
    repos_root.mkdir(parents=True, exist_ok=True)
    dest = repos_root / str(repository.id)

    log_event(
        db,
        stage="fetching",
        message="Fetching repository contents",
        repository_id=repository.id,
    )
    _set_index_status(db, index, IndexStatus.fetching)

    if is_demo:
        demo_src = Path(settings.demo_repo_path).resolve()
        if not demo_src.exists():
            raise RuntimeError(f"Demo repository path not found: {demo_src}")
        if not (demo_src / ".git").exists():
            raise RuntimeError(f"Demo repository is missing git history: {demo_src}")
        if dest.exists():
            shutil.rmtree(dest)
        # Preserve full linear history for P2 bisect (local clone).
        repo = Repo.clone_from(str(demo_src), str(dest))
        if "main" in [h.name for h in repo.heads]:
            repo.heads.main.checkout()
            repository.branch = "main"
        commit_sha = repo.head.commit.hexsha
    else:
        _verify_public_repo(repository.owner, repository.name)
        if dest.exists():
            shutil.rmtree(dest)
        # Full history required for P2 bisect / P5 verified-base identity (not depth=1).
        repo = Repo.clone_from(repository.github_url + ".git", dest)
        remote_refs = [ref.name for ref in repo.remotes.origin.refs]
        requested = repository.branch
        if requested and any(ref.endswith(f"/{requested}") or ref == requested for ref in remote_refs):
            repo.git.checkout(requested)
        else:
            requested = repo.active_branch.name
            repository.branch = requested
            repo.git.checkout(requested)
        commit_sha = repo.head.commit.hexsha

    index.commit_sha = commit_sha
    repository.local_path = str(dest)
    db.commit()
    log_event(
        db,
        stage="fetching",
        message=f"Resolved commit {commit_sha[:12]} on branch {repository.branch}",
        repository_id=repository.id,
    )
    return dest


def _iter_candidate_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__", ".venv", "venv", "node_modules"}]
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            files.append(path)
    return files


def run_indexing_job(db: Session, job_id: uuid.UUID) -> None:
    job = db.get(Job, job_id)
    if not job or not job.repository_id:
        return
    job.status = JobStatus.running
    job.attempt += 1
    job.started_at = _utcnow()
    db.commit()

    repository = db.get(Repository, job.repository_id)
    if not repository:
        job.status = JobStatus.failed
        job.error_message = "Repository missing"
        job.completed_at = _utcnow()
        db.commit()
        return

    settings = get_settings()
    # Latest queued index for this repository
    index = (
        db.query(RepositoryIndex)
        .filter(RepositoryIndex.repository_id == repository.id)
        .order_by(RepositoryIndex.created_at.desc())
        .first()
    )
    if not index:
        job.status = JobStatus.failed
        job.error_message = "Index record missing"
        job.completed_at = _utcnow()
        db.commit()
        return

    previous_active = repository.active_index_id
    repository.status = RepositoryStatus.indexing
    index.started_at = _utcnow()
    index.error_message = None
    db.commit()

    try:
        root = _prepare_checkout(db, repository, index, is_demo=repository.is_demo)

        _set_index_status(db, index, IndexStatus.scanning)
        log_event(
            db,
            stage="scanning",
            message="Scanning eligible source files",
            repository_id=repository.id,
        )

        candidates = _iter_candidate_files(root)
        index.total_files_estimate = len(candidates)
        db.commit()

        eligible: list[tuple[Path, str]] = []
        skipped: list[tuple[str, str]] = []
        total_bytes = 0

        for path in candidates:
            rel = str(path.relative_to(root)).replace("\\", "/")
            reason = should_skip_path(rel)
            if reason:
                skipped.append((rel, reason))
                continue
            try:
                size = path.stat().st_size
            except OSError:
                skipped.append((rel, "unreadable"))
                continue
            if size > settings.max_file_bytes:
                skipped.append((rel, f"file_exceeds_{settings.max_file_bytes}_bytes"))
                continue
            if total_bytes + size > settings.max_total_index_bytes:
                raise RuntimeError(
                    f"Import exceeds the {settings.max_total_index_bytes} byte total text limit. "
                    "Narrow the repository or raise limits via environment configuration."
                )
            if len(eligible) >= settings.max_index_files:
                raise RuntimeError(
                    f"Import exceeds the {settings.max_index_files} file limit. "
                    "Narrow the repository or raise MAX_INDEX_FILES."
                )
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                skipped.append((rel, "not_utf8_text"))
                continue
            except OSError:
                skipped.append((rel, "unreadable"))
                continue
            eligible.append((path, text))
            total_bytes += size

        index.skipped_count = len(skipped)
        db.commit()

        # Persist skipped rows
        for rel, reason in skipped:
            db.add(
                SourceFile(
                    index_id=index.id,
                    path=rel,
                    language=language_for_path(rel),
                    content_hash=_sha256(reason),
                    content=None,
                    line_count=0,
                    indexing_status="skipped",
                    skip_reason=reason,
                    source_type=source_type_for_path(rel),
                )
            )
        db.commit()

        _set_index_status(db, index, IndexStatus.chunking)
        log_event(
            db,
            stage="chunking",
            message=f"Chunking {len(eligible)} files",
            repository_id=repository.id,
        )

        chunk_rows: list[tuple[CodeChunk, str]] = []
        for i, (path, text) in enumerate(eligible, start=1):
            rel = str(path.relative_to(root)).replace("\\", "/")
            sf = SourceFile(
                index_id=index.id,
                path=rel,
                language=language_for_path(rel),
                content_hash=_sha256(text),
                content=text,
                line_count=text.count("\n") + (1 if text and not text.endswith("\n") else 0 if not text else 1),
                indexing_status="indexed",
                skip_reason=None,
                source_type=source_type_for_path(rel),
            )
            if text and sf.line_count == 0:
                sf.line_count = 1
            db.add(sf)
            db.flush()
            drafts = chunk_file(rel, text)
            for draft in drafts:
                chunk = CodeChunk(
                    source_file_id=sf.id,
                    index_id=index.id,
                    start_line=draft.start_line,
                    end_line=draft.end_line,
                    symbol_name=draft.symbol_name,
                    content=draft.content,
                )
                db.add(chunk)
                db.flush()
                chunk_rows.append((chunk, rel))
            index.processed_files = i
            if i % 25 == 0:
                db.commit()

        index.file_count = len(eligible)
        index.chunk_count = len(chunk_rows)
        db.commit()

        _set_index_status(db, index, IndexStatus.embedding)
        log_event(
            db,
            stage="embedding",
            message=f"Embedding {len(chunk_rows)} chunks with {settings.embedding_model}",
            repository_id=repository.id,
        )

        batch_size = 64
        points: list[dict] = []
        for start in range(0, len(chunk_rows), batch_size):
            batch = chunk_rows[start : start + batch_size]
            vectors = embed_texts([c.content for c, _ in batch])
            for (chunk, rel), vector in zip(batch, vectors, strict=True):
                point_id = str(uuid.uuid4())
                chunk.vector_point_id = point_id
                points.append(
                    {
                        "id": point_id,
                        "vector": vector,
                        "chunk_id": str(chunk.id),
                        "file_path": rel,
                    }
                )
            db.commit()

        _set_index_status(db, index, IndexStatus.saving)
        log_event(
            db,
            stage="saving",
            message="Saving vector index",
            repository_id=repository.id,
        )
        qdrant_store.upsert_chunks(
            repository_id=repository.id,
            index_id=index.id,
            points=points,
        )

        index.status = IndexStatus.ready
        index.completed_at = _utcnow()
        repository.status = RepositoryStatus.ready
        repository.active_index_id = index.id
        repository.updated_at = _utcnow()
        job.status = JobStatus.completed
        job.completed_at = _utcnow()
        db.commit()
        log_event(
            db,
            stage="ready",
            message=f"Index ready: {index.file_count} files, {index.chunk_count} chunks",
            repository_id=repository.id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Indexing failed for repository %s", repository.id)
        index.status = IndexStatus.failed
        index.error_message = str(exc)
        index.completed_at = _utcnow()
        # Keep previous ready index usable
        if previous_active:
            repository.active_index_id = previous_active
            prev = db.get(RepositoryIndex, previous_active)
            if prev and prev.status == IndexStatus.ready:
                repository.status = RepositoryStatus.ready
            else:
                repository.status = RepositoryStatus.failed
        else:
            repository.status = RepositoryStatus.failed
        job.status = JobStatus.failed
        job.error_message = str(exc)
        job.completed_at = _utcnow()
        db.commit()
        log_event(
            db,
            stage="failed",
            message=str(exc),
            level="error",
            repository_id=repository.id,
        )


def run_cleanup_job(db: Session, job_id: uuid.UUID) -> None:
    job = db.get(Job, job_id)
    if not job or not job.repository_id:
        return
    job.status = JobStatus.running
    job.attempt += 1
    job.started_at = _utcnow()
    db.commit()

    repository_id = job.repository_id
    repository = db.get(Repository, repository_id)
    failures: list[str] = []

    try:
        qdrant_store.delete_repository_points(repository_id)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"qdrant: {exc}")

    local_path = repository.local_path if repository else None
    if local_path and Path(local_path).exists():
        try:
            shutil.rmtree(local_path)
        except OSError as exc:
            failures.append(f"filesystem: {exc}")

    if repository:
        db.delete(repository)
        db.commit()

    if failures:
        job.status = JobStatus.failed
        job.error_message = "; ".join(failures)
        log_event(
            db,
            stage="cleanup",
            message=f"Cleanup finished with errors: {job.error_message}",
            level="error",
            repository_id=None,
        )
    else:
        job.status = JobStatus.completed
        log_event(
            db,
            stage="cleanup",
            message=f"Repository {repository_id} cleaned up",
            repository_id=None,
        )
    job.completed_at = _utcnow()
    db.commit()
