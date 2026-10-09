"""Tiny CLI entry for the demo login app."""

from app.auth import login
from app.logging_util import info


def main() -> None:
    info("starting demo login")
    print(login("alice", "wonderland"))


if __name__ == "__main__":
    main()
