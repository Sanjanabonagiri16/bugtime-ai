# BugTime AI

**Trace the bug. Find the breaking commit. Verify the fix. Publish a draft PR.**

BugTime AI is an investigation workspace for real software bugs. You pin a repository at a commit, retrieve likely code, reproduce the failure with the project’s own pytest suite, locate the introducing commit, generate a focused patch with a model, verify it in a disposable sandbox, then optionally open a **draft** pull request on GitHub — with local BugTime evidence clearly separated from CI.

| Milestone | What you get |
| --- | --- |
| **P0** | Import a public GitHub repo, index a pinned commit, retrieve evidence |
| **P1** | Discover and run existing pytest tests in a locked-down sandbox; confirm reproduction |
| **P2** | Find and verify the breaking commit (frozen reproduction + linear bisect) |
| **P3** | Propose a focused patch, re-run reproduction + suite, accept/reject by patch hash |
| **P4** | Optional retrieval reranker (base or fine-tuned) — **experimental** |
| **P5** | Preview and publish a **draft** PR from an accepted `verified_fix` |

---

## Why it exists

Most “AI fix” tools jump straight to a patch. BugTime insists on a chain of custody:

1. **Evidence** at a pinned SHA  
2. **Reproduction** with the repo’s real tests  
3. **Boundary** — which commit introduced the failure  
4. **Verified fix** — same frozen tests pass after the patch  
5. **Draft PR** — only the reviewed bytes, with limitations spelled out  

If any link is missing, publishing stays disabled.

---

## Architecture

```
browser → frontend:3000 → backend:8000 → postgres / qdrant
                              ↓
                     redis ← worker (index / retrieve)
                              ↓
                     redis ← executor → disposable pytest containers
                              ↓
                     GitHub API (P5 draft PRs; token stays server-side)
```

| Layer | Stack |
| --- | --- |
| Frontend | Next.js App Router, React, TypeScript, Tailwind, Monaco |
| Backend | FastAPI, SQLAlchemy, Alembic |
| Jobs | Redis + RQ (`worker` for index; `executor` for tests/repair) |
| Vector store | Qdrant (filtered by `repository_id` + `index_id`) |
| Sandbox | `bugtime-pytest-runner` — no network, 1 CPU, 1 GB, timeout |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` |
| Models | OpenAI-compatible API **or** local Ollama |

Only the **executor** mounts the Docker socket. Sandbox containers never see app secrets, the GitHub token, or the socket.

---

## Quick start

**Requirements:** Docker Desktop (or Engine + Compose v2), ~4 GB free RAM for the first embedding download.

```bash
git clone https://github.com/Sanjanabonagiri16/bugtime-ai.git
cd bugtime-ai
cp .env.example .env

# Restore the demo fixture’s git history (needed for P2 bisect)
./scripts/restore-demo-fixture.sh

# Pytest sandbox image
docker build -t bugtime-pytest-runner:latest ./runner

# Stack
docker compose up --build -d
```

| Service | URL |
| --- | --- |
| App | http://localhost:3000 |
| API health | http://localhost:8000/api/v1/health |
| Qdrant | http://localhost:6333 |

### Optional: local model (no cloud credits)

```env
MODEL_PROVIDER=ollama
MODEL_API_KEY=ollama
MODEL_NAME=qwen2.5-coder:3b
MODEL_BASE_URL=http://host.docker.internal:11434/v1
```

### Optional: draft PRs (P5)

Create a **fine-grained** GitHub PAT for the target repo only:

- **Contents:** Read and write  
- **Pull requests:** Read and write  

```env
GITHUB_TOKEN=...
```

Then recreate the API:

```bash
docker compose up -d --build backend
```

Check **Settings → GitHub** (status only — the token is never returned to the browser). Preview never creates remotes; **Create draft PR** does.

---

## Demo walkthrough (login regression)

1. **Repositories → Load demo repository** (`demo-login-bug-v2`)  
2. Wait until the index is **Ready**  
3. **New investigation** — mixed-case login fails (`Alice` vs `alice`)  
4. **Evidence** — expect `app/auth.py` / `tests/test_auth.py`  
5. **Tests → Discover** → run `tests/test_auth.py::test_login_mixed_case_should_succeed`  
6. Confirm the failure matches the bug  
7. **Commits → Find breaking commit** (pick an early good SHA)  
8. **Changes → Generate fix** → accept a **Verified fix**  
9. **Prepare draft PR** → review title/body → **Create draft PR** (needs `GITHUB_TOKEN` + a writable GitHub copy of the demo if you want a live PR)

Passing selected tests means those tests passed — not “no bug exists.” P2 assumes a single introduction on a **linear** history.

---

## P4 reranker (experimental)

P4 adds an optional cross-encoder reranker for evidence ranking (`RERANKER_MODE=disabled|base|finetuned`).

Fine-tuning and SWE-bench pilot results are **experimental**. They do **not** claim production-grade generalization. Treat held-out metrics as research notes, not product guarantees. The default deployment keeps the reranker **disabled**.

---

## P5 publishing rules

- Eligible only for an **accepted** patch whose hash matches a **verified_fix**  
- Preview is local; publish creates a feature branch + **draft** PR (no force-push, no auto-merge)  
- If `main` moved past the verified SHA → **stale_base** → revalidate (old verification is not transferred)  
- Idempotent retries return the same publication when possible  
- PR body labels results as **BugTime verification**, not GitHub Actions/CI  

---

## Configuration highlights

| Variable | Purpose |
| --- | --- |
| `MODEL_*` | Repair model (OpenAI-compatible or Ollama) |
| `GITHUB_TOKEN` | Server-side draft PR publishing (optional) |
| `RUNNER_IMAGE` | Pytest sandbox image |
| `RERANKER_MODE` | `disabled` (default) / `base` / `finetuned` |
| `EXECUTION_TIMEOUT_SECONDS` | Sandbox timeout (default `120`) |

See `.env.example` for the full list. Never commit `.env`.

---

## Project layout

```
backend/           FastAPI, RQ workers, Alembic, tests
frontend/          Next.js UI
runner/            Disposable pytest image
demo-repo-v2/      Login regression fixture (restore history via script)
demo-repo-v2.bundle  Full git history for the fixture
dev/               Milestone notes / screenshots specs
docker-compose.yml
```

---

## Tests

```bash
cd backend && pytest -q
cd frontend && npm ci && npm run lint && npm run build
```

---

## Troubleshooting

| Symptom | Check |
| --- | --- |
| UI can’t reach API | `docker compose ps`, `/api/v1/health` |
| Discover/run image errors | `docker images \| grep bugtime-pytest-runner`; rebuild `./runner` |
| Executor can’t start containers | Docker socket on `executor`; Desktop running |
| Demo bisect missing SHAs | Run `./scripts/restore-demo-fixture.sh` |
| Draft PR disabled | Accept a `verified_fix`; set `GITHUB_TOKEN`; repo must be the real GitHub identity |
| GitHub status failed | Fine-grained PAT scopes; recreate `backend` after editing `.env` |

---

## Honest limits

- Public GitHub HTTPS imports (and the built-in demo); not a general private-SCM suite yet  
- Pytest + existing tests only for reproduction  
- Linear history for bisect; merges unsupported in P2  
- One model provider at a time; patch attempts are budgeted  
- Fork publishing and auto-merge are out of scope  
- P4 fine-tuning remains **experimental**

---

## License / authorship

Built as a milestone product (P0–P5). Replace this section with your preferred license before a public release if needed.
