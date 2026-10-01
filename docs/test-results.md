# Test results

Automated test suites for the project. Run them from a **clone** of the repository (see the note
below). Reproduce with:

```bash
make check          # ruff + pyright + pytest + eslint + tsc + vitest + contract drift
make test           # backend pytest + web vitest only
cd services/api && uv run pytest -q     # backend only
pnpm -C apps/web test                   # web only
```

## Backend — `pytest` (`services/api`)

**406 passed · 0 failed**

- Also clean: `ruff check`, `ruff format --check`, `pyright` (0 errors).
- The suite runs fully offline (no network); model calls are replayed from the committed verdict
  caches, and the golden-data tests read the committed golden outputs in `data/golden/v*/outputs/`.
- Complex-script overlay tests (Devanagari, Thai, Arabic) need Pillow with libraqm; `make setup`
  installs it, and CI checks for it explicitly.
- The repair-probe test scripts the fake image client to typo the first-round headline, so the
  draft it probes fails text deterministically rather than depending on an evaluator miss.

## Web — `vitest` (`apps/web`)

**137 passed · 0 failed** (17 test files)

## Clone vs. ZIP

The repository keeps the full golden dataset, including the generated `outputs/` images the
evaluation and golden-data tests read. The downloadable source ZIP omits those large folders (via
`.gitattributes` `export-ignore`) to keep the download small, so **clone the repository**
to run `make eval` or the full test suite.
