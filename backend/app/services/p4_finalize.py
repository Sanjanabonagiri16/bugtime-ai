"""P4 close-out: freeze experiment, blind test labels, held-out comparison, report."""

from __future__ import annotations

import hashlib
import json
import random
import shutil
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.offline_hybrid import RETRIEVAL_CONFIG
from app.services.p4b_pilot import (
    BASE_MODEL,
    MAX_LENGTH,
    TRUNCATION_POLICY,
    _assignments,
    _device,
    _judgments_for_bug,
    _load_pool_for_bug,
    _rank_metrics_with_uncertainty,
    _root,
    _score_ce,
    _utcnow,
)
from app.services.rerank_eval import aggregate_chunk_to_file, _p95
from app.services.reranker import checkpoint_hash_for_path
from app.services.swebench_import import EXPERIMENT_NAME

SELECTED_EPOCH = 2
BLIND_SHUFFLE_SEED = 20261009
FILE_AGGREGATION_RULE = (
    "Chunk→file: first-seen file path in the ranked chunk list wins "
    "(max score / hybrid order → file-level Hit@k / MRR)."
)
METRIC_DEFINITIONS = {
    "Hit@1": "1 if a confirmed-relevant file appears at rank 1 after file aggregation",
    "Hit@5": "1 if any confirmed-relevant file appears in top-5 aggregated files",
    "MRR@10": "1/rank of first confirmed-relevant file within top-10 aggregated files (else 0)",
    "lower_bound": "Only human/agent labels == relevant count as relevant",
    "upper_bound": "relevant OR uncertain files count as relevant (optimistic)",
    "uncertain_handling": "Uncertain never forced to irrelevant; ranked lists unchanged",
}


def freeze_experiment() -> dict[str, Any]:
    root = _root()
    meta = json.loads(
        (root / "p4b" / "checkpoints" / "pilot_v1" / "training_meta.json").read_text()
    )
    assert meta["selected_epoch"] == SELECTED_EPOCH
    epoch_path = next(h["path"] for h in meta["history"] if h["epoch"] == SELECTED_EPOCH)
    epoch_hash = checkpoint_hash_for_path(epoch_path)
    assert epoch_hash == meta["selected_checkpoint_hash"]

    assigns = _assignments()
    test_ids = sorted(i for i, s in assigns.items() if s == "test")
    pool_hashes = {}
    for iid in test_ids:
        pool = _load_pool_for_bug(iid)
        pool_hashes[iid] = pool["pool_hash"]

    # Try to record base model revision from HF cache / config
    base_revision = None
    try:
        from huggingface_hub import hf_hub_download
        import json as _json

        cfg = Path(
            hf_hub_download(BASE_MODEL, "config.json")
        )
        base_revision = cfg.parent.name  # snapshot dir often is revision
        conf = _json.loads(cfg.read_text())
        tokenizer_class = conf.get("model_type")
    except Exception:  # noqa: BLE001
        tokenizer_class = "bert"
        base_revision = "unresolved_at_freeze_time"

    freeze = {
        "frozen_at": _utcnow(),
        "experiment": EXPERIMENT_NAME,
        "selected_epoch": SELECTED_EPOCH,
        "selected_checkpoint_hash": epoch_hash,
        "selected_checkpoint_path": epoch_path,
        "base_model": BASE_MODEL,
        "base_model_revision_note": base_revision,
        "tokenizer": {
            "family": "BertTokenizerFast (ms-marco-MiniLM-L6-v2)",
            "model_type": tokenizer_class,
            "max_length": MAX_LENGTH,
            "truncation_policy": TRUNCATION_POLICY,
        },
        "candidate_pool_hashes": pool_hashes,
        "retrieval_config": RETRIEVAL_CONFIG,
        "file_aggregation_rule": FILE_AGGREGATION_RULE,
        "metric_definitions": METRIC_DEFINITIONS,
        "application_default": "hybrid_retrieval (RERANKER_MODE=disabled)",
        "no_further_tuning_on_this_test_set": True,
        "train_bugs": 14,
        "validation_bugs": 3,
        "validation_repos": ["pyvista/pyvista"],
        "test_bugs": 3,
        "test_repos": ["sqlfluff/sqlfluff"],
        "test_instance_ids": test_ids,
    }
    dest = root / "p4b" / "freeze" / "experiment_freeze.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(freeze, indent=2), encoding="utf-8")
    return freeze


