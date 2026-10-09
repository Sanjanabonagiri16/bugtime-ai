"""P4 Evaluations API: datasets, baseline eval, activation control."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from app.core.config import get_settings
from app.schemas.evaluation import ActivateRequest, DatasetSummary, EvalRunRequest, RerankerStatus
from app.services.rerank_dataset import (
    default_datasets_dir,
    list_dataset_files,
    load_jsonl,
    split_manifest,
    validate_dataset,
    write_jsonl,
)
from app.services.rerank_eval import (
    list_eval_reports,
    load_eval_report,
    run_baseline_eval,
    save_eval_report,
)
from app.services.reranker import BASE_MODEL, resolve_model_path

router = APIRouter(tags=["evaluations"])


def _data_dir() -> Path:
    return Path(get_settings().data_dir)


@router.get("/evaluations/datasets", response_model=list[DatasetSummary])
def list_datasets():
    out: list[DatasetSummary] = []
    for path in list_dataset_files(_data_dir()):
        rows = load_jsonl(path)
        v = validate_dataset(rows)
        label = "synthetic_smoke" if "smoke" in path.name or "synthetic" in path.name else "user"
        out.append(
            DatasetSummary(
                name=path.name,
                path=str(path),
                example_count=v.example_count,
                labeled_count=v.labeled_count,
                unreviewed_count=v.unreviewed_count,
                split_counts=v.split_counts,
                bug_group_count=v.bug_group_count,
                repository_count=v.repository_count,
                dataset_hash=v.dataset_hash,
                split_strategy=v.split_strategy,
                ok=v.ok,
                notes=v.notes,
                issues=[{"code": i.code, "message": i.message, "example_id": i.example_id} for i in v.issues],
                provenance_label=label,
            )
        )
    return out


@router.post("/evaluations/datasets/import", response_model=DatasetSummary)
async def import_dataset(file: UploadFile = File(...), name: str | None = None):
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, detail={"error": {"code": "invalid_jsonl", "message": str(exc)}}) from exc
    v = validate_dataset(rows)
    if not v.ok:
        raise HTTPException(
            400,
            detail={
                "error": {
                    "code": "dataset_invalid",
                    "message": "Dataset failed validation",
                    "field_errors": None,
                    "issues": [
                        {"code": i.code, "message": i.message, "example_id": i.example_id}
                        for i in v.issues
                    ],
                }
            },
        )
    fname = name or file.filename or "import.jsonl"
    if not fname.endswith(".jsonl"):
        fname += ".jsonl"
    dest = default_datasets_dir(_data_dir()) / Path(fname).name
    write_jsonl(dest, rows)
    manifest = split_manifest(rows)
    man_path = _data_dir() / "rerank" / "manifests" / f"{dest.stem}.json"
    man_path.parent.mkdir(parents=True, exist_ok=True)
    man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return DatasetSummary(
        name=dest.name,
        path=str(dest),
        example_count=v.example_count,
        labeled_count=v.labeled_count,
        unreviewed_count=v.unreviewed_count,
        split_counts=v.split_counts,
        bug_group_count=v.bug_group_count,
        repository_count=v.repository_count,
        dataset_hash=v.dataset_hash,
        split_strategy=v.split_strategy,
        ok=v.ok,
        notes=v.notes,
        issues=[],
        provenance_label="user",
    )


@router.get("/evaluations/datasets/{name}/export")
def export_dataset(name: str):
    path = default_datasets_dir(_data_dir()) / Path(name).name
    if not path.exists():
        raise HTTPException(404, detail="Dataset not found")
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="application/x-ndjson")


@router.post("/evaluations/runs")
def start_eval_run(body: EvalRunRequest):
    path = default_datasets_dir(_data_dir()) / Path(body.dataset_name).name
    if not path.exists():
        raise HTTPException(404, detail="Dataset not found")
    settings = get_settings()
    ft = body.finetuned_path or (settings.reranker_model_path or None)
    report = run_baseline_eval(
        path,
        split=body.split,
        candidate_limit=settings.reranker_candidate_limit,
        finetuned_path=ft,
        label=body.label,
    )
    saved = save_eval_report(_data_dir(), report)
    data = report.to_dict()
    data["id"] = saved.stem
    return data


@router.get("/evaluations/runs")
def get_eval_runs():
    return list_eval_reports(_data_dir())


@router.get("/evaluations/runs/{run_id}")
def get_eval_run(run_id: str):
    data = load_eval_report(_data_dir(), run_id)
    if not data:
        raise HTTPException(404, detail="Eval run not found")
    return data


@router.get("/evaluations/runs/{run_id}/report.md", response_class=PlainTextResponse)
def download_report_md(run_id: str):
    data = load_eval_report(_data_dir(), run_id)
    if not data:
        raise HTTPException(404, detail="Eval run not found")
    lines = [
        f"# Reranker evaluation `{run_id}`",
        "",
        f"- Label: **{data.get('label')}** (show real results only; synthetic/smoke must be labeled)",
        f"- Dataset hash: `{data.get('dataset_hash')}`",
        f"- Split: `{data.get('split')}` / strategy: `{data.get('split_strategy')}`",
        "",
        "## Configurations",
        "",
        "| config | bugs | recall | Hit@1 | Hit@5 | MRR@10 | med ms | p95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for c in data.get("configs") or []:
        lines.append(
            f"| {c['config']} | {c['bug_count']} | {c['candidate_recall']:.3f} | "
            f"{c['hit_at_1']:.3f} | {c['hit_at_5']:.3f} | {c['mrr_at_10']:.3f} | "
            f"{c['latency_ms_median']:.1f} | {c['latency_ms_p95']:.1f} |"
        )
    lines += ["", "## Limitations", ""]
    for lim in data.get("limitations") or []:
        lines.append(f"- {lim}")
    lines += ["", "## Per-bug", ""]
    for b in data.get("per_bug") or []:
        lines.append(
            f"- `{b['bug_group_id']}` / {b['config']}: Hit@1={b['hit_at_1']} "
            f"MRR@10={b['mrr_at_10']} missed={b.get('missed_relevant')}"
        )
    return "\n".join(lines) + "\n"


@router.get("/evaluations/reranker", response_model=RerankerStatus)
def reranker_status():
    settings = get_settings()
    mode, path = resolve_model_path()
    ft_path = settings.reranker_model_path or ""
    return RerankerStatus(
        mode=settings.reranker_mode,
        model_path=settings.reranker_model_path or None,
        candidate_limit=settings.reranker_candidate_limit,
        base_model=BASE_MODEL,
        finetuned_exists=bool(ft_path and Path(ft_path).exists()),
        experimental=True,
    )


@router.get("/evaluations/inventory")
def inventory():
    """Report whether available data supports a meaningful P4B experiment."""
    datasets = list_datasets()
    total_bugs = sum(d.bug_group_count for d in datasets)
    total_repos = sum(d.repository_count for d in datasets)
    supports_p4b = total_bugs >= 3 and any(d.split_counts.get("train", 0) >= 20 for d in datasets)
    return {
        "independent_bug_groups": total_bugs,
        "repositories": total_repos,
        "datasets": [
            {
                "name": d.name,
                "bug_groups": d.bug_group_count,
                "repos": d.repository_count,
                "split_counts": d.split_counts,
                "split_strategy": d.split_strategy,
                "label": d.provenance_label,
                "notes": d.notes,
            }
            for d in datasets
        ],
        "supports_meaningful_p4b": supports_p4b,
        "recommendation": (
            "Proceed to P4B when ≥3 independent bug groups and a labeled train split exist."
            if not supports_p4b
            else "Dataset scale may support an experimental fine-tune; keep promotion gated."
        ),
        "login_demo_note": (
            "The login demo is a smoke test. One successful repair does not demonstrate generalization."
        ),
    }


@router.post("/evaluations/activate")
def activate_checkpoint(body: ActivateRequest):
    """
    Persist activation intent to data/rerank/active.json.
    Runtime mode still comes from env (RERANKER_MODE); this records operator choice for UI.
    Promotion remains experimental — does not rewrite historical evidence.
    """
    settings = get_settings()
    if body.mode == "finetuned":
        path = body.model_path or settings.reranker_model_path
        if not path or not Path(path).exists():
            raise HTTPException(400, detail="Finetuned checkpoint path missing or unavailable")
    active = {
        "mode": body.mode,
        "model_path": body.model_path or settings.reranker_model_path or None,
        "experimental": True,
        "note": "Set RERANKER_MODE / RERANKER_MODEL_PATH in environment to apply at runtime.",
    }
    dest = _data_dir() / "rerank" / "active.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(active, indent=2), encoding="utf-8")
    return active


@router.post("/evaluations/seed-smoke-dataset")
def seed_smoke_dataset():
    """Copy bundled login_smoke_v1 into DATA_DIR if missing."""
    dest_dir = default_datasets_dir(_data_dir())
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "login_smoke_v1.jsonl"
    candidates = [
        Path(__file__).resolve().parents[3] / "data" / "rerank" / "datasets" / "login_smoke_v1.jsonl",
        Path("/app/seed/rerank/datasets/login_smoke_v1.jsonl"),
        Path(__file__).resolve().parents[4] / "data" / "rerank" / "datasets" / "login_smoke_v1.jsonl",
    ]
    src = next((p for p in candidates if p.exists()), None)
    if src is None and not dest.exists():
        raise HTTPException(404, detail="Bundled smoke dataset not found in image/workspace")
    if src and (not dest.exists() or dest.stat().st_size == 0):
        shutil.copy2(src, dest)
    rows = load_jsonl(dest)
    v = validate_dataset(rows)
    return {"path": str(dest), "validation": v.to_dict()}


# --- P4A.1 SWE-bench review queue ---


@router.get("/evaluations/review/cases")
def review_list_cases(include_test: bool = False):
    from app.services import dataset_review

    return dataset_review.list_cases(include_test=include_test)


@router.get("/evaluations/review/cases/{instance_id}")
def review_get_case(instance_id: str):
    from app.services import dataset_review
    from app.services.dataset_review import _split_assignments

    # Block accidental exposure of held-out test during tuning workflows
    split = _split_assignments().get(instance_id)
    bundle = dataset_review.load_case_bundle(instance_id)
    if not bundle:
        raise HTTPException(404, detail="Case not found")
    bundle["split"] = split
    bundle["items_by_file"] = _group_items_by_file(bundle.get("items") or [])
    return bundle


def _group_items_by_file(items: list) -> list[dict]:
    groups: dict[str, list] = {}
    order: list[str] = []
    for it in items:
        fp = it.get("file_path") or ""
        if fp not in groups:
            groups[fp] = []
            order.append(fp)
        groups[fp].append(it)
    return [{"file_path": fp, "items": groups[fp]} for fp in order]


@router.post("/evaluations/review/cases/{instance_id}")
def review_save_case(instance_id: str, body: dict):
    from app.services import dataset_review

    updates = body.get("updates") or []
    try:
        result = dataset_review.save_annotations(instance_id, updates)
        result["progress"] = dataset_review.annotation_progress()
        return result
    except FileNotFoundError as exc:
        raise HTTPException(404, detail="Case not found") from exc


@router.get("/evaluations/review/coverage")
def review_coverage():
    from app.services import dataset_review
    from app.services.swebench_import import coverage_report, experiment_root

    root = experiment_root(_data_dir())
    report_path = root / "reports" / "coverage.json"
    base = (
        json.loads(report_path.read_text(encoding="utf-8"))
        if report_path.exists()
        else coverage_report(root)
    )
    base["annotation_progress"] = dataset_review.annotation_progress()
    return base


@router.get("/evaluations/review/progress")
def review_progress():
    from app.services import dataset_review

    return dataset_review.annotation_progress()


@router.get("/evaluations/review/batch-report")
def review_batch_report(ids: str = ""):
    from app.services import dataset_review

    default = [
        "marshmallow-code__marshmallow-1229",
        "pvlib__pvlib-python-1026",
        "pydicom__pydicom-1000",
    ]
    instance_ids = [x for x in ids.split(",") if x] or default
    return dataset_review.batch_report(instance_ids)


@router.post("/evaluations/review/export")
def review_export():
    from app.services import dataset_review

    return dataset_review.export_reviewed_dataset()


@router.post("/evaluations/review/baseline")
def review_baseline():
    from app.services import dataset_review

    return dataset_review.run_reviewed_baselines()


@router.post("/evaluations/review/collect")
def review_collect(body: dict | None = None):
    """Kick off / resume SWE-bench collection (may take a long time)."""
    from app.services.swebench_import import run_collection

    body = body or {}
    return run_collection(
        target_n=int(body.get("target_n") or 20),
        min_repos=int(body.get("min_repos") or 4),
        candidate_limit=int(body.get("candidate_limit") or 50),
        data_dir=_data_dir(),
        limit_cases=body.get("limit"),
    )
