# BugTime Demo v2 — Login Regression History

Linear history fixture for P2 breaking-commit search.

Authentication is case-insensitive: `Alice` maps to stored user `alice`.

## Run tests

```bash
pytest -q
```

## Known issue

Some users report login failures with mixed-case usernames.
