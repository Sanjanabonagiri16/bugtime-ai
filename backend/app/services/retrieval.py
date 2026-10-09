from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.models.entities import (
    CodeChunk,
    Evidence,
    Investigation,
    InvestigationStatus,
    Job,
    JobStatus,
    MatchReason,
    SourceFile,
)
from app.services.activity import log_event
from app.services.embeddings import embed_query
from app.services import qdrant_store
from app.services.reranker import rerank_indices


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


IDENTIFIER_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\b")
FILENAME_RE = re.compile(
    r"\b([\w./-]+\.(?:py|md|txt|toml|ya?ml|json))\b",
    re.IGNORECASE,
)


def build_query_text(inv: Investigation) -> str:
    parts = [inv.title, inv.description]
    if inv.expected_behavior:
        parts.append(inv.expected_behavior)
    if inv.actual_behavior:
        parts.append(inv.actual_behavior)
    if inv.logs:
        parts.append(inv.logs)
    return "\n".join(parts)


def extract_terms(text: str) -> tuple[set[str], set[str]]:
    filenames = {m.group(1).lower() for m in FILENAME_RE.finditer(text)}
    identifiers = {m.group(1) for m in IDENTIFIER_RE.finditer(text)}
    stop = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "when",
        "then",
        "error",
        "failed",
        "failure",
        "exception",
        "traceback",
        "expected",
        "actual",
        "should",
        "would",
        "could",
        "login",
        "user",
        "password",
    }
    identifiers = {i for i in identifiers if i.lower() not in stop and len(i) >= 3}
    return filenames, identifiers


