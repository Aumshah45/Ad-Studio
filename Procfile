api: cd services/api && OTEL_ENABLED=${DEV_TRACES:-false} uv run uvicorn backend.main:app --reload --port 8000
web: pnpm -C apps/web dev --port 3000
phoenix: uvx --from arize-phoenix phoenix serve
