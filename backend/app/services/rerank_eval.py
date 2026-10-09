"""P4 baseline evaluation: hybrid vs base CE vs finetuned (same frozen pools)."""

from __future__ import annotations

import json
import statistics
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.rerank_dataset import (
    load_jsonl,
    supervised_rows,
    validate_dataset,
)
from app.services.reranker import BASE_MODEL


@dataclass
class BugEvalRow:
    bug_group_id: str
    query: str
    config: str
    candidate_files: list[str]
    ranked_files: list[str]
    relevant_files: list[str]
    candidate_recall: float
    hit_at_1: float
    hit_at_5: float
    mrr_at_10: float
    missed_relevant: list[str]
    retrieval_ms: float
    rerank_ms: float


@dataclass
class ConfigAggregate:
    config: str
    bug_count: int
    candidate_recall: float
    hit_at_1: float
    hit_at_5: float
    mrr_at_10: float
    latency_ms_median: float
    latency_ms_p95: float
    retrieval_ms_median: float
    rerank_ms_median: float


@dataclass
class EvalReport:
    dataset_path: str
    dataset_hash: str
    split: str
    split_strategy: str
    label: str  # e.g. synthetic_smoke | real
    configs: list[ConfigAggregate]
    per_bug: list[BugEvalRow]
    notes: list[str] = field(default_factory=list)
    created_at: str = ""
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_path": self.dataset_path,
            "dataset_hash": self.dataset_hash,
            "split": self.split,
            "split_strategy": self.split_strategy,
            "label": self.label,
            "configs": [asdict(c) for c in self.configs],
            "per_bug": [asdict(b) for b in self.per_bug],
            "notes": self.notes,
            "limitations": self.limitations,
            "created_at": self.created_at,
        }


def _p95(vals: list[float]) -> float:
    if not vals:
        return 0.0
    ordered = sorted(vals)
    idx = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
    return ordered[idx]


def aggregate_chunk_to_file(ranked_chunks: list[dict[str, Any]]) -> list[str]:
    """First-seen file path wins (chunk → file aggregation for file-level metrics)."""
    seen: list[str] = []
    for ch in ranked_chunks:
        path = str(ch.get("file_path") or "")
        if path and path not in seen:
            seen.append(path)
    return seen


def file_metrics(
    ranked_files: list[str],
    relevant: set[str],
    candidate_files: set[str],
) -> dict[str, Any]:
    in_pool = relevant & candidate_files
    recall = (len(in_pool) / len(relevant)) if relevant else 0.0
    missed = sorted(relevant - candidate_files)
    hit1 = 1.0 if ranked_files and ranked_files[0] in relevant else 0.0
    hit5 = 1.0 if any(f in relevant for f in ranked_files[:5]) else 0.0
    mrr = 0.0
    for i, f in enumerate(ranked_files[:10], start=1):
        if f in relevant:
            mrr = 1.0 / i
            break
    return {
        "candidate_recall": recall,
        "hit_at_1": hit1,
        "hit_at_5": hit5,
        "mrr_at_10": mrr,
        "missed_relevant": missed,
    }


def build_frozen_pools(
    rows: list[dict[str, Any]],
    *,
    candidate_limit: int = 50,
) -> dict[str, dict[str, Any]]:
    """
    Freeze candidate pool per bug_group from labeled examples in the eval split.
    Candidates = all snippets for that bug in the dataset (capped).
    Does NOT insert missing positives into candidates beyond what's listed.
    """
    by_bug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_bug[str(row["bug_group_id"])].append(row)

    pools: dict[str, dict[str, Any]] = {}
    for bg, items in by_bug.items():
        query = str(items[0]["query"])
        # Prefer the first query text; all should be related
        relevant = {
            str(r["file_path"])
            for r in items
            if r.get("label") == "relevant"
        }
        # Deduplicate candidates by (file_path, snippet) preserving order
        seen: set[str] = set()
        candidates: list[dict[str, Any]] = []
        for r in items:
            key = f"{r['file_path']}::{r['snippet'][:80]}"
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "file_path": r["file_path"],
                    "snippet": r["snippet"],
                    "example_id": r["example_id"],
                    "hybrid_rank": len(candidates) + 1,
                }
            )
            if len(candidates) >= candidate_limit:
                break
        pools[bg] = {
            "query": query,
            "relevant_files": sorted(relevant),
            "candidates": candidates,
        }
    return pools


