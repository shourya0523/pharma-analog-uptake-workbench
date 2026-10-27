# AGENTS.md

Read `CLAUDE.md` first: its rules apply to every change here, and its Layout
section says what this branch holds.

## Runnable today

This branch is a greenfield rebuild. The only runnable service is the frontend;
the backend it talks to has not been written yet (the previous one is on
`main`).

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

The scripts in `scripts/` run with plain `python3` and need `openpyxl` for the
workbook exporters. Not all of them run on this branch: some import the old
backend or read seed inputs that were removed.

## Non-obvious caveats

- The frontend reads its API base from `VITE_API_URL`, defaulting to
  `http://127.0.0.1:8000`. With no backend, pages that fetch data show their
  error or empty state; that is expected, not an environment problem.
- The production image (`frontend/Dockerfile`) builds with `VITE_API_URL=/api`
  into a Caddy image. The Caddyfile that proxied `/api` to the backend lived in
  `deploy/`, which is not on this branch, so the image is not deployable as is.
- There is no Python environment, database or worker yet. When the backend
  lands, add its service row, commands and caveats here.
