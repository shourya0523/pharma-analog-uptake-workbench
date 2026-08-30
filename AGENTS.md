# AGENTS.md

## Cursor Cloud specific instructions

Monorepo with two runnable services: a FastAPI backend (`backend/`, Python 3.12, managed by `uv`) and a React + Vite frontend (`frontend/`, Node, npm). Standard commands live in `README.md`, `backend/pyproject.toml`, and `frontend/package.json` — prefer those rather than duplicating them here.

### Services

| Service | Dir | Dev command | Port |
| --- | --- | --- | --- |
| Backend API + in-process pipeline/worker | `backend/` | `uv run uvicorn app.main:app --reload --port 8000` | 8000 |
| Frontend SPA | `frontend/` | `npm run dev` | 5173 |

- Lint: backend `uv run ruff check .`; frontend `npm run lint` (oxlint).
- Test: backend `uv run pytest` (23 tests, no network needed). Frontend has no test suite; `npm run build` (`tsc -b && vite build`) is the build check.

### Non-obvious caveats

- `uv` is installed to `~/.local/bin` (added to `~/.bashrc` by its installer). If `uv` is not found, run `export PATH="$HOME/.local/bin:$PATH"`.
- `backend/.env` must exist before starting the backend: `cp backend/.env.example backend/.env`. It is gitignored, so it is recreated per VM (the update script handles this).
- The Vite dev server binds to `localhost` only, not `127.0.0.1`. Health-check the frontend at `http://localhost:5173` (curling `127.0.0.1:5173` returns nothing).
- No separate database or worker process: SQLite (`backend/storage/workbench.db`), local file store, and the extraction pipeline all run in-process inside the backend and are auto-created on startup.
- The extraction pipeline runs **without** an `OPENROUTER_API_KEY` — every LLM step degrades gracefully to a safe empty/deterministic result. In that mode the pipeline still retrieves SEC/OpenFDA sources and extracts citation-backed profile fields from OpenFDA, but `candidates_extracted` (LLM-derived quarterly revenue) will be 0. Set `OPENROUTER_API_KEY` in `backend/.env` for full revenue extraction/judging.
- `backend/ruff check .` currently reports pre-existing style findings (unsorted imports, etc.); these are not environment problems.
- End-to-end runs need outbound internet to `openrouter.ai`, `data.sec.gov`/`www.sec.gov`, and `api.fda.gov`. If egress is restricted in a future VM, source retrieval will be limited but the app still starts and serves.
- AWS S3/SQS/RDS paths (`STORAGE_BACKEND=s3`, `JOB_BACKEND=sqs`) and `infra/` are optional and not needed for local dev.
