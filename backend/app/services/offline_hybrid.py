"""BugTime hybrid retrieval without DB/Qdrant (local cosine + exact + RRF).

Used for P4A.1 frozen candidate pools so experiment collection does not mutate
production indexes. Ranking logic mirrors app.services.retrieval.
"""

from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.core.config import get_settings
from app.services.chunking import chunk_file, should_skip_path
from app.services.embeddings import embed_query, embed_texts
from app.services.retrieval import extract_terms, reciprocal_rank_fusion

RETRIEVAL_CONFIG = "bugtime_hybrid_v1_local_cosine_rrf"


@dataclass
class LocalChunk:
    chunk_id: str
    file_path: str
    start_line: int
    end_line: int
    symbol_name: str | None
    content: str
    vector: list[float] | None = None


def index_checkout(root: Path, *, max_files: int | None = None) -> list[LocalChunk]:
    settings = get_settings()
    max_files = max_files or settings.max_index_files
    chunks: list[LocalChunk] = []
    files: list[tuple[str, str]] = []
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        if should_skip_path(rel):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > settings.max_file_bytes:
            continue
        if total_bytes + size > settings.max_total_index_bytes:
            break
        if len(files) >= max_files:
            break
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        files.append((rel, text))
        total_bytes += size

    drafts_flat: list[tuple[str, Any]] = []
    for rel, text in files:
        for d in chunk_file(rel, text):
            drafts_flat.append((rel, d))

    batch = 64
    contents = [d.content for _, d in drafts_flat]
    vectors: list[list[float]] = []
    for i in range(0, len(contents), batch):
        vectors.extend(embed_texts(contents[i : i + batch]))

    for (rel, d), vec in zip(drafts_flat, vectors, strict=True):
        cid = hashlib.sha1(f"{rel}:{d.start_line}:{d.end_line}:{d.content[:64]}".encode()).hexdigest()
        chunks.append(
            LocalChunk(
                chunk_id=cid,
                file_path=rel,
                start_line=d.start_line,
                end_line=d.end_line,
                symbol_name=d.symbol_name,
                content=d.content,
                vector=vec,
            )
        )
    return chunks


def hybrid_retrieve(
    query: str,
    chunks: list[LocalChunk],
    *,
    limit: int = 50,
    per_file_limit: int = 3,
) -> list[dict[str, Any]]:
    if not chunks:
        return []
    filenames, identifiers = extract_terms(query)
    exact_ranked: list[uuid.UUID] = []
    exact_hits: set[str] = set()
    id_map: dict[str, uuid.UUID] = {
        c.chunk_id: uuid.UUID(hashlib.md5(c.chunk_id.encode()).hexdigest()) for c in chunks
    }

    by_uid = {id_map[c.chunk_id]: c for c in chunks}

    for c in chunks:
        path = c.file_path.lower()
        content_l = c.content.lower()
        hit = False
        for fn in filenames:
            if fn in path or fn in content_l:
                hit = True
                break
        if not hit:
            for ident in identifiers:
                if ident in c.content or (c.symbol_name and ident in c.symbol_name):
                    hit = True
                    break
        if hit:
            exact_ranked.append(id_map[c.chunk_id])
            exact_hits.add(c.chunk_id)

    qvec = np.asarray(embed_query(query), dtype=np.float32)
    mats = np.asarray([c.vector for c in chunks], dtype=np.float32)
    # embeddings are normalized → cosine = dot
    scores = mats @ qvec
    order = np.argsort(-scores)[:30]
    semantic_ranked: list[uuid.UUID] = []
    semantic_scores: dict[str, float] = {}
    for idx in order:
        c = chunks[int(idx)]
        semantic_ranked.append(id_map[c.chunk_id])
        semantic_scores[c.chunk_id] = float(scores[int(idx)])

    fused = reciprocal_rank_fusion([exact_ranked, semantic_ranked])
    selected: list[dict[str, Any]] = []
    per_file: dict[str, int] = defaultdict(int)
    for uid, rrf in fused:
        c = by_uid.get(uid)
        if not c:
            continue
        if per_file[c.file_path] >= per_file_limit:
            continue
        selected.append(
            {
                "chunk_id": c.chunk_id,
                "file_path": c.file_path,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "line_range": f"{c.start_line}-{c.end_line}",
                "symbol_name": c.symbol_name,
                "snippet": c.content,
                "rrf_score": rrf,
                "semantic_score": semantic_scores.get(c.chunk_id),
                "exact_match": c.chunk_id in exact_hits,
                "hybrid_rank": len(selected) + 1,
            }
        )
        per_file[c.file_path] += 1
        if len(selected) >= limit:
            break
    return selected


def pool_hash(candidates: list[dict[str, Any]]) -> str:
    payload = [
        f"{c.get('file_path')}:{c.get('start_line')}:{c.get('end_line')}:{c.get('chunk_id')}"
        for c in candidates
    ]
    return hashlib.sha256("\n".join(payload).encode()).hexdigest()
