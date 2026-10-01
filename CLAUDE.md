# Ad Studio — rules for coding agents

Ad Studio: display-ad image generation (Gemini image models, product reference image + geography /
season / must-include text → ad ≤ 1024 px long edge) with a measured quality gate and evaluator.

## Map
```
apps/web/                       Next.js App Router (TypeScript strict), shadcn/ui (base-nova preset = Base UI, not Radix)
  src/app/<route>/              feature pages (status page at /status)
  src/components/               ui/ (shadcn), app-shell, markdown, feature components
  src/lib/api/                  GENERATED client from the API's OpenAPI — never hand-edit (make contract)
  src/lib/sse.ts                SSE parser + typed StreamEvent
services/api/                   FastAPI, Python 3.13 via uv; package `backend`
  src/backend/core/             settings, logging, errors (problem+json), telemetry
  src/backend/http/             app factory, middleware, routes, SSE
  src/backend/db/               SQLAlchemy models, session
  src/backend/llm/              gateway (text), calls.guarded_call (image/audio/embeddings), cache, ledger, prompts/
  src/backend/guardrails/       input, context (wrap_untrusted), output, pii, budget
  src/backend/domain/<context>/ FEATURE CODE goes here (services + schemas); nothing depends on domain
  migrations/                   Alembic (async)
  evals/suites/                 metric suites (NAME, run(), TARGETS); reports in evals/reports/
  tests/unit, tests/integration pytest (no network)
data/golden/                    golden dataset + SOURCES.md (provenance, licences)
docs/                           prd, architecture, ai-design, ux, production-readiness, stack-lock
```

## Commands
`make setup` · `make dev` (api :8000, web :3000, Phoenix :6006) · `make check` (ruff, pyright, pytest, eslint, tsc,
vitest, contract drift) · `make fmt` · `make test` · `make migrate` · `make db` · `make eval` · `make contract`.

## Rules
- Every call to a hosted or local model (text, image, audio, embeddings) goes through `backend.llm`: text via
  `gateway.LlmGateway`, anything else wrapped with `calls.guarded_call` so it is timed, retried, rate-limited, cached
  and recorded in the `model_calls` ledger.
- Prompts live in `backend/llm/prompts/` as `Prompt(name, version, system)`; bump `version` on every text change.
- Model outputs consumed by code are Pydantic models (`output_type=`).
- User-controlled text reaching a model is checked with `guardrails.input.check_input` and wrapped with
  `guardrails.context.wrap_untrusted` (the gateway wraps `user_input` by default).
- Never hand-edit `apps/web/src/lib/api`; run `make contract` after API schema changes and commit the result.
- Model IDs come from env (`LLM_*`), never hardcoded in code.
- No secrets in code or logs. Keys live only in `.env` (gitignored).
- Model policy: no local LLMs and no local AI models except OCR (hosted APIs otherwise).
- Errors are RFC 9457 problem+json (`core.errors.AppError`).

## Definition of done
`make check` green, slice eval gate met, contract regenerated, and a commit with a clear message.

## Pointers
`docs/prd.md` (requirements, success criteria), `docs/architecture.md`, `docs/ai-design.md`, `docs/ux.md`,
`docs/stack-lock.md` (installed versions and deviations).
