-include .env
export

PG_BIN := $(shell [ -d /opt/homebrew/opt/postgresql@17/bin ] && echo /opt/homebrew/opt/postgresql@17/bin/ || echo "")
PSQL := $(PG_BIN)psql
DB_URL := $(subst +psycopg,,$(DATABASE_URL))
TEST_DB_URL := $(subst +psycopg,,$(TEST_DATABASE_URL))
API := services/api
WEB := apps/web
HAS_WEB := $(shell [ -d $(WEB) ] && echo 1)

.PHONY: setup db migrate dev dev-traces fmt lint typecheck test check eval eval-record contract contract-check \
	golden-run golden-repair-probe golden-export golden-import golden-sheet golden-sheet-v1-composition golden-sheet-v1-reasons golden-profile \
	plant golden-dryrun

setup:
	cd $(API) && uv sync
ifeq ($(HAS_WEB),1)
	pnpm -C $(WEB) install
endif
	$(MAKE) db
	$(MAKE) migrate
ifeq ($(HAS_WEB),1)
	$(MAKE) contract
endif

db:
	@for url in "$(DB_URL)" "$(TEST_DB_URL)"; do \
	  name=$${url##*/}; name=$${name%%\?*}; base=$${url%/*}/postgres; \
	  if ! $(PSQL) "$$base" -tAc "SELECT 1 FROM pg_database WHERE datname='$$name'" | grep -q 1; then \
	    echo "creating database $$name"; $(PSQL) "$$base" -qc "CREATE DATABASE \"$$name\""; \
	  fi; \
	  $(PSQL) "$$url" -qc "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm; CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;"; \
	done

migrate:
	cd $(API) && uv run alembic upgrade head

dev:  # the app: API :8000 + web :3000
	uvx honcho start api web

dev-traces:  # the app plus Phoenix trace viewer on :6006 (optional, for debugging model calls)
	DEV_TRACES=true uvx honcho start

fmt:
	cd $(API) && uv run ruff format . && uv run ruff check --fix .

lint:
	cd $(API) && uv run ruff check . && uv run ruff format --check .
ifeq ($(HAS_WEB),1)
	pnpm -C $(WEB) lint
endif

typecheck:
	cd $(API) && uv run pyright
ifeq ($(HAS_WEB),1)
	pnpm -C $(WEB) typecheck
endif

test:
	cd $(API) && uv run pytest -q
ifeq ($(HAS_WEB),1)
	pnpm -C $(WEB) test
endif

check: lint typecheck test
ifeq ($(HAS_WEB),1)
	$(MAKE) contract-check
endif

# --- golden dataset and evals (slices 14-17; architecture "Offline batch and eval path") ---------
# GOLDEN_VERSION=v1|v2 (default v2) picks the dataset dir data/golden/<version> (ADR-007: v1 is the
# labelled baseline run, v2 the composition run). OUT=<dir> writes the dataset somewhere else (a
# scratch dir). `make eval` without OUT/GOLDEN_VERSION reports every version that has outputs plus a
# v1 -> v2 comparison. DRY=1 is a key-less dry run: the fake
# image and vision clients, the test database and scratch blobs/outputs/reports under
# services/api/var/golden-dryrun (gitignored). ONLY=B01,B02 limits golden-run, BUDGET=7 sets its
# batch spend cap in USD (default 5), FORCE=1 re-runs finished briefs (the new run supersedes);
# RERUN=10 makes
# eval-record judge 10 items twice (judge stability).
OUT ?=
DRY ?=
GOLDEN_VERSION ?=
DRYRUN_DIR := $(CURDIR)/$(API)/var/golden-dryrun
ifeq ($(DRY),1)
GOLDEN_OUT := $(abspath $(or $(OUT),$(DRYRUN_DIR)/golden))
REPORTS_OUT := $(abspath $(or $(REPORTS),$(DRYRUN_DIR)/reports))
GOLDEN_ENV := DATABASE_URL=$(TEST_DATABASE_URL) BLOB_DIR=$(DRYRUN_DIR)/blobs IMAGE_CLIENT=fake \
	VISION_CLIENT=fake FAKE_IMAGE_SCRIPT= OTEL_ENABLED=false
GOLDEN_FLAGS := --fake
PLANT_FLAGS := --assume-pass
else
GOLDEN_OUT := $(abspath $(or $(OUT),$(CURDIR)/data/golden/$(or $(GOLDEN_VERSION),v2)))
REPORTS_OUT := $(abspath $(or $(REPORTS),$(CURDIR)/$(API)/evals/reports))
endif
GOLDEN := cd $(API) && $(GOLDEN_ENV) uv run python -m backend.cli golden

golden-run:
	$(GOLDEN) run --golden $(GOLDEN_OUT) $(GOLDEN_FLAGS) $(if $(ONLY),--only $(ONLY)) \
		$(if $(BUDGET),--budget $(BUDGET)) $(if $(FORCE),--force)

golden-export:
	$(GOLDEN) export --golden $(GOLDEN_OUT) $(GOLDEN_FLAGS)

# The live repair loop on every failing first-round draft of the latest golden runs (probe runs
# stay out of export; writes <out dir>/repair_probe.json). ONLY=V12 limits it.
golden-repair-probe:
	$(GOLDEN) repair-probe --golden $(GOLDEN_OUT) $(GOLDEN_FLAGS) $(if $(ONLY),--only $(ONLY))

golden-sheet:
	$(GOLDEN) sheet --golden $(GOLDEN_OUT)

# The v1 composition pass (rubric v2): the 40 v1 images with their labels preloaded, only the
# composition column (and technical corrections) to fill -> data/golden/v1/labels.csv, rubric 2.
golden-sheet-v1-composition:
	$(GOLDEN) sheet --version v1 --composition

# The v1 reason relabel (analysis R1b): only the images failed on product or context, labels
# preloaded; a required reason per failed dimension (scale / pasted move it to composition)
# -> data/golden/v1/labels.csv with `reason:<dimension>=<code>` in notes.
golden-sheet-v1-reasons:
	$(GOLDEN) sheet --version v1 --reasons

golden-profile:
	$(GOLDEN) profile $(GOLDEN_FLAGS) $(if $(FORCE),--force)

# KEEP_GENERATED=1 regenerates the pure items only and keeps the generated context items.
plant:
	$(GOLDEN) plant --golden $(GOLDEN_OUT) $(GOLDEN_FLAGS) $(PLANT_FLAGS) \
		$(if $(KEEP_GENERATED),--keep-generated)

golden-import:
	$(GOLDEN) import --golden $(GOLDEN_OUT) --reports $(REPORTS_OUT) $(GOLDEN_FLAGS)

eval-record:
	cd $(API) && $(GOLDEN_ENV) uv run python -m evals --record --golden $(GOLDEN_OUT) \
	  $(if $(RERUN),--rerun $(RERUN))

eval:
ifeq ($(or $(OUT),$(GOLDEN_VERSION),$(DRY)),)
	cd $(API) && uv run python -m evals --all-versions --reports $(REPORTS_OUT)
else
	cd $(API) && $(GOLDEN_ENV) uv run python -m evals --golden $(GOLDEN_OUT) --reports $(REPORTS_OUT)
endif

# The whole chain on the fake clients: run -> export -> plant -> record -> eval -> sheet -> import.
golden-dryrun:
	cd $(API) && DATABASE_URL=$(TEST_DATABASE_URL) uv run alembic upgrade head
	$(MAKE) DRY=1 golden-run golden-export plant
	$(MAKE) DRY=1 RERUN=10 eval-record
	$(MAKE) DRY=1 eval golden-sheet golden-import

contract:
ifeq ($(HAS_WEB),1)
	cd $(API) && uv run python -m backend.export_openapi > ../../$(WEB)/openapi.json
	pnpm -C $(WEB) openapi-ts
else
	@echo "no apps/web; skipping contract"
endif

contract-check: contract
ifeq ($(HAS_WEB),1)
	@test -z "$$(git status --porcelain -- $(WEB)/openapi.json $(WEB)/src/lib/api)" || \
	  (echo "contract drift: run make contract and commit"; git status --porcelain -- $(WEB)/openapi.json $(WEB)/src/lib/api; exit 1)
endif