def build_blind_queue(freeze: dict[str, Any]) -> dict[str, Any]:
    """All 150 candidates, grouped by bug/file, fixed shuffle, no model scores."""
    root = _root()
    items: list[dict[str, Any]] = []
    for iid in freeze["test_instance_ids"]:
        case = json.loads((root / "cases" / f"{iid}.json").read_text())
        rows = [
            json.loads(l)
            for l in (root / "reviews" / f"{iid}.jsonl").read_text().splitlines()
            if l.strip()
        ]
        for r in rows:
            items.append(
                {
                    "example_id": r["example_id"],
                    "bug_group_id": iid,
                    "repository_id": r["repository_id"],
                    "commit_sha": r["commit_sha"],
                    "query": case["query"],  # issue text allowed
                    "file_path": r["file_path"],
                    "line_range": r.get("line_range"),
                    "snippet": r["snippet"],  # pre-fix source
                    # intentionally omit hybrid_rank, scores, predictions
                    "reviewer_only_patch_available": True,
                }
            )
    assert len(items) == 150
    rng = random.Random(BLIND_SHUFFLE_SEED)
    order = list(range(len(items)))
    rng.shuffle(order)
    queued = []
    for blind_pos, idx in enumerate(order):
        row = dict(items[idx])
        row["blind_review_order"] = blind_pos
        queued.append(row)

    # Group view for UI/export (still without scores)
    by_bug: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for row in queued:
        by_bug[row["bug_group_id"]][row["file_path"]].append(
            {
                "example_id": row["example_id"],
                "line_range": row["line_range"],
                "snippet": row["snippet"],
                "blind_review_order": row["blind_review_order"],
            }
        )

    payload = {
        "blind_shuffle_seed": BLIND_SHUFFLE_SEED,
        "n_candidates": len(queued),
        "hidden_fields": ["hybrid_rank", "model_scores", "predictions", "rrf_score"],
        "allowed_fields": ["issue_text", "pre_fix_snippet", "line_range", "reviewer_only_diff"],
        "queue": queued,
        "grouped_by_bug_file": {
            bug: {fp: snips for fp, snips in files.items()} for bug, files in by_bug.items()
        },
    }
    dest = root / "p4b" / "freeze" / "blind_test_queue.json"
    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _blind_decide(row: dict[str, Any], case: dict[str, Any], patch: str) -> tuple[str, str]:
    """Judge without model scores — issue + snippet + reviewer patch only."""
    path = row["file_path"]
    snip = row["snippet"]
    sl = snip.lower()
    q = case["query"].lower()
    suggested = set(case.get("suggested_relevant_files") or [])

    # noise
    if any(
        path.startswith(p)
        for p in (
            ".github/",
            "docs/",
            "README",
            "CHANGELOG",
            "setup.",
            "pyproject",
            "tox.",
            ".pre-commit",
        )
    ):
        return "irrelevant", "docs/CI/config noise"

    patch_files = []
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4 and parts[3].startswith("b/"):
                patch_files.append(parts[3][2:])

    # extract added identifiers from patch
    tokens = set()
    for line in patch.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            for tok in line[1:].replace("(", " ").replace(")", " ").replace(".", " ").split():
                if len(tok) > 3 and tok.isidentifier():
                    tokens.add(tok)

    if path in patch_files or path in suggested:
        hits = sum(1 for t in tokens if t in snip)
        # also topical keywords from issues
        topical = [
            "helper",
            "templater",
            "parse",
            "segment",
            "indent",
            "lint",
            "rule",
            "fix",
            "dialect",
            "whitespace",
        ]
        topic_hits = sum(1 for t in topical if t in q and t in sl)
        if path.endswith(".py") and (hits >= 1 or topic_hits >= 1):
            return "relevant", "pre-fix snippet in patched/suggested file overlaps edit theme"
        if hits >= 2:
            return "relevant", "pre-fix snippet overlaps patched symbols"
        return "uncertain", "in changed file but snippet may miss edited region"

    if "test" in path.lower():
        words = [w for w in q.replace("`", " ").split() if len(w) > 5][:15]
        if sum(1 for w in words if w.lower() in sl) >= 2:
            return "uncertain", "test may illustrate failure; not primary site"
        return "irrelevant", "test unrelated to primary bug site"

    # code outside patch
    if sum(1 for t in tokens if t in snip) >= 3:
        return "uncertain", "unchanged file mentioning patched symbols"
    return "irrelevant", "unchanged file; no clear link to reported behavior"


