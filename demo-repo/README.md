# BugTime Demo — Seeded Login Bug

A tiny Python app with a documented authentication bug for BugTime P0 demos.

## Bug

Users with valid credentials cannot log in when their username contains mixed case.

Expected: `Alice` authenticates successfully against stored user `alice`.
Actual: login fails with `Invalid credentials`.

Root cause (for later milestones): `authenticate` compares usernames with case sensitivity after normalizing only the stored side incorrectly.

## Run tests

```bash
pytest -q
```

One test currently fails and documents the bug.
