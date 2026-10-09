"""P4A.1 human review queue over frozen SWE-bench candidate pools."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.rerank_dataset import validate_dataset, write_jsonl
from app.services.rerank_eval import run_baseline_eval, save_eval_report
from app.services.swebench_import import (
    EXPERIMENT_NAME,
    coverage_report,
    experiment_root,
)


def _root() -> Path:
    return experiment_root(Path(get_settings().data_dir))


def _split_assignments() -> dict[str, str]:
    sm = _root() / "splits" / "manifest.json"
    if not sm.exists():
        return {}
    return json.loads(sm.read_text(encoding="utf-8")).get("assignments") or {}


def _case_label_stats(rev_path: Path) -> dict[str, int]:
    stats = {
        "total": 0,
        "unreviewed": 0,
        "human": 0,
        "relevant": 0,
        "irrelevant": 0,
        "uncertain": 0,
        "completed": 0,  # 1 if no unreviewed left
    }
    if not rev_path.exists():
        return stats
    for line in rev_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        stats["total"] += 1
        lab = row.get("label")
        if lab == "unreviewed":
            stats["unreviewed"] += 1
        elif lab == "relevant":
            stats["relevant"] += 1
        elif lab == "irrelevant":
            stats["irrelevant"] += 1
        elif lab == "uncertain":
            stats["uncertain"] += 1
        if row.get("label_source") == "human_review":
            stats["human"] += 1
    stats["completed"] = 1 if stats["total"] and stats["unreviewed"] == 0 else 0
    return stats


def list_cases(*, include_test: bool = False) -> list[dict[str, Any]]:
    """List review cases. Held-out test bugs stay hidden from tuning UI by default."""
    root = _root()
    assignments = _split_assignments()
    out = []
    for p in sorted((root / "cases").glob("*.json")) if (root / "cases").exists() else []:
        case = json.loads(p.read_text(encoding="utf-8"))
        iid = case["instance_id"]
        split = assignments.get(iid, "unassigned")
        if split == "test" and not include_test:
            continue
        pool_path = root / "pools" / f"{iid}.json"
        pool = json.loads(pool_path.read_text()) if pool_path.exists() else {}
        rev_path = root / "reviews" / f"{iid}.jsonl"
        stats = _case_label_stats(rev_path)
        out.append(
            {
                "instance_id": iid,
                "repository_id": case["repository_id"],
                "commit_sha": case["commit_sha"],
                "split": split,
                "query_preview": (case.get("query") or "")[:240],
                "pool_hash": pool.get("pool_hash"),
                "failure_flags": pool.get("failure_flags"),
                "relevant_in_top50": pool.get("relevant_in_top50"),
                "relevant_missing_from_top50": pool.get("relevant_missing_from_top50"),
                "review_stats": stats,
                "batch1_priority": iid
                in {
                    "marshmallow-code__marshmallow-1229",
                    "pvlib__pvlib-python-1026",
                    "pydicom__pydicom-1000",
                },
            }
        )
    # Prefer batch-1 train bugs, then incomplete train, then validation
    order = {"train": 0, "validation": 1, "unassigned": 2, "test": 3}
    out.sort(
        key=lambda c: (
            0 if c.get("batch1_priority") else 1,
            order.get(c.get("split") or "", 9),
            0 if c["review_stats"]["unreviewed"] else 1,
            c["instance_id"],
        )
    )
    return out


def annotation_progress() -> dict[str, Any]:
    """Progress for train/validation only — no held-out test metrics for tuning."""
    cases = list_cases(include_test=False)
    by_split: dict[str, dict[str, int]] = {}
    for c in cases:
        sp = c.get("split") or "unassigned"
        bucket = by_split.setdefault(
            sp,
            {
                "bugs": 0,
                "bugs_completed": 0,
                "snippets": 0,
                "snippets_reviewed": 0,
                "relevant": 0,
                "irrelevant": 0,
                "uncertain": 0,
                "unreviewed": 0,
            },
        )
        st = c["review_stats"]
        bucket["bugs"] += 1
        bucket["bugs_completed"] += int(st.get("completed") or 0)
        bucket["snippets"] += st["total"]
        bucket["snippets_reviewed"] += st["total"] - st["unreviewed"]
        bucket["relevant"] += st.get("relevant", 0)
        bucket["irrelevant"] += st.get("irrelevant", 0)
        bucket["uncertain"] += st.get("uncertain", 0)
        bucket["unreviewed"] += st["unreviewed"]
    return {
        "by_split": by_split,
        "note": "Held-out test split is excluded from this progress view and must not guide model choices.",
        "batch1_bugs": [
            "marshmallow-code__marshmallow-1229",
            "pvlib__pvlib-python-1026",
            "pydicom__pydicom-1000",
        ],
    }


def load_case_bundle(instance_id: str) -> dict[str, Any] | None:
    root = _root()
    case_path = root / "cases" / f"{instance_id}.json"
    if not case_path.exists():
        return None
    case = json.loads(case_path.read_text(encoding="utf-8"))
    pool = json.loads((root / "pools" / f"{instance_id}.json").read_text(encoding="utf-8"))
    rows = []
    rev_path = root / "reviews" / f"{instance_id}.jsonl"
    if rev_path.exists():
        for line in rev_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    rows.sort(key=lambda r: int(r.get("hybrid_rank") or 0))
    return {
        "case": {
            "instance_id": case["instance_id"],
            "repository_id": case["repository_id"],
            "commit_sha": case["commit_sha"],
            "query": case["query"],
            "query_exclusions": case.get("query_exclusions"),
            "suggested_relevant_files": case.get("suggested_relevant_files"),
            "source_reference": case.get("source_reference"),
        },
        "reviewer_only": case.get("reviewer_only") or {},
        "pool": {
            "pool_hash": pool.get("pool_hash"),
            "retrieval_config": pool.get("retrieval_config"),
            "failure_flags": pool.get("failure_flags"),
            "relevant_indexed": pool.get("relevant_indexed"),
            "relevant_in_top50": pool.get("relevant_in_top50"),
            "relevant_missing_from_index": pool.get("relevant_missing_from_index"),
            "relevant_missing_from_top50": pool.get("relevant_missing_from_top50"),
            "candidate_count": len(pool.get("candidates") or []),
        },
        "items": rows,
    }


def save_annotations(instance_id: str, updates: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply human labels. suggested_label is never treated as human_review."""
    root = _root()
    rev_path = root / "reviews" / f"{instance_id}.jsonl"
    if not rev_path.exists():
        raise FileNotFoundError(instance_id)
    rows = [json.loads(l) for l in rev_path.read_text().splitlines() if l.strip()]
    by_id = {r["example_id"]: r for r in rows}
    now = datetime.now(timezone.utc).isoformat()
    for u in updates:
        eid = u["example_id"]
        if eid not in by_id:
            continue
        label = u.get("label")
        if label not in ("relevant", "irrelevant", "uncertain", "unreviewed"):
            continue
        row = by_id[eid]
        row["label"] = label
        row["annotation_note"] = u.get("annotation_note") or row.get("annotation_note") or ""
        if label == "unreviewed":
            row["label_source"] = "unreviewed"
        else:
            row["label_source"] = "human_review"
            row["reviewed_at"] = now
        # Never promote suggested_label into label_source=human_review automatically
    with rev_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    # refresh coverage
    cov = coverage_report(root)
    (root / "reports" / "coverage.json").write_text(json.dumps(cov, indent=2), encoding="utf-8")
    return {"ok": True, "instance_id": instance_id, "coverage": cov}