def annotate_test_blind(freeze: dict[str, Any], queue: dict[str, Any]) -> dict[str, Any]:
    """
    Apply agent judgments in blind order. No model scores used.
    Labels stored separately from any prior train/val provenance.
    """
    root = _root()
    provenance = "agent_generated_weak_supervision"
    evaluation_label = "weakly_judged_held_out_evaluation"
    human_confirmed = 0  # none available

    # Work in blind order
    updates_by_bug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cases = {
        iid: json.loads((root / "cases" / f"{iid}.json").read_text())
        for iid in freeze["test_instance_ids"]
    }
    for row in sorted(queue["queue"], key=lambda r: r["blind_review_order"]):
        iid = row["bug_group_id"]
        case = cases[iid]
        patch = (case.get("reviewer_only") or {}).get("patch") or ""
        label, note = _blind_decide(row, case, patch)
        updates_by_bug[iid].append(
            {
                "example_id": row["example_id"],
                "label": label,
                "annotation_note": note,
                "label_source": provenance,
                "judgment_channel": "agent_blind",
                "human_confirmed": False,
                "blind_review_order": row["blind_review_order"],
            }
        )

    # Write reviews (preserve uncertain); do not look at predictions
    all_records = []
    for iid, ups in updates_by_bug.items():
        rev_path = root / "reviews" / f"{iid}.jsonl"
        existing = {
            json.loads(l)["example_id"]: json.loads(l)
            for l in rev_path.read_text().splitlines()
            if l.strip()
        }
        by_id = {u["example_id"]: u for u in ups}
        out_rows = []
        for eid, row in existing.items():
            u = by_id[eid]
            row = dict(row)
            row["label"] = u["label"]
            row["annotation_note"] = u["annotation_note"]
            row["label_source"] = u["label_source"]
            row["judgment_channel"] = u["judgment_channel"]
            row["human_confirmed"] = False
            row["blind_review_order"] = u["blind_review_order"]
            row["reviewed_at"] = _utcnow()
            # strip ranking hints from stored review export copies later
            out_rows.append(row)
            all_records.append(
                {
                    "example_id": eid,
                    "bug_group_id": iid,
                    "label": row["label"],
                    "label_source": row["label_source"],
                    "human_confirmed": False,
                    "annotation_note": row["annotation_note"],
                    "blind_review_order": row["blind_review_order"],
                }
            )
        with rev_path.open("w", encoding="utf-8") as f:
            for r in sorted(out_rows, key=lambda x: x.get("blind_review_order", 0)):
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    counts = Counter(r["label"] for r in all_records)
    manifest_body = json.dumps(all_records, sort_keys=True, separators=(",", ":"))
    label_manifest_hash = hashlib.sha256(manifest_body.encode()).hexdigest()

    manifest = {
        "frozen_before_scoring": True,
        "frozen_at": _utcnow(),
        "evaluation_label": evaluation_label,
        "annotation_provenance": provenance,
        "human_confirmed_count": human_confirmed,
        "agent_generated_count": len(all_records),
        "label_counts": dict(counts),
        "n_records": len(all_records),
        "label_manifest_hash": label_manifest_hash,
        "uncertain_preserved": counts.get("uncertain", 0),
        "note": (
            "Only agent annotation is available for the held-out set; "
            "results are a weakly judged held-out evaluation. "
            "Labels frozen before revealing model rankings/scores."
        ),
        "records": all_records,
    }
    dest = root / "p4b" / "freeze" / "test_label_manifest.json"
    dest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    # Immutable copy
    shutil.copy2(dest, root / "p4b" / "freeze" / f"test_label_manifest_{label_manifest_hash[:12]}.json")
    return manifest


