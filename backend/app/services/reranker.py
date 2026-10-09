"""P4 cross-encoder reranker: disabled / base / finetuned with fallback."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

BASE_MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"


@dataclass
class RerankResult:
    scores: list[float]
    mode: str
    model_path: str | None
    checkpoint_hash: str | None
    fallback: bool
    error: str | None = None


def checkpoint_hash_for_path(path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    if p.is_file():
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    if p.is_dir():
        # Hash sorted file digests for local SentenceTransformer/CrossEncoder dirs
        h = hashlib.sha256()
        files = sorted(x for x in p.rglob("*") if x.is_file())
        if not files:
            return hashlib.sha256(str(p.resolve()).encode()).hexdigest()
        for fp in files:
            h.update(fp.relative_to(p).as_posix().encode())
            h.update(b"\0")
            with fp.open("rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
        return h.hexdigest()
    return hashlib.sha256(path.encode()).hexdigest()


@lru_cache(maxsize=4)
def _load_cross_encoder(model_name_or_path: str):
    from sentence_transformers import CrossEncoder

    logger.info("Loading cross-encoder %s", model_name_or_path)
    return CrossEncoder(model_name_or_path)


def resolve_model_path(mode: str | None = None) -> tuple[str, str | None]:
    """Return (mode, model_path). mode is disabled|base|finetuned."""
    settings = get_settings()
    mode = (mode or settings.reranker_mode or "disabled").strip().lower()
    if mode == "disabled":
        return "disabled", None
    if mode == "base":
        return "base", settings.reranker_model_path or BASE_MODEL
    if mode == "finetuned":
        path = settings.reranker_model_path
        if not path or not Path(path).exists():
            return "finetuned", path
        return "finetuned", path
    return "disabled", None


def score_pairs(
    query: str,
    snippets: list[str],
    *,
    mode: str | None = None,
) -> RerankResult:
    resolved_mode, model_path = resolve_model_path(mode)
    if resolved_mode == "disabled" or not snippets:
        return RerankResult(
            scores=[0.0] * len(snippets),
            mode="disabled",
            model_path=None,
            checkpoint_hash=None,
            fallback=False,
        )
    if resolved_mode == "finetuned" and (not model_path or not Path(model_path).exists()):
        return RerankResult(
            scores=[0.0] * len(snippets),
            mode="disabled",
            model_path=model_path,
            checkpoint_hash=None,
            fallback=True,
            error="Finetuned checkpoint unavailable; falling back to hybrid ranking",
        )
    assert model_path is not None
    try:
        model = _load_cross_encoder(model_path)
        pairs = [[query, s] for s in snippets]
        raw = model.predict(pairs, show_progress_bar=False)
        scores = [float(x) for x in raw]
        return RerankResult(
            scores=scores,
            mode=resolved_mode,
            model_path=model_path,
            checkpoint_hash=checkpoint_hash_for_path(model_path)
            if resolved_mode == "finetuned"
            else checkpoint_hash_for_path(None),
            fallback=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Reranker failed; falling back")
        return RerankResult(
            scores=[0.0] * len(snippets),
            mode="disabled",
            model_path=model_path,
            checkpoint_hash=None,
            fallback=True,
            error=str(exc),
        )


def rerank_indices(
    query: str,
    items: list[dict[str, Any]],
    *,
    snippet_key: str = "snippet",
    mode: str | None = None,
) -> tuple[list[int], RerankResult]:
    """Return permutation of indices (best first) and metadata."""
    snippets = [str(it.get(snippet_key) or "") for it in items]
    result = score_pairs(query, snippets, mode=mode)
    if result.mode == "disabled" or result.fallback:
        # Preserve original order
        return list(range(len(items))), result
    order = sorted(range(len(items)), key=lambda i: result.scores[i], reverse=True)
    return order, result
