"""P4A.1 SWE-bench development-split importer and review queue builder."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.offline_hybrid import (
    RETRIEVAL_CONFIG,
    hybrid_retrieve,
    index_checkout,
    pool_hash,
)

# Pinned HF dataset revision (Upload dataset commit, Mar 2025).
SWE_BENCH_DATASET = "princeton-nlp/SWE-bench"
SWE_BENCH_REVISION = "e48e2bd1e9fecd5bbd641e9414ac59da9f2e69f6"
SWE_BENCH_SPLIT = "dev"  # official test split preserved for future eval
DATASET_LICENSE_NOTE = (
    "SWE-bench (princeton-nlp/SWE-bench) is MIT-licensed; upstream GitHub repos "
    "retain their own licenses. Issue text and patches are used for research "
    "labeling only. hints_text and patches never enter retrieval queries."
)

EXPERIMENT_NAME = "swebench_dev_p4a1"
HINTS_EXCLUSION = "hints_text excluded from queries and model inputs"
SOLUTION_SECTION_EXCLUSION = "solution-bearing sections stripped from problem_statement when detected"


@dataclass
class Exclusion:
    instance_id: str
    repo: str
    reason: str


def experiment_root(data_dir: Path | None = None) -> Path:
    root = Path(data_dir or get_settings().data_dir) / "rerank" / "experiments" / EXPERIMENT_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_patch_files(patch: str) -> list[str]:
    files: list[str] = []
    for line in (patch or "").splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                b = parts[3]
                if b.startswith("b/"):
                    b = b[2:]
                files.append(b)
    return files


def strip_solution_sections(text: str) -> tuple[str, list[str]]:
    """Remove obvious solution-bearing blocks from issue text."""
    exclusions: list[str] = []
    if not text:
        return "", exclusions
    patterns = [
        re.compile(r"(?is)\n+#+\s*solution\b.*"),
        re.compile(r"(?is)\n+proposed\s+fix\s*:.*"),
        re.compile(r"(?is)\n+here\s+is\s+(the\s+)?(fix|patch)\b.*"),
    ]  # noqa: intentional whitespace patterns
    out = text
    for pat in patterns:
        if pat.search(out):
            exclusions.append(SOLUTION_SECTION_EXCLUSION)
            out = pat.sub("\n", out)
    return out.strip(), list(dict.fromkeys(exclusions))


def focused_python_score(row: dict[str, Any]) -> tuple[float, str | None]:
    """Higher is better. Return (score, exclude_reason)."""
    patch = row.get("patch") or ""
    files = parse_patch_files(patch)
    if not files:
        return -1.0, "empty_patch_files"
    py_files = [f for f in files if f.endswith(".py")]
    if not py_files:
        return -1.0, "no_python_files_in_patch"
    code_py = [f for f in py_files if "/test" not in f.replace("\\", "/") and not Path(f).name.startswith("test_")]
    if not code_py:
        return -1.0, "python_changes_only_in_tests"
    if len(files) > 8:
        return -1.0, f"too_many_changed_files:{len(files)}"
    ps = (row.get("problem_statement") or "").strip()
    if len(ps) < 40:
        return -1.0, "problem_statement_too_short"
    if len(ps) > 12000:
        return -1.0, "problem_statement_too_long"
    # Prefer fewer files, prefer code over tests
    score = 100.0 - 5.0 * len(files) + 2.0 * len(code_py) - 0.5 * (len(py_files) - len(code_py))
    return score, None


def load_dev_split():
    from datasets import load_dataset

    return load_dataset(
        SWE_BENCH_DATASET,
        split=SWE_BENCH_SPLIT,
        revision=SWE_BENCH_REVISION,
    )


def select_cases(
    *,
    target_n: int = 20,
    min_repos: int = 4,
    per_repo_cap: int = 6,
) -> tuple[list[dict[str, Any]], list[Exclusion]]:
    ds = load_dev_split()
    scored: list[tuple[float, dict[str, Any]]] = []
    exclusions: list[Exclusion] = []
    for row in ds:
        r = dict(row)
        score, reason = focused_python_score(r)
        if reason:
            exclusions.append(Exclusion(r["instance_id"], r["repo"], reason))
            continue
        scored.append((score, r))
    scored.sort(key=lambda x: (-x[0], x[1]["instance_id"]))

    # Round-robin across repos for diversity
    by_repo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for score, r in scored:
        by_repo[r["repo"]].append(r)

    repos_sorted = sorted(by_repo.keys(), key=lambda k: (-len(by_repo[k]), k))
    selected: list[dict[str, Any]] = []
    per_repo: Counter[str] = Counter()
    # Ensure at least min_repos get representation first
    for repo in repos_sorted[: max(min_repos, len(repos_sorted))]:
        if not by_repo[repo]:
            continue
        r = by_repo[repo].pop(0)
        selected.append(r)
        per_repo[repo] += 1
        if len(selected) >= target_n:
            break

    while len(selected) < target_n:
        progressed = False
        for repo in repos_sorted:
            if per_repo[repo] >= per_repo_cap:
                continue
            if not by_repo[repo]:
                continue
            selected.append(by_repo[repo].pop(0))
            per_repo[repo] += 1
            progressed = True
            if len(selected) >= target_n:
                break
        if not progressed:
            break

    selected_ids = {r["instance_id"] for r in selected}
    for score, r in scored:
        if r["instance_id"] not in selected_ids:
            exclusions.append(
                Exclusion(r["instance_id"], r["repo"], "not_selected_quota")
            )
    return selected, exclusions


def ensure_checkout(repo: str, sha: str, cache_root: Path) -> Path:
    """Materialize pre-fix source at sha via full bare clone + git archive."""
    owner, name = repo.split("/", 1)
    dest = cache_root / f"{owner}__{name}" / sha
    if dest.exists() and any(dest.iterdir()):
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    url = f"https://github.com/{repo}.git"
    mirror = cache_root / f"{owner}__{name}" / "_mirror.git"
    # Drop broken partial clones from earlier runs
    if mirror.exists():
        cfg = mirror / "config"
        if cfg.exists() and "partialclonefilter" in cfg.read_text(encoding="utf-8", errors="ignore"):
            shutil.rmtree(mirror)
    if not mirror.exists():
        subprocess.check_call(
            ["git", "clone", "--bare", url, str(mirror)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    else:
        subprocess.check_call(
            ["git", "-C", str(mirror), "fetch", "--tags", "origin", "+refs/heads/*:refs/heads/*"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    # Ensure object exists (some SHAs may need explicit fetch)
    probe = subprocess.run(
        ["git", "-C", str(mirror), "cat-file", "-t", sha],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        subprocess.check_call(
            ["git", "-C", str(mirror), "fetch", "origin", sha],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "-C", str(mirror), "archive", sha],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["tar", "-x", "-C", str(dest)],
        input=archive.stdout,
        check=True,
    )
    (dest / ".bugtime_base_commit").write_text(sha + "\n", encoding="utf-8")
    return dest


def build_case_record(row: dict[str, Any]) -> dict[str, Any]:
    query, strip_notes = strip_solution_sections(row.get("problem_statement") or "")
    query_exclusions = [HINTS_EXCLUSION, *strip_notes]
    if row.get("hints_text"):
        query_exclusions.append("hints_text_present_but_unused")
    patch_files = parse_patch_files(row.get("patch") or "")
    code_files = [
        f
        for f in patch_files
        if f.endswith(".py")
        and "/test" not in f.replace("\\", "/")
        and not Path(f).name.startswith("test_")
    ]
    return {
        "instance_id": row["instance_id"],
        "bug_group_id": row["instance_id"],
        "repository_id": row["repo"],
        "commit_sha": row["base_commit"],
        "query": query,
        "query_exclusions": query_exclusions,
        "patch_files": patch_files,
        "suggested_relevant_files": code_files,
        "created_at": row.get("created_at"),
        "version": row.get("version"),
        "source_reference": (
            f"{SWE_BENCH_DATASET}@{SWE_BENCH_REVISION} split={SWE_BENCH_SPLIT} "
            f"instance={row['instance_id']}; {DATASET_LICENSE_NOTE}"
        ),
        "reviewer_only": {
            "patch": row.get("patch") or "",
            "test_patch": row.get("test_patch") or "",
            "hints_text": row.get("hints_text") or "",
        },
    }


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def generate_pool_for_case(
    case: dict[str, Any],
    checkout: Path,
    *,
    candidate_limit: int = 50,
) -> dict[str, Any]:
    chunks = index_checkout(checkout)
    indexed_files = sorted({c.file_path for c in chunks})
    suggested = case.get("suggested_relevant_files") or []
    relevant_indexed = [f for f in suggested if f in indexed_files]
    missing_indexed = [f for f in suggested if f not in indexed_files]

    candidates = hybrid_retrieve(
        case["query"],
        chunks,
        limit=candidate_limit,
        per_file_limit=3,
    )
    pool_files = {c["file_path"] for c in candidates}
    in_pool = [f for f in suggested if f in pool_files]
    missing_pool = [f for f in suggested if f not in pool_files]

    ph = pool_hash(candidates)
    return {
        "instance_id": case["instance_id"],
        "repository_id": case["repository_id"],
        "commit_sha": case["commit_sha"],
        "dataset_revision": SWE_BENCH_REVISION,
        "retrieval_config": RETRIEVAL_CONFIG,
        "embedding_model": get_settings().embedding_model,
        "candidate_limit": candidate_limit,
        "pool_hash": ph,
        "chunk_count_indexed": len(chunks),
        "file_count_indexed": len(indexed_files),
        "suggested_relevant_files": suggested,
        "relevant_indexed": relevant_indexed,
        "relevant_missing_from_index": missing_indexed,
        "relevant_in_top50": in_pool,
        "relevant_missing_from_top50": missing_pool,
        "failure_flags": {
            "relevant_source_indexed": bool(relevant_indexed),
            "relevant_in_candidate_pool": bool(in_pool),
            "ranking_unevaluated": True,
            "relevance_uncertain": True,
        },
        "candidates": candidates,
        "created_at": _utcnow(),
    }


def seed_review_queue(case: dict[str, Any], pool: dict[str, Any]) -> list[dict[str, Any]]:
    """Create unreviewed examples; weak_auto suggestions are NOT human review."""
    rows: list[dict[str, Any]] = []
    suggested = set(case.get("suggested_relevant_files") or [])
    for cand in pool["candidates"]:
        weak = "relevant" if cand["file_path"] in suggested else "irrelevant"
        rows.append(
            {
                "example_id": f"{case['instance_id']}::{cand['chunk_id']}",
                "bug_group_id": case["bug_group_id"],
                "repository_id": case["repository_id"],
                "commit_sha": case["commit_sha"],
                "query": case["query"],
                "file_path": cand["file_path"],
                "line_range": cand["line_range"],
                "snippet": cand["snippet"],
                "label": "unreviewed",
                "label_source": "unreviewed",
                "suggested_label": weak,
                "suggested_label_source": "weak_auto_patch_file",
                "annotation_note": "",
                "source_reference": case["source_reference"],
                "split": "unassigned",
                "pool_hash": pool["pool_hash"],
                "hybrid_rank": cand["hybrid_rank"],
                "candidate_origin": "natural_retrieval",
                "dataset_revision": SWE_BENCH_REVISION,
            }
        )
    return rows


def build_repo_disjoint_splits(
    cases: list[dict[str, Any]],
    *,
    train_repos: int | None = None,
) -> dict[str, Any]:
    """Internal partitions of imported development data — not official SWE-bench test."""
    repos = sorted({c["repository_id"] for c in cases})
    if len(repos) < 3:
        return {
            "strategy": "insufficient_repository_diversity",
            "limitation": "Fewer than 3 repositories; cannot form repo-disjoint train/val/test.",
            "repos": repos,
            "assignments": {c["instance_id"]: "unassigned" for c in cases},
            "repo_to_split": {},
        }
    # Reserve test repos first (last alphabetically for stability), then val, rest train
    test_n = max(1, len(repos) // 4)
    val_n = max(1, len(repos) // 4)
    test_repos = set(repos[-test_n:])
    remaining = [r for r in repos if r not in test_repos]
    val_repos = set(remaining[-val_n:]) if remaining else set()
    train_set = set(r for r in remaining if r not in val_repos)
    repo_to_split = {}
    for r in train_set:
        repo_to_split[r] = "train"
    for r in val_repos:
        repo_to_split[r] = "validation"
    for r in test_repos:
        repo_to_split[r] = "test"
    assignments = {
        c["instance_id"]: repo_to_split.get(c["repository_id"], "unassigned") for c in cases
    }
    return {
        "strategy": "repository_disjoint",
        "note": "Internal partitions of SWE-bench development imports — not official SWE-bench test results.",
        "repos": repos,
        "repo_to_split": repo_to_split,
        "assignments": assignments,
        "counts": {
            "train_repos": len(train_set),
            "validation_repos": len(val_repos),
            "test_repos": len(test_repos),
            "train_bugs": sum(1 for v in assignments.values() if v == "train"),
            "validation_bugs": sum(1 for v in assignments.values() if v == "validation"),
            "test_bugs": sum(1 for v in assignments.values() if v == "test"),
        },
    }


def leakage_report(cases: list[dict[str, Any]], review_rows: list[dict[str, Any]]) -> dict[str, Any]:
    queries = defaultdict(list)
    near = defaultdict(list)
    for r in review_rows:
        qn = re.sub(r"\s+", " ", (r.get("query") or "").strip().lower())
        sn = re.sub(r"\s+", " ", (r.get("snippet") or "")[:200].strip().lower())
        queries[qn].append(r["example_id"])
        near[hashlib.sha256(f"{qn}||{sn}".encode()).hexdigest()].append(r["example_id"])
    dup_queries = {k: v for k, v in queries.items() if len(set(v)) > 1 and len(v) > 50}
    # Group near-dups across bug groups
    cross_group = []
    for h, ids in near.items():
        if len(ids) < 2:
            continue
        groups = {i.split("::")[0] for i in ids}
        if len(groups) > 1:
            cross_group.append({"hash": h, "example_ids": ids[:10], "bug_groups": sorted(groups)})
    return {
        "independent_bugs": len(cases),
        "repositories": sorted({c["repository_id"] for c in cases}),
        "repository_count": len({c["repository_id"] for c in cases}),
        "pair_count": len(review_rows),
        "cross_bug_near_duplicates": cross_group[:50],
        "note": "Report bugs/repos, not inflated query/snippet pair counts, for experiment sizing.",
    }


def coverage_report(root: Path) -> dict[str, Any]:
    cases_dir = root / "cases"
    pools_dir = root / "pools"
    reviews_dir = root / "reviews"
    case_files = sorted(cases_dir.glob("*.json")) if cases_dir.exists() else []
    cases = [json.loads(p.read_text()) for p in case_files]
    reviewed = 0
    unreviewed = 0
    uncertain = 0
    human = 0
    for p in sorted(reviews_dir.glob("*.jsonl")) if reviews_dir.exists() else []:
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            lab = row.get("label")
            if lab == "unreviewed":
                unreviewed += 1
            elif lab == "uncertain":
                uncertain += 1
                reviewed += 1
            else:
                reviewed += 1
            if row.get("label_source") == "human_review":
                human += 1
    pool_stats = []
    for p in sorted(pools_dir.glob("*.json")) if pools_dir.exists() else []:
        pool = json.loads(p.read_text())
        pool_stats.append(
            {
                "instance_id": pool["instance_id"],
                "relevant_indexed": pool["failure_flags"]["relevant_source_indexed"],
                "relevant_in_top50": pool["failure_flags"]["relevant_in_candidate_pool"],
                "missing_index": pool.get("relevant_missing_from_index"),
                "missing_pool": pool.get("relevant_missing_from_top50"),
            }
        )
    return {
        "experiment": EXPERIMENT_NAME,
        "dataset_revision": SWE_BENCH_REVISION,
        "independent_bugs": len(cases),
        "repositories": sorted({c["repository_id"] for c in cases}),
        "repository_count": len({c["repository_id"] for c in cases}),
        "review_queue_items": reviewed + unreviewed,
        "human_reviewed_labels": human,
        "unreviewed_labels": unreviewed,
        "uncertain_labels": uncertain,
        "login_smoke_excluded_from_metrics": True,
        "pools": pool_stats,
        "ready_for_p4b": False,
        "recommendation": (
            "Collect / complete human review on repository-disjoint splits before any pilot fine-tune. "
            "Do not start P4B until reviewed data and defensible splits exist."
            if human < 50
            else "If reviewed coverage and splits look solid, an explicitly labeled pilot fine-tune may begin."
        ),
        "generated_at": _utcnow(),
    }


def run_collection(
    *,
    target_n: int = 20,
    min_repos: int = 4,
    candidate_limit: int = 50,
    data_dir: Path | None = None,
    limit_cases: int | None = None,
) -> dict[str, Any]:
    root = experiment_root(data_dir)
    selected, exclusions = select_cases(target_n=target_n, min_repos=min_repos)
    if limit_cases is not None:
        selected = selected[:limit_cases]

    excl_path = root / "exclusions.jsonl"
    if excl_path.exists():
        excl_path.unlink()
    for ex in exclusions:
        append_jsonl(excl_path, asdict(ex))

    cache = root / "source_cache"
    cases_out: list[dict[str, Any]] = []
    all_review_rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for i, row in enumerate(selected, start=1):
        case = build_case_record(row)
        iid = case["instance_id"]
        print(f"[{i}/{len(selected)}] {iid} @ {case['commit_sha'][:12]}", flush=True)
        try:
            checkout = ensure_checkout(case["repository_id"], case["commit_sha"], cache)
            print(f"  indexed checkout ready", flush=True)
            pool = generate_pool_for_case(case, checkout, candidate_limit=candidate_limit)
            write_json(root / "cases" / f"{iid}.json", case)
            write_json(root / "pools" / f"{iid}.json", pool)
            review_rows = seed_review_queue(case, pool)
            rev_path = root / "reviews" / f"{iid}.jsonl"
            if rev_path.exists():
                rev_path.unlink()
            for r in review_rows:
                append_jsonl(rev_path, r)
            cases_out.append(case)
            all_review_rows.extend(review_rows)
            print(
                f"  pool={pool['pool_hash'][:12]} in_top50={pool['relevant_in_top50']}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED: {exc}", flush=True)
            errors.append({"instance_id": iid, "error": str(exc)})
            append_jsonl(excl_path, {"instance_id": iid, "repo": case["repository_id"], "reason": f"import_failed:{exc}"})

    splits = build_repo_disjoint_splits(cases_out)
    write_json(root / "splits" / "manifest.json", splits)
    # apply split into review rows export later
    write_json(
        root / "manifest.json",
        {
            "experiment": EXPERIMENT_NAME,
            "dataset": SWE_BENCH_DATASET,
            "dataset_revision": SWE_BENCH_REVISION,
            "split_source": SWE_BENCH_SPLIT,
            "official_test_preserved": True,
            "license_note": DATASET_LICENSE_NOTE,
            "retrieval_config": RETRIEVAL_CONFIG,
            "target_n": target_n,
            "imported_bugs": len(cases_out),
            "repositories": sorted({c["repository_id"] for c in cases_out}),
            "errors": errors,
            "login_smoke_excluded": True,
            "created_at": _utcnow(),
        },
    )
    write_json(root / "reports" / "leakage.json", leakage_report(cases_out, all_review_rows))
    cov = coverage_report(root)
    write_json(root / "reports" / "coverage.json", cov)
    return {"root": str(root), "imported": len(cases_out), "errors": errors, "coverage": cov, "splits": splits}