def run_held_out_comparison(freeze: dict[str, Any], label_manifest: dict[str, Any]) -> dict[str, Any]:
    """Score hybrid / base CE / FT on frozen test pools after labels are sealed."""
    from sentence_transformers import CrossEncoder

    # Confirm manifest hash unchanged
    records = label_manifest["records"]
    body = json.dumps(records, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(body.encode()).hexdigest() == label_manifest["label_manifest_hash"]

    test_ids = freeze["test_instance_ids"]
    ft_path = freeze["selected_checkpoint_path"]

    t_cold = time.perf_counter()
    base_model = CrossEncoder(BASE_MODEL, max_length=MAX_LENGTH)
    cold_ms = (time.perf_counter() - t_cold) * 1000
    ft_model = CrossEncoder(ft_path, max_length=MAX_LENGTH)

    per_bug: list[dict[str, Any]] = []
    warm: dict[str, list[float]] = defaultdict(list)

    exact_counts = Counter()
    for r in records:
        exact_counts[r["label"]] += 1

    for iid in test_ids:
        pool = _load_pool_for_bug(iid)
        case = json.loads((_root() / "cases" / f"{iid}.json").read_text())
        query = case["query"]
        cands = pool["candidates"]
        judgments = _judgments_for_bug(iid)
        by_file: dict[str, set[str]] = defaultdict(set)
        for c in cands:
            eid = f"{iid}::{c['chunk_id']}"
            by_file[c["file_path"]].add(judgments.get(eid, "unreviewed"))

        bug_label_counts = Counter(judgments[e] for e in judgments)

        def run(cfg: str, ordered: list[dict], ms: float) -> None:
            files = aggregate_chunk_to_file(ordered)
            metrics = _rank_metrics_with_uncertainty(files, by_file)
            per_bug.append(
                {
                    "bug_group_id": iid,
                    "repository_id": case["repository_id"],
                    "config": cfg,
                    "exact_label_counts": dict(bug_label_counts),
                    "ranked_files": files,
                    "metrics": metrics,
                    "warm_rerank_ms": ms,
                    "pool_hash": pool["pool_hash"],
                    "relevant_in_top50_proxy": pool["failure_flags"]["relevant_in_candidate_pool"],
                    "missing_from_top50_proxy": pool.get("relevant_missing_from_top50"),
                }
            )
            warm[cfg].append(ms)

        hybrid = sorted(cands, key=lambda c: int(c.get("hybrid_rank") or 0))
        run("hybrid", hybrid, 0.0)

        snippets = [c["snippet"] for c in cands]
        t1 = time.perf_counter()
        scores = _score_ce(base_model, query, snippets)
        order = sorted(range(len(cands)), key=lambda i: scores[i], reverse=True)
        run("hybrid_plus_base_ce", [cands[i] for i in order], (time.perf_counter() - t1) * 1000)

        t2 = time.perf_counter()
        scores_ft = _score_ce(ft_model, query, snippets)
        order2 = sorted(range(len(cands)), key=lambda i: scores_ft[i], reverse=True)
        run(
            "hybrid_plus_finetuned_ce",
            [cands[i] for i in order2],
            (time.perf_counter() - t2) * 1000,
        )

    def agg(cfg: str, bound: str) -> dict[str, Any]:
        rows = [b for b in per_bug if b["config"] == cfg]
        usable = [b for b in rows if b["metrics"]["has_confirmed_relevant"]]
        n = len(usable) or 1

        def avg(key: str) -> float:
            return sum(float(b["metrics"][bound][key]) for b in usable) / n if usable else 0.0

        lats = warm[cfg]
        return {
            "config": cfg,
            "bound": bound,
            "bugs_total": len(rows),
            "bugs_with_confirmed_relevant": len(usable),
            "hit_at_1": avg("hit_at_1"),
            "hit_at_5": avg("hit_at_5"),
            "mrr_at_10": avg("mrr_at_10"),
            "candidate_recall": avg("candidate_recall"),
            "warm_latency_ms_median": statistics.median(lats) if lats else 0.0,
            "warm_latency_ms_p95": _p95(lats) if lats else 0.0,
            "per_bug_hit_at_1": {
                b["bug_group_id"]: b["metrics"][bound]["hit_at_1"] for b in rows
            },
            "per_bug_mrr_at_10": {
                b["bug_group_id"]: b["metrics"][bound]["mrr_at_10"] for b in rows
            },
        }

    configs = ["hybrid", "hybrid_plus_base_ce", "hybrid_plus_finetuned_ce"]
    aggregates = []
    for cfg in configs:
        aggregates.append(agg(cfg, "lower"))
        aggregates.append(agg(cfg, "upper"))

    result = {
        "split": "test",
        "evaluation_label": label_manifest["evaluation_label"],
        "label_manifest_hash": label_manifest["label_manifest_hash"],
        "exact_test_label_counts": dict(exact_counts),
        "cold_load_ms_base_ce": cold_ms,
        "hardware": {"device": _device()},
        "checkpoint_hash": freeze["selected_checkpoint_hash"],
        "aggregates": aggregates,
        "per_bug": per_bug,
        "created_at": _utcnow(),
    }
    dest = _root() / "p4b" / "reports" / "held_out_test_comparison.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def write_final_report(
    freeze: dict[str, Any],
    label_manifest: dict[str, Any],
    test_cmp: dict[str, Any],
    val_report: dict[str, Any] | None,
    optin: dict[str, Any],
) -> dict[str, Any]:
    root = _root()
    # Load prior val aggregates if present
    pilot = root / "p4b" / "reports" / "pilot_final.json"
    val_block = {}
    if pilot.exists():
        prev = json.loads(pilot.read_text())
        val_block = {
            "baselines_lower": prev.get("baselines_validation_lower_bound"),
            "finetuned_lower": prev.get("with_finetuned_validation_lower_bound"),
        }

    def lower(cmp: dict, cfg: str) -> dict:
        for a in cmp["aggregates"]:
            if a["config"] == cfg and a["bound"] == "lower":
                return a
        return {}

    report = {
        "title": "BugTime P4 reranker pilot — final held-out evaluation",
        "status": "pilot_closed",
        "experimental_checkpoint": True,
        "activated": False,
        "application_default": freeze["application_default"],
        "selected_epoch": freeze["selected_epoch"],
        "selected_checkpoint_hash": freeze["selected_checkpoint_hash"],
        "dataset_structure": {
            "train_bugs": 14,
            "validation_bugs": 3,
            "validation_repository": "pyvista/pyvista (single repo)",
            "test_bugs": 3,
            "test_repository": "sqlfluff/sqlfluff (held-out)",
        },
        "annotation_provenance": {
            "train_val": "agent_generated_weak_supervision",
            "test": label_manifest["evaluation_label"],
            "human_confirmed_test_labels": label_manifest["human_confirmed_count"],
            "label_manifest_hash": label_manifest["label_manifest_hash"],
        },
        "limitations": [
            "Labels are agent-generated without independent human confirmation (weak supervision / weakly judged held-out eval).",
            "Validation uses only 3 bugs from one repository; test uses 3 bugs from another — insufficient for reliable generalization claims.",
            "Base model ms-marco-MiniLM-L6-v2 may have been exposed to public web/benchmark-adjacent text; contamination risk is unquantified.",
            "Improved retrieval rankings do not imply repair success or verified fixes.",
            "Prior 75% figure was patch-file proxy coverage, not human-verified recall.",
        ],
        "validation_summary": val_block,
        "test_summary_lower_bound": {
            cfg: lower(test_cmp, cfg)
            for cfg in ("hybrid", "hybrid_plus_base_ce", "hybrid_plus_finetuned_ce")
        },
        "test_exact_label_counts": test_cmp["exact_test_label_counts"],
        "test_per_bug": test_cmp["per_bug"],
        "opt_in_smoke": optin,
        "closure_rules": [
            "No automatic checkpoint activation",
            "No further tuning against this test set",
            "Any later model revision requires fresh held-out evaluation data",
            "Hybrid retrieval remains application default",
        ],
        "created_at": _utcnow(),
    }

    # Honest comparison narrative
    h = lower(test_cmp, "hybrid")
    b = lower(test_cmp, "hybrid_plus_base_ce")
    f = lower(test_cmp, "hybrid_plus_finetuned_ce")
    report["test_headline"] = {
        "hybrid_mrr": h.get("mrr_at_10"),
        "base_ce_mrr": b.get("mrr_at_10"),
        "finetuned_mrr": f.get("mrr_at_10"),
        "hybrid_hit1": h.get("hit_at_1"),
        "base_ce_hit1": b.get("hit_at_1"),
        "finetuned_hit1": f.get("hit_at_1"),
        "bugs_with_confirmed_relevant": f.get("bugs_with_confirmed_relevant"),
        "bugs_total": f.get("bugs_total"),
    }

    dest = root / "p4b" / "reports" / "P4_FINAL_REPORT.json"
    dest.write_text(json.dumps(report, indent=2), encoding="utf-8")

    md = [
        "# P4 final held-out report",
        "",
        f"- Status: **{report['status']}** · experimental checkpoint · **not activated**",
        f"- Selected epoch **{freeze['selected_epoch']}** hash `{freeze['selected_checkpoint_hash']}`",
        f"- App default: **hybrid retrieval**",
        "",
        "## Dataset",
        "- **14** training bugs (4 repos)",
        "- **3** validation bugs from **one** repository (pyvista)",
        "- **3** test bugs from **another** repository (sqlfluff)",
        "",
        "## Provenance",
        f"- Train/val: agent weak supervision",
        f"- Test: **{label_manifest['evaluation_label']}** (human confirmed: {label_manifest['human_confirmed_count']})",
        f"- Label manifest hash: `{label_manifest['label_manifest_hash']}`",
        "",
        "## Test lower-bound metrics (confirmed relevant only)",
        "",
        "| config | Hit@1 | Hit@5 | MRR@10 | n_pos_bugs |",
        "|---|---:|---:|---:|---:|",
    ]
    for cfg in ("hybrid", "hybrid_plus_base_ce", "hybrid_plus_finetuned_ce"):
        a = lower(test_cmp, cfg)
        md.append(
            f"| `{cfg}` | {a.get('hit_at_1', 0):.3f} | {a.get('hit_at_5', 0):.3f} | "
            f"{a.get('mrr_at_10', 0):.3f} | {a.get('bugs_with_confirmed_relevant', 0)}/"
            f"{a.get('bugs_total', 0)} |"
        )
    md += [
        "",
        f"Test label counts: {test_cmp['exact_test_label_counts']}",
        f"Cold base CE load: {test_cmp.get('cold_load_ms_base_ce'):.0f} ms",
        "",
        "## Limitations",
        "",
    ]
    for lim in report["limitations"]:
        md.append(f"- {lim}")
    md += [
        "",
        "## Closure",
        "- No automatic activation; no tuning on this test set.",
        "- Later revisions need fresh held-out data.",
        f"- Opt-in smoke: {json.dumps(optin)}",
        "",
    ]
    (root / "p4b" / "reports" / "P4_FINAL_REPORT.md").write_text("\n".join(md), encoding="utf-8")
    return report


