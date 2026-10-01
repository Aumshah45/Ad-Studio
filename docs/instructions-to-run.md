# Instructions to run

Ad Studio runs in two modes:

- **Offline (no API key)** — the full pipeline, UI, tests and evaluation run against built-in *fake* image/vision
  clients and the committed golden data. This is enough to explore the app and **reproduce every metric** in the
  reports. Start here.
- **Live (Gemini key)** — real generation and judging with the Gemini API. Needed only to create new ads.

Everything is driven by `make`. Run all commands from the repository root.

---

## 1. Prerequisites

- **macOS or Linux**
- **PostgreSQL 17** with the **pgvector** extension, running locally
- **Python 3.13** with [`uv`](https://docs.astral.sh/uv/)
- **Node 24** with [`pnpm`](https://pnpm.io/)
- **Tesseract OCR** with language packs (`tesseract-lang`) and **`libraqm`** (complex-script text shaping)

<details>
<summary>Install the system dependencies</summary>

**macOS (Homebrew):**
```bash
brew install postgresql@17 pgvector tesseract tesseract-lang libraqm uv pnpm
brew services start postgresql@17
```

**Debian/Ubuntu:**
```bash
sudo apt-get update
sudo apt-get install -y postgresql postgresql-contrib postgresql-16-pgvector \
  tesseract-ocr tesseract-ocr-jpn tesseract-ocr-hin tesseract-ocr-kor \
  tesseract-ocr-tha tesseract-ocr-ara libraqm0
# uv:  curl -LsSf https://astral.sh/uv/install.sh | sh
# pnpm: npm install -g pnpm
```
</details>

## 2. Get the code and set up

```bash
git clone https://github.com/Aumshah45/Ad-Studio.git
cd Ad-Studio
cp .env.example .env          # defaults run fully offline (IMAGE_CLIENT=fake, VISION_CLIENT=fake)
make setup                    # install deps (api + web), create databases, run migrations
```

`make setup` installs the Python (`uv`) and web (`pnpm`) dependencies, creates the app and test databases, and applies
the Alembic migrations. No API key is required.

> **Clone, don't download the ZIP, if you want to run the evals or the full test suite.** The repository includes the
> full golden dataset, including the generated `data/golden/v*/outputs/` images that `make eval` and the golden-data
> tests read. The source ZIP (GitHub's *Download ZIP*) omits those large folders to keep the download small (about
> 16 MB), so it is enough to read the code and run the app, but not to reproduce the metrics.

> If your Postgres uses non-default credentials, set `DATABASE_URL` and `TEST_DATABASE_URL` in `.env` before
> `make setup` (see §6).

## 3. Run the app (offline)

```bash
make dev
```

- **Web UI:** http://localhost:3000
  - **Studio** (`/`) — upload a product photo, pick geography + season, enter the required text, run a generation
  - **Run view** (`/runs/[id]`) — the live plan → candidates → scorecards → repair → approval timeline (SSE)
  - **Evaluator** (`/evals`) — precision/recall, agreement, cost/latency from the committed reports
  - **Batch** (`/batch`) — the golden gallery
  - **Status** (`/status`) — model health, budget and the model-call ledger
- **API:** http://localhost:8000 (OpenAPI at `/docs`)

Optional: `make dev-traces` also starts the Phoenix OpenTelemetry trace viewer on http://localhost:6006 for inspecting
every model call. The app does not need it.

## 4. Run the tests

```bash
make check      # full gate: ruff, pyright, pytest (offline), eslint, tsc, vitest, contract drift
```

Or the suites individually:

```bash
make test                                 # backend pytest + web vitest
cd services/api && uv run pytest -q       # backend only (offline, no network)
pnpm -C apps/web test                     # web only (vitest)
```

The Python suite runs with sockets blocked and replays model calls from the committed caches — no network, no key.
All suites pass (backend 406/406, web 137/137); current results are recorded in [`test-results.md`](test-results.md).

## 5. Reproduce the evaluation metrics (offline)

```bash
make eval
```

This reads the committed golden sets (`data/golden/v1`, `v2`, `v3`) and their cached verdict snapshots, recomputes
precision/recall, human agreement, cost and latency, and writes fresh reports to `services/api/evals/reports/`,
including a v1 → v2 comparison. It runs entirely offline, so the numbers in the reports are reproducible from a clean
clone. A key-less dry run of the whole record pipeline is `make eval DRY=1`.

Pre-computed reports are already in the repo:
- [`services/api/evals/reports/20260926T073328Z-v2.md`](../services/api/evals/reports/20260926T073328Z-v2.md) — final v2 (evaluator ev-0.8)
- [`services/api/evals/reports/v3-unseen-2026-09-26.md`](../services/api/evals/reports/v3-unseen-2026-09-26.md) — unseen v3 hard cases
- [`services/api/evals/reports/analysis-2026-09-26.md`](../services/api/evals/reports/analysis-2026-09-26.md) — failure analysis

## 6. Live generation (optional — needs a Gemini key)

To generate real ads you need a `GOOGLE_API_KEY` from a **billed** Google AI Studio project. In `.env`:

```bash
GOOGLE_API_KEY=your_key_here
IMAGE_CLIENT=google
VISION_CLIENT=google
```

Then either use **Studio** in the running app, or run the golden batch from the CLI:

```bash
make golden-run                 # generate + evaluate all 20 briefs live (about $1.75 per run)
make golden-run ONLY=B01,B02    # limit to specific briefs
make golden-repair-probe        # run the live repair loop on the latest run's failing drafts
```

Budgets are enforced (default $0.25/run, $3/day, ≤ 5 image calls, 150 s deadline) — see §6 variables to adjust.

## 7. Environment variables

Names and defaults are in [`.env.example`](../.env.example). The ones you are most likely to touch:

| Variable | Purpose |
|---|---|
| `DATABASE_URL`, `TEST_DATABASE_URL` | Postgres connections for the app and tests |
| `IMAGE_CLIENT`, `VISION_CLIENT` | `fake` (offline, default) or `google` (live) |
| `GOOGLE_API_KEY` | Gemini key, required only for live generation |
| `IMAGE_MODEL_CANDIDATE`, `IMAGE_MODEL_REPAIR`, `VISION_JUDGE` | model IDs (never hardcoded in code) |
| `AD_BUDGET_USD`, `DAILY_BUDGET_USD`, `MAX_IMAGE_CALLS`, `AD_WALLCLOCK_S` | per-run and per-day budgets |
| `N_CANDIDATES`, `MAX_REPAIRS`, `IMAGE_TIMEOUT_S` | pipeline tuning |

## 8. Handy make targets

| Command | Does |
|---|---|
| `make setup` | Install deps, create databases, migrate |
| `make dev` / `make dev-traces` | Run API + web (+ Phoenix traces) |
| `make check` | Full lint + type + test + contract gate |
| `make test` | Backend pytest + web vitest |
| `make eval` | Offline eval reports from the committed golden sets |
| `make golden-run` | Live generation of the 20-brief golden batch (needs a key) |
| `make migrate` / `make db` | Apply migrations / (re)create databases |
| `make contract` | Regenerate the typed web API client from the OpenAPI schema |
| `make fmt` | Format code |

## Troubleshooting

- **Ports in use:** the app uses 3000 (web), 8000 (API) and 6006 (traces). Free them or stop the conflicting process.
- **Postgres/pgvector:** ensure the server is running and the `vector` extension is available; `make setup` creates the
  databases but the server must be up first.
- **Tesseract language errors:** install the language packs listed in §1 (Japanese, Hindi, Korean, Thai, Arabic) for
  the non-Latin briefs.
- **No key:** keep `IMAGE_CLIENT=fake VISION_CLIENT=fake` — the whole flow, tests and evals run without one.

More detail — prerequisites, variables and the design behind each step — is in [`../TECHNICAL_REPORT.md`](../TECHNICAL_REPORT.md#9-how-to-run).
