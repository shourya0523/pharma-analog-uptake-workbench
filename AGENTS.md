# AGENTS.md

Read `CLAUDE.md` first: its rules apply to every change here, and its Layout
section says what this branch holds.

## Runnable today

This branch is a greenfield rebuild. The frontend runs; the backend so far is
a library and a command-line probe, with no API server (the previous backend
is on `main`).

| Service | Dir | Dev command | Port |
| --- | --- | --- | --- |
| Frontend SPA (React + Vite, Node, npm) | `frontend/` | `npm run dev` | 5173 |

Commands live in `frontend/package.json` - prefer those to duplicating them
here.

- Install: `cd frontend && npm ci`.
- Lint: `npm run lint` (oxlint).
- Test: `npm run test` (`vitest run`, jsdom; API calls are mocked, so no backend
  is needed).
- Build check: `npm run build` (`tsc -b && vite build`).

Backend (`backend/`, Python, managed by `uv`):

- Install: `cd backend && uv sync`.
- Test: `uv run pytest -q` (offline; the Gemini client is faked).
- XBRL first pass: `app.retrieval.xbrl.first_pass(product, "Q2 2023", issuer)`
  reads the product's revenue from the issuer's 10-Q XBRL via EDGAR full-text
  search. Needs `SEC_USER_AGENT` with a contact address; SEC answers 403
  "Undeclared Automated Tool" without one.
- Probe: `uv run python -m app.retrieval.probe "<product>" "<period>" --issuer "<issuer>"`
  calls Gemini with Google Search grounding. Needs `GEMINI_API_KEY`; the model
  is `GEMINI_MODEL` if set, else the default in `gemini_search.py`.

The scripts in `scripts/` run with plain `python3` and need `openpyxl` for the
workbook exporters. `build_independent_gold.py` also needs `httpx`,
`beautifulsoup4`, `lxml`, `pdfplumber` and network access, and writes into `seed/gold/`
unless given `--out-dir`. `build_gold_cases.py` imports the old backend and does
not run on this branch.

## Non-obvious caveats

- The frontend reads its API base from `VITE_API_URL`, defaulting to
  `http://127.0.0.1:8000`. With no backend, pages that fetch data show their
  error or empty state; that is expected, not an environment problem.
- The production image (`frontend/Dockerfile`) builds with `VITE_API_URL=/api`
  into a Caddy image. The Caddyfile that proxied `/api` to the backend lived in
  `deploy/`, which is not on this branch, so the image is not deployable as is.
- There is no database, worker or API server yet. When they land, add their
  service rows, commands and caveats here.