def optin_load_and_restore() -> dict[str, Any]:
    """
    Verify one opt-in path can load the checkpoint and record its hash,
    then restore default (disabled / hybrid) configuration intent.
    """
    settings = get_settings()
    root = _root()
    meta = json.loads(
        (root / "p4b" / "checkpoints" / "pilot_v1" / "training_meta.json").read_text()
    )
    path = meta["selected_path"]
    expected = meta["selected_checkpoint_hash"]

    # Record opt-in attempt (does not change process env permanently beyond active.json)
    active_path = Path(settings.data_dir) / "rerank" / "active.json"
    active_path.parent.mkdir(parents=True, exist_ok=True)
    prior = None
    if active_path.exists():
        prior = json.loads(active_path.read_text())

    from sentence_transformers import CrossEncoder

    t0 = time.perf_counter()
    model = CrossEncoder(path, max_length=MAX_LENGTH)
    load_ms = (time.perf_counter() - t0) * 1000
    _ = model.predict([["sqlfluff indent issue", "def fix_indent(seg): return seg"]], show_progress_bar=False)
    # Hash of selected epoch dir (stable), not the copy with sidecars
    epoch_path = next(h["path"] for h in meta["history"] if h["epoch"] == SELECTED_EPOCH)
    got = checkpoint_hash_for_path(epoch_path)

    opt_in_record = {
        "mode": "finetuned",
        "model_path": path,
        "checkpoint_hash": got,
        "expected_hash": expected,
        "hash_match": got == expected,
        "load_ms": load_ms,
        "opt_in": True,
        "recorded_at": _utcnow(),
    }
    (root / "p4b" / "freeze" / "opt_in_smoke.json").write_text(
        json.dumps(opt_in_record, indent=2), encoding="utf-8"
    )

    # Restore default configuration intent
    restored = {
        "mode": "disabled",
        "model_path": None,
        "experimental": True,
        "note": "Restored after opt-in smoke. Application default remains hybrid (RERANKER_MODE=disabled).",
        "restored_at": _utcnow(),
        "prior": prior,
    }
    active_path.write_text(json.dumps(restored, indent=2), encoding="utf-8")
    return {"opt_in": opt_in_record, "restored_default": restored}


def finalize_p4() -> dict[str, Any]:
    freeze = freeze_experiment()
    queue = build_blind_queue(freeze)
    labels = annotate_test_blind(freeze, queue)
    # Labels frozen — only now score
    test_cmp = run_held_out_comparison(freeze, labels)
    optin = optin_load_and_restore()
    report = write_final_report(freeze, labels, test_cmp, None, optin)
    return {
        "freeze": freeze,
        "label_manifest_hash": labels["label_manifest_hash"],
        "test_comparison": test_cmp["aggregates"],
        "report_path": str(_root() / "p4b" / "reports" / "P4_FINAL_REPORT.md"),
        "opt_in": optin,
        "activated": False,
        "status": "pilot_closed",
    }
