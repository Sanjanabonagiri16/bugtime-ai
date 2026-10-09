from __future__ import annotations

import ast
import re
from dataclasses import dataclass


@dataclass
class ChunkDraft:
    start_line: int
    end_line: int
    symbol_name: str | None
    content: str


INDEX_EXTENSIONS = {".py", ".md", ".txt", ".toml", ".yaml", ".yml", ".json"}
SKIP_DIR_NAMES = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".idea",
    ".vscode",
    "coverage",
    "htmlcov",
}
SKIP_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
}
SKIP_LOCKFILES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
    "uv.lock",
    "Cargo.lock",
    "Gemfile.lock",
}

MAX_CHUNK_LINES = 80
CHUNK_OVERLAP = 12


def language_for_path(path: str) -> str | None:
    lower = path.lower()
    if lower.endswith(".py"):
        return "python"
    if lower.endswith(".md"):
        return "markdown"
    if lower.endswith((".yaml", ".yml")):
        return "yaml"
    if lower.endswith(".toml"):
        return "toml"
    if lower.endswith(".json"):
        return "json"
    if lower.endswith(".txt"):
        return "text"
    return None


def source_type_for_path(path: str) -> str:
    lower = path.lower().replace("\\", "/")
    if "/tests/" in f"/{lower}" or lower.startswith("tests/") or lower.endswith("_test.py") or lower.startswith("test_"):
        return "test"
    if lower.endswith((".md", ".txt", ".rst")):
        return "documentation"
    return "code"


def should_skip_path(rel_path: str) -> str | None:
    parts = rel_path.replace("\\", "/").split("/")
    name = parts[-1]
    if any(p in SKIP_DIR_NAMES for p in parts[:-1]):
        return "generated_or_dependency_folder"
    if name in SKIP_FILE_NAMES or name.startswith(".env"):
        return "credential_or_env_file"
    if name in SKIP_LOCKFILES:
        return "lockfile"
    if name.endswith((".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip")):
        return "binary_or_unsupported"
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in INDEX_EXTENSIONS:
        return "unsupported_extension"
    return None


def _lines_with_numbers(text: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    return [(i + 1, line) for i, line in enumerate(lines)]


def _slice_content(lines: list[tuple[int, str]], start: int, end: int) -> str:
    return "\n".join(line for num, line in lines if start <= num <= end)


def _bounded_sections(
    lines: list[tuple[int, str]],
    start: int,
    end: int,
    symbol_name: str | None,
) -> list[ChunkDraft]:
    span = end - start + 1
    if span <= MAX_CHUNK_LINES:
        content = _slice_content(lines, start, end)
        return [ChunkDraft(start, end, symbol_name, content)]
    drafts: list[ChunkDraft] = []
    cursor = start
    while cursor <= end:
        section_end = min(cursor + MAX_CHUNK_LINES - 1, end)
        content = _slice_content(lines, cursor, section_end)
        drafts.append(ChunkDraft(cursor, section_end, symbol_name, content))
        if section_end >= end:
            break
        cursor = section_end - CHUNK_OVERLAP + 1
    return drafts


def chunk_python(text: str) -> list[ChunkDraft]:
    lines = _lines_with_numbers(text)
    if not lines:
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return chunk_by_lines(text)

    drafts: list[ChunkDraft] = []
    covered: set[int] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = getattr(node, "lineno", 1)
            end = getattr(node, "end_lineno", start)
            # small surrounding context
            start = max(1, start - 1)
            end = min(lines[-1][0], end + 1)
            name = getattr(node, "name", None)
            drafts.extend(_bounded_sections(lines, start, end, name))
            covered.update(range(start, end + 1))

    leftovers = [num for num, _ in lines if num not in covered]
    if leftovers and not drafts:
        return chunk_by_lines(text)
    if leftovers:
        # keep uncovered top-level as line chunks
        group_start = leftovers[0]
        prev = leftovers[0]
        for num in leftovers[1:] + [None]:  # type: ignore[list-item]
            if num is None or num != prev + 1:
                drafts.extend(_bounded_sections(lines, group_start, prev, None))
                if num is not None:
                    group_start = num
            if num is not None:
                prev = num
    return drafts or chunk_by_lines(text)


def chunk_markdown(text: str) -> list[ChunkDraft]:
    lines = _lines_with_numbers(text)
    if not lines:
        return []
    heading = re.compile(r"^(#{1,6})\s+(.*)$")
    sections: list[tuple[int, int, str | None]] = []
    current_start = 1
    current_name: str | None = None
    for num, line in lines:
        m = heading.match(line)
        if m and num > current_start:
            sections.append((current_start, num - 1, current_name))
            current_start = num
            current_name = m.group(2).strip()
        elif m:
            current_name = m.group(2).strip()
    sections.append((current_start, lines[-1][0], current_name))
    drafts: list[ChunkDraft] = []
    for start, end, name in sections:
        drafts.extend(_bounded_sections(lines, start, end, name))
    return drafts


def chunk_by_lines(text: str) -> list[ChunkDraft]:
    lines = _lines_with_numbers(text)
    if not lines:
        return []
    return _bounded_sections(lines, 1, lines[-1][0], None)


def chunk_file(path: str, text: str) -> list[ChunkDraft]:
    lang = language_for_path(path)
    if lang == "python":
        return chunk_python(text)
    if lang == "markdown":
        return chunk_markdown(text)
    return chunk_by_lines(text)