def _rank_hybrid(cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(cands, key=lambda c: int(c.get("hybrid_rank") or 0))


def _rank_with_ce(
    query: str,
    cands: list[dict[str, Any]],
    *,
    mode: str,
    model_path: str | None,
) -> tuple[list[dict[str, Any]], float]:
    t0 = time.perf_counter()
    snippets = [str(c["snippet"]) for c in cands]
    # Direct score_pairs with forced mode/path via temporary settings override is awkward;
    # call CE loader explicitly for eval configs.
    if mode == "disabled":
        ranked = _rank_hybrid(cands)
        return ranked, (time.perf_counter() - t0) * 1000

    from app.services.reranker import _load_cross_encoder

    path = model_path or BASE_MODEL
    model = _load_cross_encoder(path)
    pairs = [[query, s] for s in snippets]
    scores = [float(x) for x in model.predict(pairs, show_progress_bar=False)]
    order = sorted(range(len(cands)), key=lambda i: scores[i], reverse=True)
    ranked = [cands[i] for i in order]
    return ranked, (time.perf_counter() - t0) * 1000


def run_baseline_eval(
    dataset_path: Path,
    *,
    split: str = "test",
    candidate_limit: int = 50,
    finetuned_path: str | None = None,
    label: str = "synthetic_smoke",
) -> EvalReport:
    rows = load_jsonl(dataset_path)
    validation = validate_dataset(rows)
    eval_rows = supervised_rows(rows, split=split)
    pools = build_frozen_pools(eval_rows, candidate_limit=candidate_limit)

    configs_spec = [
        ("hybrid", "disabled", None),
        ("hybrid_plus_base_ce", "base", BASE_MODEL),
    ]
    if finetuned_path and Path(finetuned_path).exists():
        configs_spec.append(("hybrid_plus_finetuned_ce", "finetuned", finetuned_path))

    per_bug: list[BugEvalRow] = []
    latencies: dict[str, list[float]] = defaultdict(list)
    ret_ms_map: dict[str, list[float]] = defaultdict(list)
    rr_ms_map: dict[str, list[float]] = defaultdict(list)
    metric_acc: dict[str, list[dict[str, float]]] = defaultdict(list)

    for bg, pool in pools.items():
        query = pool["query"]
        cands = pool["candidates"]
        relevant = set(pool["relevant_files"])
        cand_files = {str(c["file_path"]) for c in cands}
        # Retrieval latency approximated as pool assembly cost (frozen)
        retrieval_ms = 0.05 * len(cands)

        for cfg_name, mode, path in configs_spec:
            ranked_chunks, rerank_ms = _rank_with_ce(query, cands, mode=mode, model_path=path)
            ranked_files = aggregate_chunk_to_file(ranked_chunks)
            m = file_metrics(ranked_files, relevant, cand_files)
            total_ms = retrieval_ms + rerank_ms
            latencies[cfg_name].append(total_ms)
            ret_ms_map[cfg_name].append(retrieval_ms)
            rr_ms_map[cfg_name].append(rerank_ms)
            metric_acc[cfg_name].append(m)
            per_bug.append(
                BugEvalRow(
                    bug_group_id=bg,
                    query=query[:200],
                    config=cfg_name,
                    candidate_files=sorted(cand_files),
                    ranked_files=ranked_files,
                    relevant_files=sorted(relevant),
                    candidate_recall=m["candidate_recall"],
                    hit_at_1=m["hit_at_1"],
                    hit_at_5=m["hit_at_5"],
                    mrr_at_10=m["mrr_at_10"],
                    missed_relevant=m["missed_relevant"],
                    retrieval_ms=retrieval_ms,
                    rerank_ms=rerank_ms,
                )
            )

    aggregates: list[ConfigAggregate] = []
    for cfg_name, _, _ in configs_spec:
        ms = metric_acc[cfg_name]
        n = len(ms) or 1

        def avg(key: str) -> float:
            return sum(float(x[key]) for x in ms) / n if ms else 0.0

        totals = latencies[cfg_name]
        aggregates.append(
            ConfigAggregate(
                config=cfg_name,
                bug_count=len(pools),
                candidate_recall=avg("candidate_recall"),
                hit_at_1=avg("hit_at_1"),
                hit_at_5=avg("hit_at_5"),
                mrr_at_10=avg("mrr_at_10"),
                latency_ms_median=statistics.median(totals) if totals else 0.0,
                latency_ms_p95=_p95(totals),
                retrieval_ms_median=statistics.median(ret_ms_map[cfg_name])
                if ret_ms_map[cfg_name]
                else 0.0,
                rerank_ms_median=statistics.median(rr_ms_map[cfg_name])
                if rr_ms_map[cfg_name]
                else 0.0,
            )
        )

    limitations = list(validation.notes)
    if not finetuned_path or not Path(finetuned_path).exists():
        limitations.append("Finetuned configuration skipped: no checkpoint.")
    limitations.append(
        "Chunk→file aggregation: first-seen file in ranked chunk list (max score order)."
    )

    return EvalReport(
        dataset_path=str(dataset_path),
        dataset_hash=validation.dataset_hash,
        split=split,
        split_strategy=validation.split_strategy,
        label=label,
        configs=aggregates,
        per_bug=per_bug,
        notes=validation.notes,
        created_at=datetime.now(timezone.utc).isoformat(),
        limitations=limitations,
    )


def save_eval_report(data_dir: Path, report: EvalReport) -> Path:
    root = data_dir / "rerank" / "eval_runs"
    root.mkdir(parents=True, exist_ok=True)
    rid = str(uuid.uuid4())
    path = root / f"{rid}.json"
    payload = report.to_dict()
    payload["id"] = rid
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def list_eval_reports(data_dir: Path) -> list[dict[str, Any]]:
    root = data_dir / "rerank" / "eval_runs"
    if not root.exists():
        return []
    out = []
    for p in sorted(root.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append(
                {
                    "id": data.get("id") or p.stem,
                    "created_at": data.get("created_at"),
                    "label": data.get("label"),
                    "dataset_hash": data.get("dataset_hash"),
                    "split": data.get("split"),
                    "bug_count": (data.get("configs") or [{}])[0].get("bug_count"),
                    "path": str(p),
                }
            )
        except Exception:  # noqa: BLE001
            continue
    return out


def load_eval_report(data_dir: Path, run_id: str) -> dict[str, Any] | None:
    path = data_dir / "rerank" / "eval_runs" / f"{run_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
