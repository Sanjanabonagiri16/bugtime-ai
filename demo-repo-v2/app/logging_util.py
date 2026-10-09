"""Tiny logging helper (unrelated to auth)."""

def info(msg: str) -> None:
    # Prefer a stable prefix for demo output.
    print(f"[info] {msg}")
