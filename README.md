# AI Revenue Recovery Platform

Detects revenue at risk (e.g. failed payments), diagnoses the cause,
decides on a recovery strategy, executes bounded and auditable recovery
actions, observes outcomes, learns from history, and measures recovered
revenue — built incrementally as a modular monolith with self-hosted
open-weight AI models kept strictly out of the financial-action path.

See `docs/architecture.md` for the system design, `docs/project-state.md`
for current build status, and `docs/decisions/` for architecture decision
records.

## Stack

- **Backend**: Python 3.13, FastAPI, SQLAlchemy 2.0 (async), PostgreSQL,
  Redis
- **Frontend**: Next.js 16 (App Router), TypeScript, Tailwind CSS
- **AI**: self-hosted open-weight models (no paid LLM APIs), behind a
  provider abstraction

## Local Development

### Prerequisites

- Docker Desktop (for Postgres, Redis, and containerized services)
- Python 3.13 (backend, if running outside Docker)
- Node.js 20+ (frontend, if running outside Docker)

### Run everything with Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

- Backend: http://localhost:8000/health
- Frontend: http://localhost:3000
- API docs: http://localhost:8000/docs

### Run the backend locally (without Docker)

```bash
cd backend
python -m venv .venv
./.venv/Scripts/activate   # Windows
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload
```

Tests / lint / types:

```bash
pytest -q
ruff check .
ruff format --check .
mypy app
```

### Run the frontend locally (without Docker)

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

`API_BASE_URL` is a server-only variable (the homepage's backend health check
runs server-side); it is deliberately not prefixed `NEXT_PUBLIC_` because
that prefix gets inlined at build time, which breaks in Docker where the
in-network hostname differs from the host machine's `localhost`.

```bash
```

Lint / types / build:

```bash
npm run lint
npx next typegen   # generates route types tsc depends on (e.g. LayoutProps)
npx tsc --noEmit
npm run build
```

## Project Status

This project is built phase by phase under a strict verify-before-advance
methodology (no phase begins until the previous phase's tests, lint, type
checks, build, and CI are green). Current status is tracked in
`docs/project-state.md`.