def reciprocal_rank_fusion(
    ranked_lists: list[list[uuid.UUID]],
    k: int = 60,
) -> list[tuple[uuid.UUID, float]]:
    scores: dict[uuid.UUID, float] = defaultdict(float)
    for ranked in ranked_lists:
        for rank, item_id in enumerate(ranked, start=1):
            scores[item_id] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def run_retrieval_job(db: Session, job_id: uuid.UUID) -> None:
    job = db.get(Job, job_id)
    if not job or not job.investigation_id:
        return
    job.status = JobStatus.running
    job.attempt += 1
    job.started_at = _utcnow()
    db.commit()

    inv = db.get(Investigation, job.investigation_id)
    if not inv:
        job.status = JobStatus.failed
        job.error_message = "Investigation missing"
        job.completed_at = _utcnow()
        db.commit()
        return

    inv.status = InvestigationStatus.retrieving
    inv.error_message = None
    db.commit()
    log_event(
        db,
        stage="retrieving",
        message="Building retrieval query from bug report",
        investigation_id=inv.id,
        repository_id=inv.repository_id,
    )

    try:
        # Clear previous evidence on retry
        db.query(Evidence).filter(Evidence.investigation_id == inv.id).delete()
        db.commit()

        query_text = build_query_text(inv)
        filenames, identifiers = extract_terms(query_text)

        chunks = (
            db.query(CodeChunk)
            .options(joinedload(CodeChunk.source_file))
            .filter(CodeChunk.index_id == inv.index_id)
            .all()
        )
        if not chunks:
            inv.status = InvestigationStatus.evidence_ready
            inv.completed_at = _utcnow()
            job.status = JobStatus.completed
            job.completed_at = _utcnow()
            db.commit()
            log_event(
                db,
                stage="evidence_ready",
                message="No relevant evidence found (index has no chunks)",
                investigation_id=inv.id,
                repository_id=inv.repository_id,
            )
            return

        exact_ranked: list[uuid.UUID] = []
        exact_hits: set[uuid.UUID] = set()
        for chunk in chunks:
            path = chunk.source_file.path.lower()
            content_l = chunk.content.lower()
            hit = False
            for fn in filenames:
                if fn in path or fn in content_l:
                    hit = True
                    break
            if not hit:
                for ident in identifiers:
                    if ident in chunk.content or (chunk.symbol_name and ident in chunk.symbol_name):
                        hit = True
                        break
            if hit:
                exact_ranked.append(chunk.id)
                exact_hits.add(chunk.id)

        log_event(
            db,
            stage="retrieving",
            message=f"Exact match candidates: {len(exact_ranked)}",
            investigation_id=inv.id,
            repository_id=inv.repository_id,
        )

        vector = embed_query(query_text)
        semantic_hits = qdrant_store.search(
            repository_id=inv.repository_id,
            index_id=inv.index_id,
            vector=vector,
            limit=30,
        )
        semantic_ranked: list[uuid.UUID] = []
        semantic_scores: dict[uuid.UUID, float] = {}
        for hit in semantic_hits:
            try:
                cid = uuid.UUID(str(hit["chunk_id"]))
            except (ValueError, TypeError):
                continue
            semantic_ranked.append(cid)
            semantic_scores[cid] = hit["score"]

        log_event(
            db,
            stage="retrieving",
            message=f"Semantic candidates: {len(semantic_ranked)}",
            investigation_id=inv.id,
            repository_id=inv.repository_id,
        )

        settings = get_settings()
        fused = reciprocal_rank_fusion([exact_ranked, semantic_ranked])
        # Build frozen candidate pool (pre-rerank), then apply optional CE.
        pool: list[tuple[uuid.UUID, float]] = []
        per_file_pool: dict[str, int] = defaultdict(int)
        chunk_by_id = {c.id: c for c in chunks}
        cand_limit = max(1, int(settings.reranker_candidate_limit))
        per_file_cap = max(1, int(settings.evidence_per_file_limit))
        evidence_cap = max(1, int(settings.evidence_limit))

        for chunk_id, score in fused:
            chunk = chunk_by_id.get(chunk_id)
            if not chunk:
                continue
            path = chunk.source_file.path
            if per_file_pool[path] >= per_file_cap:
                continue
            pool.append((chunk_id, score))
            per_file_pool[path] += 1
            if len(pool) >= cand_limit:
                break

        rerank_mode = settings.reranker_mode
        rerank_path = settings.reranker_model_path or None
        rerank_hash = None
        rerank_fallback = False
        ordered_pool = list(pool)
        if pool and (rerank_mode or "disabled").lower() != "disabled":
            items = [
                {
                    "chunk_id": cid,
                    "snippet": (chunk_by_id[cid].content if cid in chunk_by_id else "")[:2000],
                    "hybrid_score": score,
                }
                for cid, score in pool
            ]
            order, meta = rerank_indices(query_text, items, mode=rerank_mode)
            if meta.fallback or meta.mode == "disabled":
                rerank_fallback = True
                rerank_mode = "disabled"
                log_event(
                    db,
                    stage="retrieving",
                    message=f"Reranker fallback: {meta.error or 'disabled'}",
                    investigation_id=inv.id,
                    repository_id=inv.repository_id,
                )
            else:
                score_by = {cid: sc for cid, sc in pool}
                ordered_pool = [
                    (items[i]["chunk_id"], score_by[items[i]["chunk_id"]]) for i in order
                ]
                rerank_mode = meta.mode
                rerank_path = meta.model_path
                rerank_hash = meta.checkpoint_hash
                log_event(
                    db,
                    stage="retrieving",
                    message=f"Reranked {len(ordered_pool)} candidates with mode={rerank_mode}",
                    investigation_id=inv.id,
                    repository_id=inv.repository_id,
                )

        selected: list[tuple[uuid.UUID, float]] = []
        per_file: dict[str, int] = defaultdict(int)
        for chunk_id, score in ordered_pool:
            chunk = chunk_by_id.get(chunk_id)
            if not chunk:
                continue
            path = chunk.source_file.path
            if per_file[path] >= per_file_cap:
                continue
            selected.append((chunk_id, score))
            per_file[path] += 1
            if len(selected) >= evidence_cap:
                break

        for rank, (chunk_id, _score) in enumerate(selected, start=1):
            is_exact = chunk_id in exact_hits
            is_semantic = chunk_id in semantic_scores
            if is_exact and is_semantic:
                reason = MatchReason.both
            elif is_exact:
                reason = MatchReason.exact
            else:
                reason = MatchReason.semantic
            db.add(
                Evidence(
                    investigation_id=inv.id,
                    chunk_id=chunk_id,
                    rank=rank,
                    semantic_score=semantic_scores.get(chunk_id),
                    exact_match=is_exact,
                    match_reason=reason,
                )
            )

        inv.reranker_mode = rerank_mode or "disabled"
        inv.reranker_model_path = rerank_path
        inv.reranker_checkpoint_hash = rerank_hash
        inv.reranker_fallback = bool(rerank_fallback)

        inv.status = InvestigationStatus.evidence_ready
        inv.completed_at = _utcnow()
        job.status = JobStatus.completed
        job.completed_at = _utcnow()
        db.commit()
        msg = (
            f"Evidence ready: {len(selected)} items"
            if selected
            else "No relevant evidence found"
        )
        log_event(
            db,
            stage="evidence_ready",
            message=msg,
            investigation_id=inv.id,
            repository_id=inv.repository_id,
        )
    except Exception as exc:  # noqa: BLE001
        inv.status = InvestigationStatus.failed
        inv.error_message = str(exc)
        inv.completed_at = _utcnow()
        job.status = JobStatus.failed
        job.error_message = str(exc)
        job.completed_at = _utcnow()
        db.commit()
        log_event(
            db,
            stage="failed",
            message=str(exc),
            level="error",
            investigation_id=inv.id,
            repository_id=inv.repository_id,
        )
