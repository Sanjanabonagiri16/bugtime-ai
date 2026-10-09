"""P4B weak-supervision fine-tuning pilot (train/val only; test sealed)."""

from __future__ import annotations

import hashlib
import json
import platform
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.offline_hybrid import RETRIEVAL_CONFIG
from app.services.rerank_dataset import dataset_content_hash, validate_dataset, write_jsonl
from app.services.rerank_eval import (
    aggregate_chunk_to_file,
    file_metrics,
    _p95,
)
from app.services.reranker import BASE_MODEL, checkpoint_hash_for_path
from app.services.swebench_import import EXPERIMENT_NAME, experiment_root

SEED = 42
MAX_EPOCHS = 3
LR = 2e-5
MAX_LENGTH = 512
TRUNCATION_POLICY = (
    "CrossEncoder max_length=512; query+snippet pair truncated by tokenizer "
    "from the right (Sentence Transformers / transformers default)."
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root() -> Path:
    return experiment_root(Path(get_settings().data_dir))


def _assignments() -> dict[str, str]:
    return json.loads((_root() / "splits" / "manifest.json").read_text())["assignments"]


def audit_and_export() -> dict[str, Any]:
    """Exact counts, separate train/val JSONL, leakage checks, provenance."""
    root = _root()
    assigns = _assignments()
    # Labels were produced by the agent review pipeline without independent human confirmation.
    provenance = "agent_generated_weak_supervision"
    provenance_note = (
        "Stored label_source was 'human_review' but annotations were agent-supplied "
        "without separate human confirmation. This P4B run is a weak-supervision pilot."
    )

    by_split_raw: dict[str, Counter] = defaultdict(Counter)
    train_rows: list[dict[str, Any]] = []
    val_rows: list[dict[str, Any]] = []
    all_supervised: list[dict[str, Any]] = []
    near_keys: dict[str, list[str]] = defaultdict(list)
    repos_by_split: dict[str, set[str]] = defaultdict(set)
    bugs_by_split: dict[str, set[str]] = defaultdict(set)
    # Independent bugs/repos from split assignments (includes sealed test)
    for iid, split in assigns.items():
        bugs_by_split[split].add(iid)
        case_path = root / "cases" / f"{iid}.json"
        if case_path.exists():
            repos_by_split[split].add(json.loads(case_path.read_text())["repository_id"])

    for p in sorted((root / "reviews").glob("*.jsonl")):
        iid = p.stem
        split = assigns.get(iid, "unassigned")
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            lab = r.get("label")
            by_split_raw[split][lab] += 1
            if lab not in ("relevant", "irrelevant"):
                continue  # exclude uncertain/unreviewed from training loss exports
            if split == "test":
                continue  # sealed
            out = {
                "example_id": r["example_id"],
                "bug_group_id": r["bug_group_id"],
                "repository_id": r["repository_id"],
                "commit_sha": r["commit_sha"],
                "query": r["query"],
                "file_path": r["file_path"],
                "line_range": r.get("line_range"),
                "snippet": r["snippet"],
                "label": lab,
                "label_source": provenance,
                "source_reference": r.get("source_reference"),
                "split": split,
                "pool_hash": r.get("pool_hash"),
                "candidate_origin": r.get("candidate_origin", "natural_retrieval"),
                "annotation_note": r.get("annotation_note") or "",
                "original_label_source_field": r.get("label_source"),
            }
            all_supervised.append(out)
            nk = hashlib.sha256(
                f"{out['query'].strip().lower()}||{out['snippet'][:200].strip().lower()}".encode()
            ).hexdigest()
            near_keys[nk].append(out["example_id"])
            if split == "train":
                train_rows.append(out)
            elif split == "validation":
                val_rows.append(out)

    # Leakage: repo / bug-group must be disjoint across train vs validation
    repo_leak = sorted(repos_by_split["train"] & repos_by_split["validation"])
    bug_leak = sorted(bugs_by_split["train"] & bugs_by_split["validation"])
    dup_snip = [
        {"hash": h, "example_ids": ids}
        for h, ids in near_keys.items()
        if len(ids) > 1
        and len({i.split("::")[0] for i in ids}) > 1
    ]

    exact = {
        sp: {
            "relevant": by_split_raw[sp]["relevant"],
            "irrelevant": by_split_raw[sp]["irrelevant"],
            "uncertain": by_split_raw[sp]["uncertain"],
            "unreviewed": by_split_raw[sp]["unreviewed"],
            "supervised_pairs": by_split_raw[sp]["relevant"] + by_split_raw[sp]["irrelevant"],
            "independent_bugs": len(bugs_by_split[sp]),
            "repositories": sorted(repos_by_split[sp]),
        }
        for sp in ("train", "validation", "test")
    }

    assert exact["train"]["supervised_pairs"] == len(train_rows)
    assert exact["validation"]["supervised_pairs"] == len(val_rows)
    assert exact["test"]["supervised_pairs"] == 0
    assert exact["test"]["unreviewed"] == 150
    assert not repo_leak, f"repo leakage: {repo_leak}"
    assert not bug_leak, f"bug-group leakage: {bug_leak}"

    # Duplicate snippets across splits
    cross_split_dups = []
    train_snips = {
        hashlib.sha256(f"{r['query']}||{r['snippet']}".encode()).hexdigest(): r["example_id"]
        for r in train_rows
    }
    for r in val_rows:
        h = hashlib.sha256(f"{r['query']}||{r['snippet']}".encode()).hexdigest()
        if h in train_snips:
            cross_split_dups.append({"train": train_snips[h], "validation": r["example_id"]})
    assert not cross_split_dups, f"duplicate snippet leakage: {cross_split_dups[:5]}"

    out_dir = root / "p4b" / "datasets"
    out_dir.mkdir(parents=True, exist_ok=True)
    train_path = out_dir / "train.jsonl"
    val_path = out_dir / "validation.jsonl"
    write_jsonl(train_path, train_rows)
    write_jsonl(val_path, val_rows)

    train_v = validate_dataset(train_rows)
    val_v = validate_dataset(val_rows)
    # label_source is agent_generated — validation may still pass if we allow any non-empty source
    audit = {
        "experiment": EXPERIMENT_NAME,
        "pilot_type": "weak_supervision",
        "annotation_provenance": provenance,
        "provenance_note": provenance_note,
        "exact_counts": exact,
        "train_path": str(train_path),
        "validation_path": str(val_path),
        "train_hash": dataset_content_hash(train_rows),
        "validation_hash": dataset_content_hash(val_rows),
        "train_validation": train_v.to_dict(),
        "validation_validation": val_v.to_dict(),
        "leakage": {
            "repository_disjoint": True,
            "bug_group_disjoint": True,
            "cross_split_duplicate_snippets": 0,
            "cross_bug_near_duplicates_within_exports": len(dup_snip),
        },
        "uncertain_excluded_from_training_loss": True,
        "unreviewed_excluded_from_training_loss": True,
        "test_sealed": True,
        "created_at": _utcnow(),
    }
    (root / "p4b" / "audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return audit


def _load_pool_for_bug(iid: str) -> dict[str, Any]:
    return json.loads((_root() / "pools" / f"{iid}.json").read_text(encoding="utf-8"))


def _judgments_for_bug(iid: str) -> dict[str, str]:
    """example_id -> label for pool candidates."""
    out = {}
    for line in (_root() / "reviews" / f"{iid}.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        out[r["example_id"]] = r["label"]
    return out


def _rank_metrics_with_uncertainty(
    ranked_files: list[str],
    judgments_by_file: dict[str, set[str]],
) -> dict[str, Any]:
    """
    judgments_by_file: file -> set of labels seen on its snippets.
    Lower bound: only confirmed relevant files count as relevant.
    Upper bound: confirmed relevant OR any uncertain (optimistic).
    """
    confirmed_rel = {f for f, labs in judgments_by_file.items() if "relevant" in labs}
    optimistic_rel = {
        f
        for f, labs in judgments_by_file.items()
        if "relevant" in labs or "uncertain" in labs
    }
    # Candidates = all files in ranked list (frozen pool aggregation)
    cand = set(ranked_files)

    def pack(relevant: set[str]) -> dict[str, Any]:
        m = file_metrics(ranked_files, relevant, cand)
        return m

    lower = pack(confirmed_rel)
    upper = pack(optimistic_rel)
    return {
        "confirmed_relevant_files": sorted(confirmed_rel),
        "optimistic_relevant_files": sorted(optimistic_rel),
        "lower": lower,
        "upper": upper,
        "has_confirmed_relevant": bool(confirmed_rel),
        "judgment_coverage": {
            "files_with_any_judgment": len(judgments_by_file),
            "files_with_uncertain_only": sorted(
                f
                for f, labs in judgments_by_file.items()
                if labs == {"uncertain"} or (labs <= {"uncertain", "unreviewed"} and "uncertain" in labs)
            ),
        },
    }


def _device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def _score_ce(model, query: str, snippets: list[str]) -> list[float]:
    pairs = [[query, s] for s in snippets]
    raw = model.predict(pairs, show_progress_bar=False)
    return [float(x) for x in raw]


def evaluate_validation_configs(
    *,
    finetuned_path: str | None = None,
    cold_load: bool = True,
) -> dict[str, Any]:
    """Hybrid vs base CE vs optional finetuned on frozen validation pools."""
    from sentence_transformers import CrossEncoder

    assigns = _assignments()
    val_bugs = [iid for iid, sp in assigns.items() if sp == "validation"]
    configs = ["hybrid", "hybrid_plus_base_ce"]
    if finetuned_path and Path(finetuned_path).exists():
        configs.append("hybrid_plus_finetuned_ce")

    cold_ms = None
    models: dict[str, Any] = {}
    if cold_load:
        t0 = time.perf_counter()
        models["base"] = CrossEncoder(BASE_MODEL, max_length=MAX_LENGTH)
        cold_ms = (time.perf_counter() - t0) * 1000
    else:
        models["base"] = CrossEncoder(BASE_MODEL, max_length=MAX_LENGTH)
    if finetuned_path and Path(finetuned_path).exists():
        models["ft"] = CrossEncoder(finetuned_path, max_length=MAX_LENGTH)

    per_bug: list[dict[str, Any]] = []
    warm_lat: dict[str, list[float]] = defaultdict(list)
    incomplete: list[dict[str, Any]] = []

    for iid in sorted(val_bugs):
        pool = _load_pool_for_bug(iid)
        case = json.loads((_root() / "cases" / f"{iid}.json").read_text())
        query = case["query"]
        cands = pool["candidates"]
        judgments = _judgments_for_bug(iid)
        # map chunk example_id style: f"{iid}::{chunk_id}"
        by_file_labels: dict[str, set[str]] = defaultdict(set)
        for c in cands:
            eid = f"{iid}::{c['chunk_id']}"
            lab = judgments.get(eid, "unreviewed")
            by_file_labels[c["file_path"]].add(lab)

        # hybrid order = frozen hybrid_rank
        hybrid_ranked = sorted(cands, key=lambda c: int(c.get("hybrid_rank") or 0))

        def eval_order(ordered: list[dict], cfg: str, rerank_ms: float) -> None:
            files = aggregate_chunk_to_file(ordered)
            metrics = _rank_metrics_with_uncertainty(files, by_file_labels)
            per_bug.append(
                {
                    "bug_group_id": iid,
                    "repository_id": case["repository_id"],
                    "config": cfg,
                    "ranked_files": files,
                    "metrics": metrics,
                    "rerank_ms": rerank_ms,
                    "retrieval_miss_proxy": not pool["failure_flags"]["relevant_in_candidate_pool"],
                    "no_confirmed_relevant_in_pool": not metrics["has_confirmed_relevant"],
                }
            )
            warm_lat[cfg].append(rerank_ms)
            if not metrics["has_confirmed_relevant"]:
                incomplete.append(
                    {
                        "bug_group_id": iid,
                        "config": cfg,
                        "reason": (
                            "confirmed_retrieval_miss_or_no_positive_in_pool"
                            if pool["failure_flags"]["relevant_in_candidate_pool"] is False
                            or not pool.get("relevant_in_top50")
                            else "incomplete_or_no_confirmed_relevant_labels"
                        ),
                        "missing_from_top50": pool.get("relevant_missing_from_top50"),
                    }
                )

        eval_order(hybrid_ranked, "hybrid", 0.0)

        snippets = [c["snippet"] for c in cands]
        t1 = time.perf_counter()
        scores = _score_ce(models["base"], query, snippets)
        rr = (time.perf_counter() - t1) * 1000
        order = sorted(range(len(cands)), key=lambda i: scores[i], reverse=True)
        eval_order([cands[i] for i in order], "hybrid_plus_base_ce", rr)

        if "ft" in models:
            t2 = time.perf_counter()
            scores_ft = _score_ce(models["ft"], query, snippets)
            rr2 = (time.perf_counter() - t2) * 1000
            order2 = sorted(range(len(cands)), key=lambda i: scores_ft[i], reverse=True)
            eval_order([cands[i] for i in order2], "hybrid_plus_finetuned_ce", rr2)

    def agg(cfg: str, bound: str) -> dict[str, Any]:
        rows = [b for b in per_bug if b["config"] == cfg]
        # Only bugs with confirmed relevant for primary Hit/MRR; still report coverage
        usable = [b for b in rows if b["metrics"]["has_confirmed_relevant"]]
        n = len(usable) or 1

        def avg(key: str) -> float:
            return sum(float(b["metrics"][bound][key]) for b in usable) / n if usable else 0.0

        lats = warm_lat[cfg]
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
        }

    aggregates = []
    for cfg in configs:
        aggregates.append(agg(cfg, "lower"))
        aggregates.append(agg(cfg, "upper"))

    return {
        "split": "validation",
        "cold_load_ms_base_ce": cold_ms,
        "hardware": {"device": _device(), "platform": platform.platform()},
        "aggregates": aggregates,
        "per_bug": per_bug,
        "incomplete_bugs": incomplete,
        "retrieval_config": RETRIEVAL_CONFIG,
        "created_at": _utcnow(),
    }


def train_pilot(audit: dict[str, Any]) -> dict[str, Any]:
    """Bounded CE fine-tune; select by validation loss; epoch 0 = unmodified."""
    import numpy as np
    import torch
    from torch import nn
    from torch.utils.data import DataLoader
    from sentence_transformers import CrossEncoder, InputExample

    train_path = Path(audit["train_path"])
    val_path = Path(audit["validation_path"])
    train_rows = [json.loads(l) for l in train_path.read_text().splitlines() if l.strip()]
    val_rows = [json.loads(l) for l in val_path.read_text().splitlines() if l.strip()]

    n_pos = sum(1 for r in train_rows if r["label"] == "relevant")
    n_neg = sum(1 for r in train_rows if r["label"] == "irrelevant")
    pos_weight_val = float(n_neg / max(n_pos, 1))

    device = _device()
    # Batch size for available hardware
    batch_size = 16 if device == "cpu" else 32
    if device == "mps":
        batch_size = 16

    out = _root() / "p4b" / "checkpoints" / "pilot_v1"
    out.mkdir(parents=True, exist_ok=True)

    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)

    model = CrossEncoder(BASE_MODEL, num_labels=1, max_length=MAX_LENGTH)
    # Snapshot epoch-0 weights hash
    epoch0_dir = out / "epoch0_pretrained"
    model.save(str(epoch0_dir))
    epoch0_hash = checkpoint_hash_for_path(str(epoch0_dir))

    def pair_loss(rows: list[dict]) -> float:
        if not rows:
            return 0.0
        model.model.eval()
        total = 0.0
        n = 0
        pw = torch.tensor([pos_weight_val], dtype=torch.float32)
        loss_fct = nn.BCEWithLogitsLoss(pos_weight=pw)
        with torch.no_grad():
            for i in range(0, len(rows), batch_size):
                batch = rows[i : i + batch_size]
                texts = [[r["query"], r["snippet"]] for r in batch]
                labels = torch.tensor(
                    [[1.0 if r["label"] == "relevant" else 0.0] for r in batch],
                    dtype=torch.float32,
                )
                # tokenize via model
                features = model.tokenizer(
                    [t[0] for t in texts],
                    [t[1] for t in texts],
                    padding=True,
                    truncation="longest_first",
                    return_tensors="pt",
                    max_length=MAX_LENGTH,
                )
                features = {k: v.to(model.model.device) for k, v in features.items()}
                logits = model.model(**features, return_dict=True).logits
                labels = labels.to(logits.device)
                # pos_weight device
                loss_fct = nn.BCEWithLogitsLoss(
                    pos_weight=torch.tensor([pos_weight_val], device=logits.device)
                )
                loss = loss_fct(logits.view(-1), labels.view(-1))
                total += float(loss.item()) * len(batch)
                n += len(batch)
        return total / max(n, 1)

    val_loss_epoch0 = pair_loss(val_rows)
    history = [{"epoch": 0, "val_loss": val_loss_epoch0, "path": str(epoch0_dir), "hash": epoch0_hash}]

    examples = [
        InputExample(
            texts=[r["query"], r["snippet"]],
            label=1.0 if r["label"] == "relevant" else 0.0,
        )
        for r in train_rows
    ]
    loader = DataLoader(examples, shuffle=True, batch_size=batch_size)

    class _DeviceAwareBCE(nn.Module):
        def __init__(self, pw: float) -> None:
            super().__init__()
            self.pw = float(pw)

        def forward(self, logits, labels):  # noqa: ANN001
            loss = nn.BCEWithLogitsLoss(
                pos_weight=torch.tensor(
                    [self.pw], device=logits.device, dtype=logits.dtype
                )
            )
            return loss(logits.view(-1), labels.view(-1))

    loss_fct = _DeviceAwareBCE(pos_weight_val)

    t0 = time.time()
    steps_per_epoch = len(loader)
    # Train epoch-by-epoch to record val loss and keep best
    best = history[0]
    for epoch in range(1, MAX_EPOCHS + 1):
        model.fit(
            train_dataloader=loader,
            epochs=1,
            warmup_steps=max(1, int(0.1 * steps_per_epoch)) if epoch == 1 else 0,
            optimizer_params={"lr": LR},
            loss_fct=loss_fct,
            show_progress_bar=True,
            use_amp=(device == "cuda"),
            output_path=None,
        )
        ep_dir = out / f"epoch{epoch}"
        model.save(str(ep_dir))
        vloss = pair_loss(val_rows)
        entry = {
            "epoch": epoch,
            "val_loss": vloss,
            "path": str(ep_dir),
            "hash": checkpoint_hash_for_path(str(ep_dir)),
        }
        history.append(entry)
        if vloss < best["val_loss"]:
            best = entry

    duration = time.time() - t0
    import shutil

    selected_dir = out / "selected"
    if selected_dir.exists():
        shutil.rmtree(selected_dir)
    shutil.copytree(best["path"], selected_dir)
    # Confirm weights changed vs epoch0 (unless best is epoch0)
    weights_changed = best["hash"] != epoch0_hash

    meta = {
        "status": "training_completed",
        "base_model": BASE_MODEL,
        "seed": SEED,
        "max_epochs": MAX_EPOCHS,
        "lr": LR,
        "batch_size": batch_size,
        "max_length": MAX_LENGTH,
        "truncation_policy": TRUNCATION_POLICY,
        "loss": "BCEWithLogitsLoss",
        "pos_weight_from_train_only": pos_weight_val,
        "train_pos": n_pos,
        "train_neg": n_neg,
        "val_supervised": len(val_rows),
        "steps_per_epoch": steps_per_epoch,
        "total_optimizer_steps": steps_per_epoch * MAX_EPOCHS,
        "device": device,
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "duration_sec": duration,
        "history": history,
        "selected_epoch": best["epoch"],
        "selected_val_loss": best["val_loss"],
        "selected_path": str(selected_dir),
        "selected_checkpoint_hash": best["hash"],  # hash of epoch dir before sidecar meta
        "epoch0_hash": epoch0_hash,
        "weights_changed_vs_pretrained": weights_changed,
        "selection_rule": "minimize validation BCE on judged relevant/irrelevant pairs; epoch0=unmodified included",
        "train_hash": audit["train_hash"],
        "validation_hash": audit["validation_hash"],
        "annotation_provenance": audit["annotation_provenance"],
        "experimental": True,
        "activated": False,
        "created_at": _utcnow(),
    }
    (out / "training_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    # Sidecar outside weight tree so directory hash of epochN stays stable
    (out / "selected_pointer.json").write_text(
        json.dumps(
            {
                "selected_epoch": best["epoch"],
                "epoch_path": best["path"],
                "checkpoint_hash": best["hash"],
                "selected_copy": str(selected_dir),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return meta


def verify_checkpoint(meta: dict[str, Any]) -> dict[str, Any]:
    """Reload selected weights + confirm hash against the chosen epoch directory."""
    from sentence_transformers import CrossEncoder

    path = meta["selected_path"]
    epoch_path = next(
        (h["path"] for h in meta["history"] if h["epoch"] == meta["selected_epoch"]),
        path,
    )
    t0 = time.perf_counter()
    model = CrossEncoder(path, max_length=MAX_LENGTH)
    load_ms = (time.perf_counter() - t0) * 1000
    scores = model.predict(
        [["bug in login case", "def normalize_username(u): return u.lower()"]],
        show_progress_bar=False,
    )
    epoch_hash = checkpoint_hash_for_path(epoch_path)
    return {
        "reload_ok": True,
        "inference_ok": True,
        "sample_score": float(scores[0]),
        "reload_ms": load_ms,
        "checkpoint_hash_matches": epoch_hash == meta["selected_checkpoint_hash"],
        "checkpoint_hash": epoch_hash,
        "verified_epoch_path": epoch_path,
    }


def compare_and_report(audit: dict[str, Any], train_meta: dict[str, Any], verify: dict[str, Any]) -> dict[str, Any]:
    baseline = evaluate_validation_configs(finetuned_path=None, cold_load=True)
    with_ft = evaluate_validation_configs(
        finetuned_path=train_meta["selected_path"], cold_load=False
    )

    def lower_map(report: dict) -> dict[str, dict]:
        return {
            a["config"]: a
            for a in report["aggregates"]
            if a["bound"] == "lower"
        }

    b0 = lower_map(baseline)
    b1 = lower_map(with_ft)

    # Per-bug improve/worsen: compare base CE vs FT on MRR lower bound for bugs with confirmed relevant
    base_bug = {
        (p["bug_group_id"]): p
        for p in with_ft["per_bug"]
        if p["config"] == "hybrid_plus_base_ce"
    }
    ft_bug = {
        (p["bug_group_id"]): p
        for p in with_ft["per_bug"]
        if p["config"] == "hybrid_plus_finetuned_ce"
    }
    improved, worsened, unchanged = [], [], []
    for iid, bp in base_bug.items():
        if not bp["metrics"]["has_confirmed_relevant"]:
            continue
        fp = ft_bug.get(iid)
        if not fp:
            continue
        dm = fp["metrics"]["lower"]["mrr_at_10"] - bp["metrics"]["lower"]["mrr_at_10"]
        entry = {"bug_group_id": iid, "delta_mrr_at_10": dm}
        if dm > 1e-9:
            improved.append(entry)
        elif dm < -1e-9:
            worsened.append(entry)
        else:
            unchanged.append(entry)

    report = {
        "status": train_meta["status"],
        "experimental": True,
        "activated": False,
        "pilot_type": "weak_supervision",
        "annotation_provenance": audit["annotation_provenance"],
        "provenance_note": audit["provenance_note"],
        "exact_split_counts": audit["exact_counts"],
        "checkpoint_hash": train_meta["selected_checkpoint_hash"],
        "selected_epoch": train_meta["selected_epoch"],
        "weights_changed_vs_pretrained": train_meta["weights_changed_vs_pretrained"],
        "verify": verify,
        "baselines_validation_lower_bound": b0,
        "with_finetuned_validation_lower_bound": {
            k: v for k, v in b1.items()
        },
        "cold_load_ms": baseline.get("cold_load_ms_base_ce"),
        "improved_bugs": improved,
        "worsened_bugs": worsened,
        "unchanged_bugs": unchanged,
        "incomplete_judgment_or_retrieval": with_ft.get("incomplete_bugs"),
        "uncertainty_note": (
            "Primary reported metrics use lower bound (confirmed relevant only). "
            "Uncertain snippets are never treated as irrelevant in ranked lists; "
            "upper bound treats uncertain files as optionally relevant."
        ),
        "test_sealed": True,
        "application_default_remains": "hybrid_retrieval (RERANKER_MODE=disabled)",
        "recommendation": (
            "Keep fine-tuned checkpoint experimental. Three validation bugs cannot "
            "establish reliable generalization. Freeze model+rules before any test scoring."
        ),
        "created_at": _utcnow(),
        "training_meta": train_meta,
        "full_baseline_report": baseline,
        "full_ft_report": with_ft,
    }
    dest = _root() / "p4b" / "reports" / "pilot_final.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2), encoding="utf-8")
    # Markdown summary
    md = [
        "# P4B pilot report (weak supervision)",
        "",
        f"- Status: **{report['status']}**",
        f"- Checkpoint hash: `{report['checkpoint_hash']}`",
        f"- Selected epoch: {report['selected_epoch']} (0 = unmodified pretrained)",
        f"- Weights changed vs pretrained: {report['weights_changed_vs_pretrained']}",
        f"- Activated: **false** (experimental)",
        f"- Provenance: `{report['annotation_provenance']}`",
        "",
        "## Exact split counts",
        "",
        "```json",
        json.dumps(report["exact_split_counts"], indent=2),
        "```",
        "",
        "## Validation lower-bound aggregates (confirmed relevant only)",
        "",
    ]
    for name, block in [
        ("baselines", report["baselines_validation_lower_bound"]),
        ("with_finetuned", report["with_finetuned_validation_lower_bound"]),
    ]:
        md.append(f"### {name}")
        for cfg, a in block.items():
            md.append(
                f"- `{cfg}`: Hit@1={a['hit_at_1']:.3f} Hit@5={a['hit_at_5']:.3f} "
                f"MRR@10={a['mrr_at_10']:.3f} (n_bugs_with_pos={a['bugs_with_confirmed_relevant']}/{a['bugs_total']})"
            )
        md.append("")
    md += [
        f"Improved: {len(improved)} · Worsened: {len(worsened)} · Unchanged: {len(unchanged)}",
        "",
        report["recommendation"],
        "",
    ]
    (dest.with_suffix(".md")).write_text("\n".join(md), encoding="utf-8")
    return report


def run_p4b_pilot() -> dict[str, Any]:
    audit = audit_and_export()
    train_meta = train_pilot(audit)
    verify = verify_checkpoint(train_meta)
    report = compare_and_report(audit, train_meta, verify)
    return {
        "audit": audit,
        "training": train_meta,
        "verify": verify,
        "report_path": str(_root() / "p4b" / "reports" / "pilot_final.json"),
        "status": train_meta["status"],
        "checkpoint_hash": train_meta["selected_checkpoint_hash"],
        "experimental": True,
        "activated": False,
    }
