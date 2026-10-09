"""Authentication helpers with a seeded case-sensitivity bug."""

from __future__ import annotations

USERS = {
    "alice": "wonderland",
    "bob": "builder",
}


def normalize_username(username: str) -> str:
    """Intended to make usernames case-insensitive."""
    # BUG: returns the original username instead of a lowercase form.
    return username


def authenticate(username: str, password: str) -> bool:
    """Return True when credentials match a known user."""
    key = normalize_username(username)
    stored = USERS.get(key)
    if stored is None:
        return False
    return stored == password


def login(username: str, password: str) -> dict:
    if authenticate(username, password):
        return {"ok": True, "user": normalize_username(username)}
    return {"ok": False, "error": "Invalid credentials"}
