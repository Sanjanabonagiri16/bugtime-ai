"""Reproducible P4 commands: validate, baseline-eval, train (P4B).

Examples:
  python -m app.scripts.rerank_cli validate --dataset data/rerank/datasets/login_smoke_v1.jsonl
  python -m app.scripts.rerank_cli baseline --dataset data/rerank/datasets/login_smoke_v1.jsonl
  python -m app.scripts.rerank_cli train --dataset ... --output data/rerank/checkpoints/run1
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from app.services.rerank_dataset import (
    load_jsonl,
    split_manifest,
    supervised_rows,
    validate_dataset,
)
from app.services.rerank_eval import run_baseline_eval, save_eval_report
from app.services.reranker import BASE_MODEL


def cmd_validate(args: argparse.Namespace) -> int:
    rows = load_jsonl(Path(args.dataset))
    result = validate_dataset(rows)
    print(json.dumps(result.to_dict(), indent=2))
    man = split_manifest(rows)
    if args.manifest_out:
        Path(args.manifest_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.manifest_out).write_text(json.dumps(man, indent=2), encoding="utf-8")
    return 0 if result.ok else 1


def cmd_baseline(args: argparse.Namespace) -> int:
    report = run_baseline_eval(
        Path(args.dataset),
        split=args.split,
        candidate_limit=args.candidate_limit,
        finetuned_path=args.finetuned_path,
        label=args.label,
    )
    out = Path(args.out_dir)
    saved = save_eval_report(out, report)
    print(json.dumps({"report_path": str(saved), **report.to_dict()}, indent=2)[:4000])
    print("...", flush=True)
    print(f"Saved {saved}")
    return 0


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


def cmd_train(args: argparse.Namespace) -> int:
    rows = load_jsonl(Path(args.dataset))
    v = validate_dataset(rows)
    if not v.ok:
        print(json.dumps(v.to_dict(), indent=2))
        return 1
    train = supervised_rows(rows, split="train")
    val = supervised_rows(rows, split="validation")
    if len(train) < 8:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "insufficient_train_data",
                    "train_count": len(train),
                    "validation_count": len(val),
                    "message": (
                        "P4B fine-tuning requires labeled train examples. "
                        "Current inventory does not support a meaningful train run."
                    ),
                    "device": _device(),
                    "commands_retained": True,
                },
                indent=2,
            )
        )
        return 2

    seed = args.seed
    random.seed(seed)
    device = _device()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    from sentence_transformers import CrossEncoder, InputExample
    from sentence_transformers.cross_encoder.evaluation import CEBinaryClassificationEvaluator
    from torch.utils.data import DataLoader

    t0 = time.time()
    model = CrossEncoder(args.base_model, num_labels=1, max_length=512)
    examples = [
        InputExample(
            texts=[r["query"], r["snippet"]],
            label=1.0 if r["label"] == "relevant" else 0.0,
        )
        for r in train
    ]
    loader = DataLoader(examples, shuffle=True, batch_size=args.batch_size)
    evaluator = None
    if val:
        evaluator = CEBinaryClassificationEvaluator(
            sentence_pairs=[[r["query"], r["snippet"]] for r in val],
            labels=[1 if r["label"] == "relevant" else 0 for r in val],
            name="val",
        )

    warmup = max(1, int(len(loader) * args.epochs * 0.1))
    model.fit(
        train_dataloader=loader,
        evaluator=evaluator,
        epochs=args.epochs,
        warmup_steps=warmup,
        output_path=str(out),
        show_progress_bar=True,
        use_amp=device == "cuda",
    )
    duration = time.time() - t0
    meta = {
        "dataset_hash": v.dataset_hash,
        "split_manifest": split_manifest(rows),
        "base_model": args.base_model,
        "seed": seed,
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "lr_default": "CrossEncoder.fit default",
        "device": device,
        "platform": platform.platform(),
        "python": sys.version,
        "duration_sec": duration,
        "train_count": len(train),
        "validation_count": len(val),
        "checkpoint_path": str(out),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "selected_by": "validation_evaluator" if evaluator else "final_epoch",
    }
    (out / "training_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="BugTime P4 reranker CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate")
    v.add_argument("--dataset", required=True)
    v.add_argument("--manifest-out", default=None)
    v.set_defaults(func=cmd_validate)

    b = sub.add_parser("baseline")
    b.add_argument("--dataset", required=True)
    b.add_argument("--split", default="test")
    b.add_argument("--candidate-limit", type=int, default=50)
    b.add_argument("--finetuned-path", default=None)
    b.add_argument("--label", default="synthetic_smoke")
    b.add_argument("--out-dir", default="./data")
    b.set_defaults(func=cmd_baseline)

    t = sub.add_parser("train")
    t.add_argument("--dataset", required=True)
    t.add_argument("--output", required=True)
    t.add_argument("--base-model", default=BASE_MODEL)
    t.add_argument("--epochs", type=int, default=1)
    t.add_argument("--batch-size", type=int, default=8)
    t.add_argument("--seed", type=int, default=42)
    t.set_defaults(func=cmd_train)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