def export_reviewed_dataset(*, include_unreviewed: bool = False) -> dict[str, Any]:
    """Export supervised training pairs + retain full annotation archive."""
    root = _root()
    splits = _split_assignments()
    all_rows: list[dict[str, Any]] = []
    supervised: list[dict[str, Any]] = []
    for p in sorted((root / "reviews").glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            bug = r["bug_group_id"]
            split = splits.get(bug, "unassigned")
            out = {
                "example_id": r["example_id"],
                "bug_group_id": r["bug_group_id"],
                "repository_id": r["repository_id"],
                "commit_sha": r["commit_sha"],
                "query": r["query"],
                "file_path": r["file_path"],
                "line_range": r.get("line_range"),
                "snippet": r["snippet"],
                "label": r.get("label"),
                "label_source": r.get("label_source"),
                "source_reference": r.get("source_reference"),
                "split": split,
                "pool_hash": r.get("pool_hash"),
                "candidate_origin": r.get("candidate_origin", "natural_retrieval"),
                "annotation_note": r.get("annotation_note") or "",
            }
            all_rows.append(out)
            if (
                r.get("label") in ("relevant", "irrelevant")
                and r.get("label_source") == "human_review"
            ):
                supervised.append({**out})
            elif include_unreviewed and r.get("label") == "unreviewed":
                pass

    dest = Path(get_settings().data_dir) / "rerank" / "datasets" / f"{EXPERIMENT_NAME}_reviewed.jsonl"
    write_jsonl(dest, supervised)
    archive = root / "exports" / "annotations_full.jsonl"
    write_jsonl(archive, all_rows)  # retains uncertain + unreviewed
    validation = validate_dataset(supervised) if supervised else None
    meta = {
        "path": str(dest),
        "archive_path": str(archive),
        "exported_supervised": len(supervised),
        "archive_total": len(all_rows),
        "uncertain_count": sum(1 for r in all_rows if r.get("label") == "uncertain"),
        "unreviewed_count": sum(1 for r in all_rows if r.get("label") == "unreviewed"),
        "validation": validation.to_dict() if validation else None,
        "note": (
            "Training export = explicit human relevant/irrelevant only. "
            "Archive retains uncertain/unreviewed. Login smoke excluded. "
            "Test split must not guide model choices."
        ),
    }
    (root / "exports" / "reviewed_meta.json").parent.mkdir(parents=True, exist_ok=True)
    (root / "exports" / "reviewed_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def run_reviewed_baselines(*, allow_test: bool = False) -> dict[str, Any]:
    """Baselines for tuning use validation only. Test stays sealed unless allow_test."""
    meta = export_reviewed_dataset()
    path = Path(meta["path"])
    if not path.exists() or meta["exported_supervised"] == 0:
        return {
            "ok": False,
            "error": "no_human_reviewed_labels",
            "message": "Complete Dataset review before baseline comparison on reviewed cases.",
            "export": meta,
            "coverage": coverage_report(_root()),
        }
    reports: dict[str, Any] = {}
    for split in ("train", "validation"):
        try:
            reports[split] = run_baseline_eval(
                path,
                split=split,
                candidate_limit=50,
                label="swebench_dev_p4a1_reviewed",
            ).to_dict()
        except Exception:  # noqa: BLE001
            continue
    if allow_test:
        try:
            reports["test"] = run_baseline_eval(
                path,
                split="test",
                candidate_limit=50,
                label="swebench_dev_p4a1_reviewed",
            ).to_dict()
        except Exception:  # noqa: BLE001
            pass
    else:
        reports["test"] = {
            "sealed": True,
            "message": "Held-out test metrics are not exposed during model/retrieval tuning.",
        }
    primary = reports.get("validation") or reports.get("train")
    if not primary or primary.get("sealed"):
        return {
            "ok": False,
            "error": "no_eval_split_labels",
            "export": meta,
            "reports": reports,
        }
    # Save validation-first report when available (never leak test into tuning)
    if reports.get("validation") and not reports["validation"].get("sealed"):
        report_obj = run_baseline_eval(
            path, split="validation", candidate_limit=50, label="swebench_dev_p4a1_reviewed"
        )
    else:
        report_obj = run_baseline_eval(
            path, split="train", candidate_limit=50, label="swebench_dev_p4a1_reviewed"
        )
    saved = save_eval_report(Path(get_settings().data_dir), report_obj)
    out = {
        "ok": True,
        "export": meta,
        "report_id": saved.stem,
        "reports": reports,
        "progress": annotation_progress(),
        "login_smoke_excluded": True,
        "test_sealed": not allow_test,
    }
    (_root() / "reports" / "baseline_reviewed.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8"
    )
    return out


def batch_report(instance_ids: list[str]) -> dict[str, Any]:
    root = _root()
    assignments = _split_assignments()
    bugs = []
    totals = {"relevant": 0, "irrelevant": 0, "uncertain": 0, "unreviewed": 0}
    for iid in instance_ids:
        rev = root / "reviews" / f"{iid}.jsonl"
        st = _case_label_stats(rev)
        case = json.loads((root / "cases" / f"{iid}.json").read_text())
        pool = json.loads((root / "pools" / f"{iid}.json").read_text())
        bugs.append(
            {
                "instance_id": iid,
                "repository_id": case["repository_id"],
                "split": assignments.get(iid),
                "labels": st,
                "pool_size": st["total"],
                "candidate_coverage": {
                    "relevant_indexed": pool.get("failure_flags", {}).get("relevant_source_indexed"),
                    "relevant_in_top50": pool.get("failure_flags", {}).get(
                        "relevant_in_candidate_pool"
                    ),
                    "missing_from_top50": pool.get("relevant_missing_from_top50"),
                },
                "judgment_complete": bool(st.get("completed")),
            }
        )
        for k in ("relevant", "irrelevant", "uncertain", "unreviewed"):
            totals[k] += st.get(k, 0)
    return {
        "batch_bugs": bugs,
        "label_totals": totals,
        "explicit_train_pairs": totals["relevant"] + totals["irrelevant"],
        "uncertainties": totals["uncertain"],
        "incomplete_pool_judgments": [
            b["instance_id"] for b in bugs if not b["judgment_complete"]
        ],
        "note": (
            "75% earlier figure was patch-file proxy coverage, not human-verified recall. "
            "Training may use partial reviewed pairs; ranking eval needs full-pool review "
            "or an explicit incomplete-judgment disclosure."
        ),
    }
