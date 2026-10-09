import re
from dataclasses import dataclass


GITHUB_HTTPS = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


@dataclass
class ParsedGitHubURL:
    owner: str
    repo: str
    clone_url: str
    html_url: str


def parse_github_url(url: str) -> ParsedGitHubURL:
    raw = (url or "").strip()
    if not raw:
        raise ValueError("GitHub URL is required")
    if "://" not in raw:
        raise ValueError("URL must use HTTPS")
    if raw.lower().startswith("git@"):
        raise ValueError("SSH URLs are not supported; use HTTPS")
    if "@" in raw.split("://", 1)[-1].split("/", 1)[0]:
        raise ValueError("Embedded credentials are not allowed")
    match = GITHUB_HTTPS.match(raw)
    if not match:
        raise ValueError(
            "Invalid GitHub URL. Use https://github.com/owner/repo (public repositories only)."
        )
    owner = match.group("owner")
    repo = match.group("repo")
    if repo.endswith(".git"):
        repo = repo[:-4]
    html_url = f"https://github.com/{owner}/{repo}"
    return ParsedGitHubURL(
        owner=owner,
        repo=repo,
        clone_url=f"{html_url}.git",
        html_url=html_url,
    )
