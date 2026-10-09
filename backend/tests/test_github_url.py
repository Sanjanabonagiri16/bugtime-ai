import pytest

from app.services.github_url import parse_github_url


def test_valid_https_url():
    parsed = parse_github_url("https://github.com/owner/repo")
    assert parsed.owner == "owner"
    assert parsed.repo == "repo"
    assert parsed.html_url == "https://github.com/owner/repo"


def test_git_suffix_allowed():
    parsed = parse_github_url("https://github.com/owner/repo.git")
    assert parsed.repo == "repo"


def test_rejects_ssh():
    with pytest.raises(ValueError):
        parse_github_url("git@github.com:owner/repo.git")


def test_rejects_credentials():
    with pytest.raises(ValueError):
        parse_github_url("https://user:pass@github.com/owner/repo")


def test_rejects_other_hosts():
    with pytest.raises(ValueError):
        parse_github_url("https://gitlab.com/owner/repo")
