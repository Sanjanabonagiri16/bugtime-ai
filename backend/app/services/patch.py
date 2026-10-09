"""Validate and apply focused repair patches under P3 constraints."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

PROTECTED_EXACT = {
    "pytest.ini",
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "tox.ini",
    "conftest.py",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".gitignore",
}
PROTECTED_PREFIXES = (
    ".git/",
    ".github/",
    ".env",
    "requirements",
    "poetry.lock",
    "Pipfile",
    "runner/",
    "backend/",
    "frontend/",
)
SKIP_SUPPRESS_PATTERNS = (
    re.compile(r"@pytest\.mark\.skip"),
    re.compile(r"pytest\.skip\("),
    re.compile(r"@pytest\.mark\.xfail"),
    re.compile(r"unittest\.skip"),
)


@dataclass
class PatchValidation:
    ok: bool
    error: str | None
    changed_files: list[str]
    added_lines: int
    deleted_lines: int
    unified_diff: str
    patch_hash: str
    new_test_path: str | None = None


def _normalize_path(path: str) -> str:
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _is_protected(path: str, *, existing_tests: set[str], bundle_files: set[str]) -> str | None:
    norm = _normalize_path(path)
    if (
        not norm
        or norm.startswith("/")
        or norm.startswith("~")
        or ".." in Path(norm).parts
        or norm.startswith("../")
    ):
        return f"Illegal path: {path}"
    if any(norm == b or norm.startswith(b.rstrip("/") + "/") for b in bundle_files):
        return f"Protected reproduction bundle file: {norm}"
    lower = norm.lower()
    base = Path(norm).name
    if base in PROTECTED_EXACT or lower in {x.lower() for x in PROTECTED_EXACT}:
        return f"Protected configuration file: {norm}"
    for pref in PROTECTED_PREFIXES:
        if lower.startswith(pref.lower()) or base.lower().startswith(pref.lower()):
            return f"Protected path: {norm}"
    # Existing tests may not be modified; new test files are allowed.
    if norm in existing_tests:
        return f"Existing tests are protected: {norm}"
    if "/." in f"/{norm}" and not norm.startswith("tests/"):
        return f"Hidden/dot paths are not allowed: {norm}"
    return None


def _line_delta(old: str, new: str) -> tuple[int, int]:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    # Approximate: count net additions/deletions via set-less LCS-free heuristic
    # Prefer difflib for accuracy
    import difflib

    added = deleted = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old_lines, b=new_lines).get_opcodes():
        if tag == "insert":
            added += j2 - j1
        elif tag == "delete":
            deleted += i2 - i1
        elif tag == "replace":
            deleted += i2 - i1
            added += j2 - j1
    return added, deleted


def files_to_unified_diff(checkout: Path, files: list[dict[str, str]]) -> str:
    import difflib

    chunks: list[str] = []
    for item in files:
        path = _normalize_path(item["path"])
        new = item["content"]
        if not new.endswith("\n"):
            new += "\n"
        target = checkout / path
        old = ""
        if target.is_file():
            old = target.read_text(encoding="utf-8", errors="replace")
            if not old.endswith("\n") and old:
                old += "\n"
        diff = difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
        text = "".join(diff)
        if text:
            chunks.append(text)
    return "".join(chunks)


def validate_proposal(
    checkout: Path,
    files: list[dict[str, str]],
    *,
    existing_tests: set[str],
    bundle_files: set[str],
    max_files: int,
    max_lines: int,
    new_test: dict[str, str] | None = None,
) -> PatchValidation:
    all_files = list(files)
    new_test_path = None
    if new_test and new_test.get("path") and new_test.get("content") is not None:
        new_test_path = _normalize_path(new_test["path"])
        if not new_test_path.startswith("tests/"):
            return PatchValidation(
                False, "New regression test must live under tests/", [], 0, 0, "", ""
            )
        if new_test_path in existing_tests:
            return PatchValidation(
                False, "New regression test must not overwrite an existing test file", [], 0, 0, "", ""
            )
        all_files.append({"path": new_test_path, "content": new_test["content"]})

    if not all_files:
        return PatchValidation(False, "Patch contains no file changes", [], 0, 0, "", "")
    if len(all_files) > max_files:
        return PatchValidation(
            False, f"Too many files ({len(all_files)} > {max_files})", [], 0, 0, "", ""
        )

    changed: list[str] = []
    added_total = deleted_total = 0
    for item in all_files:
        path = _normalize_path(item["path"])
        reason = _is_protected(path, existing_tests=existing_tests, bundle_files=bundle_files)
        if reason:
            return PatchValidation(False, reason, [], 0, 0, "", "")
        if path.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".so", ".bin", ".exe")):
            return PatchValidation(False, f"Binary files are not allowed: {path}", [], 0, 0, "", "")
        content = item["content"]
        stripped = content.lstrip()
        if (
            stripped.startswith("diff --git ")
            or stripped.startswith("--- a/")
            or stripped.startswith("+++ b/")
        ):
            return PatchValidation(
                False,
                f"files[].content must be full source text, not a unified diff ({path})",
                [],
                0,
                0,
                "",
                "",
            )
        for pat in SKIP_SUPPRESS_PATTERNS:
            if pat.search(content):
                return PatchValidation(
                    False, f"Patch suppresses tests in {path}", [], 0, 0, "", ""
                )
        target = checkout / path
        if target.exists() and target.is_symlink():
            return PatchValidation(False, f"Symlink changes are not allowed: {path}", [], 0, 0, "", "")
        old = ""
        if target.is_file():
            old = target.read_text(encoding="utf-8", errors="replace")
        a, d = _line_delta(old, content)
        added_total += a
        deleted_total += d
        changed.append(path)

    if added_total + deleted_total > max_lines:
        return PatchValidation(
            False,
            f"Too many changed lines ({added_total + deleted_total} > {max_lines})",
            changed,
            added_total,
            deleted_total,
            "",
            "",
        )

    unified = files_to_unified_diff(checkout, all_files)
    if not unified.strip():
        return PatchValidation(False, "Patch produces an empty diff", changed, 0, 0, "", "")

    # git apply --check when possible
    check_err = _git_apply_check(checkout, unified)
    if check_err:
        return PatchValidation(
            False, f"git apply --check failed: {check_err}", changed, added_total, deleted_total, unified, ""
        )

    digest = hashlib.sha256(unified.encode("utf-8")).hexdigest()
    return PatchValidation(
        True,
        None,
        changed,
        added_total,
        deleted_total,
        unified,
        digest,
        new_test_path=new_test_path,
    )


def _git_apply_check(checkout: Path, unified: str) -> str | None:
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8") as fh:
        fh.write(unified)
        patch_path = fh.name
    try:
        proc = subprocess.run(
            ["git", "apply", "--check", patch_path],
            cwd=str(checkout),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout or "unknown error").strip()[:500]
        return None
    finally:
        Path(patch_path).unlink(missing_ok=True)


def apply_files(checkout: Path, files: list[dict[str, str]]) -> None:
    for item in files:
        path = _normalize_path(item["path"])
        dest = checkout / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = item["content"]
        if not content.endswith("\n"):
            content += "\n"
        dest.write_text(content, encoding="utf-8")


def list_existing_tests(checkout: Path) -> set[str]:
    found: set[str] = set()
    for root in (checkout / "tests", checkout):
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            rel = str(path.relative_to(checkout)).replace("\\", "/")
            name = path.name
            if name.startswith("test_") or name.endswith("_test.py") or "/tests/" in f"/{rel}":
                if rel == "conftest.py" or rel.endswith("/conftest.py"):
                    continue
                found.add(rel)
    return found
