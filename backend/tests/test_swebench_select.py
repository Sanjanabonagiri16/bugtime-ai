from app.services.swebench_import import (
    focused_python_score,
    parse_patch_files,
    strip_solution_sections,
    build_repo_disjoint_splits,
)


def test_parse_patch_files():
    patch = """diff --git a/pkg/a.py b/pkg/a.py
--- a/pkg/a.py
+++ b/pkg/a.py
@@ -1 +1 @@
-x
+y
diff --git a/tests/test_a.py b/tests/test_a.py
"""
    assert parse_patch_files(patch) == ["pkg/a.py", "tests/test_a.py"]


def test_focused_prefers_code_py():
    row = {
        "instance_id": "x",
        "repo": "o/r",
        "problem_statement": "Bug in authentication helper that breaks login for mixed case.",
        "patch": "diff --git a/pkg/auth.py b/pkg/auth.py\n",
    }
    score, reason = focused_python_score(row)
    assert reason is None
    assert score > 0


def test_strip_solution_section():
    text = "Issue body here.\n\n## Solution\nDo this fix.\n"
    out, notes = strip_solution_sections(text)
    assert "Do this fix" not in out
    assert notes


def test_repo_disjoint_splits():
    cases = [
        {"instance_id": "a1", "repository_id": "r/a"},
        {"instance_id": "b1", "repository_id": "r/b"},
        {"instance_id": "c1", "repository_id": "r/c"},
        {"instance_id": "d1", "repository_id": "r/d"},
    ]
    m = build_repo_disjoint_splits(cases)
    assert m["strategy"] == "repository_disjoint"
    assert set(m["repo_to_split"].values()) >= {"train", "validation", "test"}
