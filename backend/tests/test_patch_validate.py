"""Unit tests for P3 patch validation constraints."""

from pathlib import Path

from app.services.patch import list_existing_tests, validate_proposal


def _repo(tmp: Path) -> Path:
    (tmp / "app").mkdir()
    (tmp / "app" / "auth.py").write_text("def normalize_username(u):\n    return u\n", encoding="utf-8")
    (tmp / "tests").mkdir()
    (tmp / "tests" / "test_auth.py").write_text(
        "def test_login_mixed_case_should_succeed():\n    assert False\n",
        encoding="utf-8",
    )
    (tmp / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    # minimal git repo for apply --check
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"],
        cwd=tmp,
        check=True,
        capture_output=True,
    )
    return tmp


def test_accepts_app_source_fix(tmp_path: Path):
    repo = _repo(tmp_path)
    existing = list_existing_tests(repo)
    result = validate_proposal(
        repo,
        [
            {
                "path": "app/auth.py",
                "content": "def normalize_username(u):\n    return u.lower()\n",
            }
        ],
        existing_tests=existing,
        bundle_files={"tests/test_auth.py"},
        max_files=5,
        max_lines=300,
    )
    assert result.ok, result.error
    assert result.patch_hash
    assert "app/auth.py" in result.changed_files


def test_rejects_existing_test_edit(tmp_path: Path):
    repo = _repo(tmp_path)
    existing = list_existing_tests(repo)
    result = validate_proposal(
        repo,
        [{"path": "tests/test_auth.py", "content": "def test_login_mixed_case_should_succeed():\n    assert True\n"}],
        existing_tests=existing,
        bundle_files=set(),
        max_files=5,
        max_lines=300,
    )
    assert not result.ok
    assert "protected" in (result.error or "").lower() or "Existing tests" in (result.error or "")


def test_rejects_dependency_edit(tmp_path: Path):
    repo = _repo(tmp_path)
    existing = list_existing_tests(repo)
    result = validate_proposal(
        repo,
        [{"path": "pyproject.toml", "content": "[project]\nname='hacked'\n"}],
        existing_tests=existing,
        bundle_files=set(),
        max_files=5,
        max_lines=300,
    )
    assert not result.ok


def test_rejects_path_traversal(tmp_path: Path):
    repo = _repo(tmp_path)
    result = validate_proposal(
        repo,
        [{"path": "../secrets.txt", "content": "x"}],
        existing_tests=set(),
        bundle_files=set(),
        max_files=5,
        max_lines=300,
    )
    assert not result.ok


def test_rejects_skip_suppression(tmp_path: Path):
    repo = _repo(tmp_path)
    result = validate_proposal(
        repo,
        [],
        existing_tests=list_existing_tests(repo),
        bundle_files=set(),
        max_files=5,
        max_lines=300,
        new_test={
            "path": "tests/test_new_regression.py",
            "content": "import pytest\n\n@pytest.mark.skip\ndef test_x():\n    assert True\n",
        },
    )
    assert not result.ok
    assert "suppress" in (result.error or "").lower()
