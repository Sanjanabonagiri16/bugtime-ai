"""P4 dataset: JSONL schema, validation, import/export, hashing."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

Label = Literal["relevant", "irrelevant", "unreviewed"]
Split = Literal["train", "validation", "test"]
VALID_LABELS = frozenset({"relevant", "irrelevant", "unreviewed"})
VALID_SPLITS = frozenset({"train", "validation", "test"})
REQUIRED = (
    "example_id",
    "bug_group_id",
    "repository_id",
    "commit_sha",
    "query",
    "file_path",
    "snippet",
    "label",
    "label_source",
    "source_reference",
    "split",
)


@dataclass
class DatasetExample:
    example_id: str
    bug_group_id: str
    repository_id: str
    commit_sha: str
    query: str
    file_path: str
    snippet: str
    label: str
    label_source: str
    source_reference: str
    split: str
    line_range: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d.get("line_range") is None:
            d.pop("line_range", None)
        return d


@dataclass
class ValidationIssue:
    code: str
    message: str
    example_id: str | None = None


@dataclass
class DatasetValidationResult:
    ok: bool
    example_count: int
    labeled_count: int
    unreviewed_count: int
    split_counts: dict[str, int]
    bug_group_count: int
    repository_count: int
    dataset_hash: str
    issues: list[ValidationIssue] = field(default_factory=list)
    split_strategy: str = "within_repository"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "example_count": self.example_count,
            "labeled_count": self.labeled_count,
            "unreviewed_count": self.unreviewed_count,
            "split_counts": self.split_counts,
            "bug_group_count": self.bug_group_count,
            "repository_count": self.repository_count,
            "dataset_hash": self.dataset_hash,
            "split_strategy": self.split_strategy,
            "notes": self.notes,
            "issues": [asdict(i) for i in self.issues],
        }


_NEAR_DUP_WS = re.compile(r"\s+")


def _norm_text(s: str) -> str:
    return _NEAR_DUP_WS.sub(" ", (s or "").strip().lower())


def _near_key(query: str, snippet: str) -> str:
    return hashlib.sha256(f"{_norm_text(query)}||{_norm_text(snippet)}".encode()).hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at line {lineno}: {exc}") from exc
            if not isinstance(obj, dict):
                raise ValueError(f"Line {lineno}: expected object")
            rows.append(obj)
    return rows


def write_jsonl(path: Path, examples: Iterable[dict[str, Any] | DatasetExample]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for ex in examples:
            d = ex.to_dict() if isinstance(ex, DatasetExample) else ex
            f.write(json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n")


def dataset_content_hash(rows: list[dict[str, Any]]) -> str:
    payload = json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def validate_dataset(rows: list[dict[str, Any]]) -> DatasetValidationResult:
    issues: list[ValidationIssue] = []
    ids: set[str] = set()
    near_seen: dict[str, str] = {}
    group_split: dict[str, set[str]] = defaultdict(set)
    group_repos: dict[str, set[str]] = defaultdict(set)
    split_counts: dict[str, int] = defaultdict(int)
    labeled = 0
    unreviewed = 0
    repos: set[str] = set()
    groups: set[str] = set()

    for i, row in enumerate(rows):
        eid = str(row.get("example_id") or f"row-{i}")
        for key in REQUIRED:
            if key not in row or row[key] in (None, ""):
                issues.append(
                    ValidationIssue("missing_field", f"Missing required field '{key}'", eid)
                )
        label = row.get("label")
        split = row.get("split")
        if label is not None and label not in VALID_LABELS:
            issues.append(ValidationIssue("invalid_label", f"Invalid label '{label}'", eid))
        if split is not None and split not in VALID_SPLITS:
            issues.append(ValidationIssue("invalid_split", f"Invalid split '{split}'", eid))
        if not row.get("source_reference"):
            issues.append(
                ValidationIssue("missing_provenance", "source_reference is required", eid)
            )
        if not row.get("label_source"):
            issues.append(
                ValidationIssue("missing_provenance", "label_source is required", eid)
            )
        if eid in ids:
            issues.append(ValidationIssue("duplicate_id", f"Duplicate example_id '{eid}'", eid))
        ids.add(eid)

        if label == "unreviewed":
            unreviewed += 1
        elif label in ("relevant", "irrelevant"):
            labeled += 1

        if split in VALID_SPLITS:
            split_counts[str(split)] += 1
        bg = str(row.get("bug_group_id") or "")
        rid = str(row.get("repository_id") or "")
        if bg:
            groups.add(bg)
            if split in VALID_SPLITS:
                group_split[bg].add(str(split))
            if rid:
                group_repos[bg].add(rid)
        if rid:
            repos.add(rid)

        nk = _near_key(str(row.get("query") or ""), str(row.get("snippet") or ""))
        if nk in near_seen and near_seen[nk] != eid:
            issues.append(
                ValidationIssue(
                    "near_duplicate",
                    f"Near-identical query+snippet vs {near_seen[nk]}",
                    eid,
                )
            )
        else:
            near_seen[nk] = eid

        # Supervised training must not include unreviewed
        if split == "train" and label == "unreviewed":
            issues.append(
                ValidationIssue(
                    "unreviewed_in_train",
                    "Unreviewed examples must stay out of supervised train",
                    eid,
                )
            )

    for bg, splits in group_split.items():
        if len(splits) > 1:
            issues.append(
                ValidationIssue(
                    "split_leakage",
                    f"bug_group_id '{bg}' appears in multiple splits: {sorted(splits)}",
                    None,
                )
            )

    split_strategy = "within_repository"
    notes: list[str] = []
    if len(repos) >= 2 and all(len(group_repos[g]) == 1 for g in groups):
        # Distinct repos with no cross-repo groups → repo-level split possible
        split_strategy = "by_repository"
        notes.append("Multiple repositories present; prefer repository-level holdout when labeling.")
    else:
        notes.append(
            "Within-repository evaluation only: insufficient independent repositories "
            "for repository-level holdout."
        )

    if len(groups) < 3:
        notes.append(
            f"Only {len(groups)} independent bug_group(s); not enough for meaningful "
            "fine-tune generalization (P4B)."
        )

    ok = not any(
        i.code
        in {
            "missing_field",
            "invalid_label",
            "invalid_split",
            "missing_provenance",
            "duplicate_id",
            "split_leakage",
            "unreviewed_in_train",
        }
        for i in issues
    )
    # near_duplicate is a warning for import but fails strict validate
    if any(i.code == "near_duplicate" for i in issues):
        ok = False

    return DatasetValidationResult(
        ok=ok,
        example_count=len(rows),
        labeled_count=labeled,
        unreviewed_count=unreviewed,
        split_counts=dict(split_counts),
        bug_group_count=len(groups),
        repository_count=len(repos),
        dataset_hash=dataset_content_hash(rows),
        issues=issues,
        split_strategy=split_strategy,
        notes=notes,
    )


def supervised_rows(rows: list[dict[str, Any]], split: str | None = None) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        if row.get("label") not in ("relevant", "irrelevant"):
            continue
        if split is not None and row.get("split") != split:
            continue
        out.append(row)
    return out


def split_manifest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_split: dict[str, list[str]] = defaultdict(list)
    groups: dict[str, str] = {}
    for row in rows:
        eid = str(row["example_id"])
        sp = str(row.get("split"))
        by_split[sp].append(eid)
        groups[str(row.get("bug_group_id"))] = sp
    return {
        "splits": {k: sorted(v) for k, v in by_split.items()},
        "bug_group_to_split": groups,
        "dataset_hash": dataset_content_hash(rows),
    }


def default_datasets_dir(data_dir: str | Path) -> Path:
    return Path(data_dir) / "rerank" / "datasets"


def list_dataset_files(data_dir: str | Path) -> list[Path]:
    root = default_datasets_dir(data_dir)
    if not root.exists():
        return []
    return sorted(root.glob("*.jsonl"))
