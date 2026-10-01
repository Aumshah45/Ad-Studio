# Production readiness — Ad Studio

Mode: **harden** (2026-09-26, against commit `14f952b`). The design pass (2026-09-24) set the threat model, decisions
and requirements before any feature code. This pass checks each "planned" row against the code, runs the checks,
load-tests a local API on the fake clients, and fills in the numbers. Statuses:
- **done**: evidence is a file path, test id or measured number;
- **partial**: works, with a named hole;
- **deferred**: not built, with a reason;
- **finding**: a defect this pass found. It is reported here and not fixed, because this pass only writes this doc.

How this pass ran (reproducible):
- **Checks.** `make check` was split so it could run in isolation. Another `make check` was running on the same machine
  at the same time and shared `ad_studio_test`, which caused 42 spurious `IntegrityError` failures (see G10). So
  ruff, pyright and pytest ran on a `git archive HEAD` copy against a throwaway database `ad_studio_hardentest`,
  since dropped. eslint, tsc and vitest ran in the repo, where `apps/web` was unmodified. The contract was diffed by
  hand.
- **Load.** The load smoke ran against `uvicorn backend.http.app:create_app --factory` on `127.0.0.1:8765` with this
  environment:
  - `IMAGE_CLIENT=fake VISION_CLIENT=fake`;
  - **all `*_API_KEY` and `LLM_*` set to empty**, plus `HTTPS_PROXY=http://127.0.0.1:9`, so any outbound call would
    fail;
  - `APP_ENV=staging`, `OTEL_ENABLED=false`, a scratch `BLOB_DIR`, and a throwaway database
    `ad_studio_loadsmoke`, since dropped.

  The server was stopped afterwards. The load ledger holds only `test:fake-image` and `local:tesseract-5.5.3` rows at
  $0.00, so **no Gemini or other hosted model was called**. `.env` was not edited.
- **Ledger.** The ledger figures come from read-only `SELECT`s on the dev database's `model_calls` table: 7,833 rows
  from the real build and golden runs.

---

## Verification summary

| Area | Result | Evidence |
|---|---|---|
| `make check` (HEAD, isolated DB) | **green** | ruff: all checks passed, 192 files formatted · pyright 0 errors · pytest **359 passed** (139.5 s) · eslint clean · tsc clean · vitest **137/137** (17 files) · OpenAPI export identical to `apps/web/openapi.json` (no contract drift) |
| Red-team cases | **11/11 pass** (fakes, no network) | `tests/integration/test_redteam.py::test_redteam_rt01…rt11`. Security subset (`test_redteam`, `test_security_api`, `test_security`, `test_products`): 54 passed. Live adversarial brief B20 is in the golden run (overlay-only, excluded from native-text metrics) |
| Threat model T1–T5 | T1 **done**, T2 **done**, T3 **partial** (F3, G5), T4 **done**, T5 **partial** (provenance deferred) | Table below |
| Design test ids | 14 of the 29 named ids exist verbatim. **All but 2 behaviours** are covered under other names (mapping below). Missing: IPTC export (D8 deferred) and a route-level blob traversal test (the route takes a UUID, so the risk is low) | `grep def test_` |
| Secrets | **clean** | `git grep` for `AIza…`, `gsk_…`, `sk-or-…` and private-key headers: 0 hits in tracked files, 0 in `git log -p --all`. `.env` is ignored (`.gitignore:1`) and untracked. `.env.example` has empty `*_API_KEY=` |
| Dependency audit | **clean** (run by hand; not in CI) | `pip-audit` on 281 pinned packages from `uv export --frozen --no-dev`: no known vulnerabilities. `pnpm audit --prod`: no known vulnerabilities |
| Unsafe HTML / raw SQL | **clean** | `grep dangerouslySetInnerHTML\|rehype-raw apps/web/src`: 0 hits. The only `sqlalchemy.text` use is the health probe (`http/routes/health.py:7`) and index DDL. No SQL is built from model output |
| Load smoke | **pass; 2 bugs found, both since fixed** | Section "Load smoke" below. F1 (orphaned runs on queue-full 429) and F2 (duplicate-image race fails runs) |

---

## Findings from this pass (F1 and F2 fixed since; the rest ranked in "Remaining gaps")

**F1. A queue-full 429 leaves an orphaned `queued` run and burns the Idempotency-Key.** *Fixed:* `prepare()` now
takes a `runner.reserve()` slot that `submit` consumes without re-checking, so a committed run is never refused
(`jobs/runner.py`, `test_queue_full_429_leaves_no_orphaned_run`, `tests/unit/test_runner.py`).
- The admission check runs before the database work. The capacity check that can fail runs after the commit:
  - `POST /v1/runs` calls `runner.ensure_capacity()` inside `prepare()` (`http/routes/runs.py:113`). It then awaits
    the ledger query and planner resolution, and `create_run` commits the row (`domain/adstudio/runs.py:160`).
  - Only then does `runner.submit()` call `ensure_capacity()` again (`jobs/runner.py:72`). When that raises
    `RunQueueFullError`, the client gets a 429, but the `runs` row is already committed with status `queued` and a
    `run.status` event.
- Reproduced: 40 concurrent fresh runs → 24 × 202, 16 × 429 `run-queue-full`, and **6 of the 16 rejected requests
  left `queued` rows**. No task ever ran them. They became `interrupted` only at the next process start
  (`recover_interrupted`).
- Consequences:
  - A client that retries with the same key gets 200 and a run that never progresses, so the UI spinner never ends.
  - The per-IP slot is released, so the limiter does not count the orphan.

**F2. Concurrent runs that produce identical image bytes fail with `IntegrityError`.** *Fixed:* the insert runs in a
savepoint and, on the unique conflict, returns the winner's row (`test_get_or_create_image_concurrent_insert_returns_the_winner`).
- `repositories.get_or_create_image` (`db/repositories.py:117-132`) does SELECT-then-INSERT, which races on
  `uq_images_tenant_sha256`. The run ends `failed / internal-error` (`pipeline.py:445` → `repositories.py:131`).
- Reproduced:
  - 2 of 10 concurrent identical cold briefs failed;
  - 1 of 2 concurrent fresh runs failed under the default limits.
- Live trigger: two in-flight runs for the same brief, one of them hitting a cache entry the other just wrote, or two
  runs whose deterministic overlay yields the same PNG. Concurrent identical **uploads** are safe: 5 parallel uploads
  gave 1 × 201 + 4 × 200.

**F3. No single-flight for identical in-flight briefs.**
- The content-keyed step cache dedupes only *sequential* repeats:
  - 20 concurrent repeats of a warm brief made **0** new image calls;
  - 10 concurrent *cold* identical briefs made **8** image calls instead of 2 (4 runner slots × 2 candidates).
- Live, that is up to 4× the spend for a double-submitted campaign. `RUNS_CONCURRENT_PER_IP=2` caps it at 2× for one
  address.

**F4. Image generation, the vision judge, the planner and product profiling share one semaphore.**
- `CallRuntime.semaphore` is keyed by provider (`llm/calls.py:52-56`), so every `google:` call shares
  `LLM_MAX_CONCURRENCY=3` slots.
- With `RUNS_MAX_CONCURRENT=4` × 2 candidates, judge calls queue behind 11–19 s image calls. The design asked for
  `(provider, kind)`.

**F5. The daily cap is checked only at admission.**
- `ensure_daily_budget` runs once per new run (`http/routes/runs.py:118`, `domain/adstudio/budget.py:125`). Runs
  already admitted can each still spend up to `AD_BUDGET_USD` ($0.25).
- Worst-case overshoot:
  - one IP: 2 concurrent runs = $0.50;
  - many IPs: 24 admitted runs (4 running + 20 queued) = $6.00, against the $3/day cap.

**F6. The upload decode timeout does not stop the worker thread.**
- `asyncio.wait_for(asyncio.to_thread(...), 5 s)` (`domain/adstudio/uploads.py:144`) returns 422, but the decode
  keeps running in the thread pool.
- Impact is bounded by the 40 MP header cap and the 30 uploads/h per-IP limit. Low.

**Test isolation (G10).** Two `make check` runs on one machine share `TEST_DATABASE_URL`. The session fixture
`TRUNCATE`s every table, so parallel runs fail. During this pass another actor had uncommitted edits to
`tests/integration/conftest.py` that appeared to address this. They are not part of `14f952b`.

---

## Key decisions (design → as built)

| # | Decision (design recommendation) | As built | Status |
|---|---|---|---|
| D1 | Enum geography and season; hemisphere computed in code | ISO-3166 alpha-2 `geography_code` plus free `season` resolved by a table first, with the model only on a miss. Unknown values → 422 (ADR-006, `domain/adstudio/resolver.py`, `locale_data.py`). Planner hemisphere 12/12 (eval report) | done (deviation recorded in ADR-006) |
| D2 | `required_text` rendered, never obeyed: NFC, reject Cc/Cf except ZWJ/ZWNJ, length cap, font coverage, one escaped slot | `validate_required_text` (`domain/adstudio/runs.py:55-68`): **≤ 80 chars, ≤ 3 lines** (architecture reconciliation overrides the 60 in the design). `ensure_overlay_supported` → 422 `unsupported-script`. `«literal»` slot (`llm/prompts/ad_generate.py`). The text never reaches the planner or judge | done |
| D3 | Judge can veto, never pardon; fail closed | Text and product gates are deterministic; the VLM can only add failures (`test_vlm_cannot_pass_failed_deterministic_check`). Judge down → `needs_review` (`test_judge_down_fails_closed_to_needs_review`, unit and integration). Boot refuses a text-only vision model (`llm/config.py validate_model_roles`, `http/app.py:47`) | done |
| D4 | USD budget reserved before each image call; no retry on image timeout | `RunBudget` (`domain/adstudio/budget.py`): **$0.25/run**, ≤ 5 image calls, 150 s. Daily cap $3 from the ledger. `retry_on_timeout=False` for images (`llm/calls.py:120`) | done; F5 overshoot |
| D5 | Content-addressed local blobs, safe serving | `storage/blobs.py` (sha regex, atomic write, `put_generated` refuses > 1024 px). Served by `GET /v1/images/{uuid}`: tenant-scoped, `ETag`, `immutable`, `CSP default-src 'none'`, nosniff (`test_images_route_headers`) | done |
| D6 | Human approval, audited | `POST /v1/runs/{id}/decision` → `run_decisions` table. An override needs a reason and writes a label (`test_decision_requires_reason_for_override`, `test_override_writes_label`) | done |
| D7 | Persisted runs, idempotency, restart recovery | `runs` + `run_events`, required `Idempotency-Key` (409 on a body mismatch), `recover_interrupted` on startup (`test_run_idempotency`, `test_startup_recovery_marks_stale_runs_interrupted`, `test_sse_replay_from_last_event_id`, `test_queue_full_429_leaves_no_orphaned_run`) | done |
| D8 | XMP/IPTC `DigitalSourceType` plus a provenance sidecar | **Not built.** Deferred after the design review. `TECHNICAL_REPORT.md` calls it "the planned next step". Uploads *strip* XMP (`test_upload_strips_exif`) | deferred: SynthID only |
| D9 | Images only to the billed Google project | Boot check plus a call-time check (`test_startup_refuses_images_to_non_google_providers`, `test_image_roles_refuse_non_google_providers`, `test_run_vision_refuses_non_google_provider_at_call_time`) | done |

---

## Threat model

**Assets**
1. Google API key and billing credit.
2. Uploaded product images, possibly unreleased, possibly carrying EXIF PII.
3. Released ads (brand reputation).
4. Evaluator integrity.
5. Golden labels and eval reports.
6. Ledger and cost data.

**Actors**
- The marketer.
- The anonymous internet, if a URL is ever exposed.
- **Content authors who plant text in images**: packaging, stickers, whoever supplies the reference photo.
- **The image model** as an untrusted producer.
- Another user of the same instance (there is no tenancy).

**Entry points (as built)**

| Entry point | Untrusted input | Guard |
|---|---|---|
| `POST /v1/products` (multipart) | image bytes, name | 12 MiB body cap, 10 MB upload cap, upload pipeline (T4), 30/h per IP |
| `POST /v1/runs` | brief, `Idempotency-Key` | Pydantic (`RunCreate`), text rules (T1), 10/h and 2 concurrent per IP, queue cap 20, daily cap |
| `GET /v1/runs/{id}/events` (SSE) | run id, `Last-Event-ID` | UUID, int parse → 422, tenant check |
| `GET /v1/images/{id}` | UUID | tenant-scoped lookup, content-addressed read |
| `POST /v1/runs/{id}/decision`, `/cancel` | action, reason | audited in `run_decisions` |
| `/v1/llm/complete`, `/v1/llm/stream` | prompt | **404 unless `APP_ENV=dev`** (`http/routes/llm.py:24`, `test_dev_llm_routes_404_outside_dev`) |
| `/v1/evals/*`, `/v1/golden/*`, `/v1/ops/summary` | query params | read-only, bounded `limit` |
| Model outputs → evaluator | generated pixels, including text | OCR, ΔE and keypoints gate; the VLM can only veto |

**Top 5 threats**

| # | Threat | OWASP | Mitigation (as built) | Proving tests | Status |
|---|---|---|---|---|---|
| T1 | Direct injection via `required_text` | LLM01, LLM05 | NFC; Cc/Cf/bidi/tag characters rejected; ≤ 80 chars / 3 lines; blocklist; injection signals flagged and routed to **overlay-only**. The text never reaches the planner or judge. Escaped `«»` slot. OCR-exact gate | `test_required_text_rejects_bidi_and_tag_chars`, `test_required_text_length_limit`, `test_spec_text_is_verbatim_even_if_planner_alters_it`, `test_image_prompt_escapes_quotes_and_newlines`, `test_injection_text_goes_overlay_only`, `test_redteam_rt01/02/03/09/10/11` | **done** |
| T2 | Indirect injection via text inside images | LLM01, LLM09 | Veto-only judge. Reference label text is stored as data in `reference_facts` and excluded from the stray-text check. Repair prompts are compiled from structured `FailureCode`s (`domain/adstudio/repair.py:4-5,140`), never from judge prose. The OCR read is escaped in repair prompts | `test_redteam_rt07_label_instruction_is_data_and_changes_no_verdict`, `test_vlm_cannot_pass_failed_deterministic_check`, `test_product_profile_facts_are_versioned_and_label_text_is_data`, `test_text_repair_prompt_escapes_the_ocr_read`, `test_repair_routing_table` | **done** |
| T3 | Unbounded consumption | LLM10 | $0.25/run, ≤ 5 image calls, 150 s; $3/day ledger cap; no image retry on timeout; per-IP 10/h and 2 concurrent; queue cap 20 → 429; dev LLM routes off outside dev | `test_budget_cap`, `test_run_budget_reserves_list_price_and_stops`, `test_daily_cap_blocks_new_runs`, `test_image_call_no_retry_on_timeout`, `test_duplicate_brief_makes_zero_new_image_calls`, `test_runs_rate_limit_per_ip_hourly`, `test_runs_rate_limit_two_concurrent_per_ip`, `test_dev_llm_routes_404_outside_dev`. Load: 429s observed (below). Ledger: max $0.2287/run, max 4 image calls/run | **partial**: F3, F5. No `DEMO_PASSCODE` or replay-only mode (the localhost bind is the control) |
| T4 | Malicious or privacy-leaking upload | LLM10, LLM02, CWE-400/434 | The upload spec below, every step implemented (`domain/adstudio/uploads.py`) | `test_upload_rejects_svg` (415), `test_upload_rejects_bomb`, `test_upload_rejects_small_animated_truncated_oversize` (413 / 422 too-small, animated, corrupt), `test_upload_strips_exif` (orientation 6 applied; GPS, owner and XMP gone), `test_upload_cmyk_normalised_to_srgb_png`, `test_redteam_rt04_oversize_text_and_uploads_are_rejected` | **done** (F6 minor) |
| T5 | Brand-unsafe or culturally wrong ad released | LLM05, LLM09 | Locale tables, conservative default for unknown markets, binary avoid-list checks in the rubric, safety blocks → `needs_review` and never retried, human approval (D6) | `test_locale_policy_avoid_list_reaches_spec_and_rubric`, `test_unknown_geography_uses_conservative_policy`, `test_safety_block_not_retried`, `test_safety_block_ends_needs_review`, `test_context_checks_compile_from_rubric_and_avoid_list`. Eval: context P/R 1.00/1.00 (n=47) | **partial**: synthetic-media disclosure (D8) not built |

**Mapping of design test ids to actual tests.** Where a test was renamed, it covers the same behaviour:

| Design id | Actual test |
|---|---|
| `test_judge_cannot_pardon_deterministic_fail` | `test_vlm_cannot_pass_failed_deterministic_check` |
| `test_reference_with_injected_text_is_flagged` | `test_redteam_rt07_…` |
| `test_repair_prompt_uses_failure_codes_only` | `test_repair_routing_table` + `test_text_repair_prompt_escapes_the_ocr_read` |
| `test_job_budget_stops_repair_loop` | `test_budget_cap` |
| `test_daily_cap_refuses_new_job_429` | `test_daily_cap_blocks_new_runs` |
| `test_image_timeout_not_retried` | `test_image_call_no_retry_on_timeout` |
| `test_duplicate_brief_returns_existing_job_zero_calls` | `test_duplicate_brief_makes_zero_new_image_calls` |
| `test_idempotent_job_same_key_same_job` | `test_run_idempotency` |
| The 8 upload ids | the 5 `test_products.py` tests above |
| `test_blob_route_rejects_traversal` | `test_blobstore_rejects_bad_ids` (store level) |
| `test_export_has_iptc_digital_source_type` | **none** (D8 deferred) |

**Accepted for the prototype**
- No tenancy enforcement: `tenant_id='demo'` is on `images`, `products`, `briefs` and `runs`
  (`db/models_adstudio.py:3`).
- System-prompt leakage (LLM07): the prompts hold no secrets.
- Threshold gaming: thresholds are tuned on the calibration split (P1–P3) and reported on the held-out split (P4–P5).

---

## Upload validation spec: verification

| Step | Status | Evidence |
|---|---|---|
| 1. 12 MiB body cap, 10 MB upload cap | done | `Settings.max_body_bytes=12_582_912`, `max_upload_bytes=10_485_760` (`core/settings.py`). `image.read(max+1)` (`http/routes/products.py:57`) |
| 2. Magic-byte allowlist PNG/JPEG/WebP → 415 | done | `Image.open(..., formats=[...])` (`uploads.py:60-75`). `test_upload_rejects_svg` |
| 3. Pixel cap before decode; bomb warning escalated | done | `MAX_IMAGE_PIXELS=40M`, `simplefilter("error", DecompressionBombWarning)`. `test_upload_rejects_bomb` |
| 4. Single frame | done | `n_frames` check (MPO primary frame allowed). `reason=animated` |
| 5. Decode in a thread with a 5 s timeout; truncated → 422 | done (F6) | `LOAD_TRUNCATED_IMAGES=False`. `reason=corrupt` |
| 6. EXIF transpose, ICC → sRGB, long edge ≤ 2048 | done | `_decode_and_normalize`. `test_upload_cmyk_normalised_to_srgb_png` |
| 7. Re-encode PNG, no metadata, content-addressed | done | `_encode_png` clears `img.info`. `test_upload_strips_exif` |
| 8. Client filename and content-type ignored | done | Never read (`products.py` docstring). Served with the stored `mime` + nosniff |

---

## Security baseline

| Item | Status | Evidence or reason |
|---|---|---|
| Secrets: env only, validated at boot | done | `SecretStr` (`core/settings.py`). `validate_model_roles` at app creation. `/ready` reports `configured:false` without printing values |
| Secrets: `.env` ignored, `.env.example` names only | done | `.gitignore:1`. `git ls-files` shows only `.env.example`, whose `*_API_KEY=` values are empty. History scan: 0 key-pattern hits |
| Secrets: gitleaks in CI | done | `.github/workflows/ci.yml` (`gitleaks/gitleaks-action@v3`) |
| Secrets: no keys to the browser | done | Only `NEXT_PUBLIC_API_URL` is public |
| AuthN | deferred | localhost demo, API on 127.0.0.1 (`Procfile`). Production: OIDC at the edge plus per-tenant keys |
| AuthZ: `tenant_id` on customer tables | done (constant `demo`) | All repository reads take `tenant_id` (`db/repositories.py`) |
| Input: Pydantic everywhere, bounded fields | done | `RunCreate` (pattern, lengths, `Literal` aspect), bounded query `limit`s, `Idempotency-Key` 1–128 |
| Input: body-size limit | done | 12 MiB (`http/middleware.py` `BodySizeLimitMiddleware`) |
| Input: upload allowlist | done | spec above |
| Input: required-text validation | done | T1 |
| Output: problem+json, no stack traces | done | `core/errors.py`, `test_500_unhandled`. The run-failure event carries only the exception class name |
| Output: sanitized markdown | done | `rehype-sanitize`. 0 `dangerouslySetInnerHTML` / `rehype-raw` |
| Output: images served safely | done | `/v1/images/{uuid}`: CSP `default-src 'none'`, nosniff, tenant-scoped (`test_images_route_headers`) |
| Transport: HTTPS | deferred | localhost only |
| Transport: CORS allowlist | partial | Origins limited to `WEB_ORIGIN` (`http/app.py:119`). **`allow_headers=["*"]`** was not narrowed |
| Headers: CSP, nosniff, Referrer-Policy, frame-ancestors | partial | nosniff, Referrer-Policy and XFO=DENY on the API and web. CSP on image responses only. **No CSP on web pages or JSON routes** (`apps/web/next.config.ts`, `core/errors.py:12-16`) |
| Abuse: per-IP token bucket | done | 120/min (`RateLimitMiddleware`). Load: 13,421 of 13,543 requests got 429 in 10 s at 20 connections |
| Abuse: per-route limits | done | `http/limits.py`: runs 10/h and 2 concurrent, uploads 30/h. Load: 12 parallel runs → 2 × 202, 10 × 429 `rate-limited` |
| Abuse: per-provider concurrency caps | partial (F4) | keyed by provider only (`llm/calls.py:52`) |
| Abuse: timeouts | partial | Model 30 s, image 60 s (`IMAGE_TIMEOUT_S`), run wall clock 150 s, decode 5 s. **No DB `statement_timeout`** (`db/session.py:25` sets only `pool_pre_ping`) |
| Abuse: cost budgets | partial (F5) | D4 |
| Data: least-privilege DB role | deferred | single local role |
| Data: no PII in logs | done | `required_text` logged as length + sha256[:16] (`http/routes/runs.py:158`). `test_redteam_rt08_pii_is_rendered_but_not_logged` |
| Data: free-tier LLMs get only public data | done | D9. The golden set is owned or CC-licensed (`data/golden/SOURCES.md`) |
| Data: retention | deferred | No purge target. Production: GCS lifecycle rules |
| Supply chain: lockfiles | done | `uv.lock`, `pnpm-lock.yaml` tracked. CI uses `--frozen-lockfile` |
| Supply chain: dependency audit in CI | **gap** | Clean when run by hand (above), but not in `ci.yml` |
| Supply chain: model IDs in config | done | `LLM_*`, `IMAGE_MODEL_*`, `VISION_JUDGE` env |
| Audit: write actions | done | `run_decisions` (actor, action, reason, time) |
| Provenance of generated media | deferred (D8) | SynthID only; the UI provenance list shows models and versions (`components/batch/ad-detail-sheet.tsx`) |

---

## Reliability

| Pattern | Status | Evidence |
|---|---|---|
| Timeouts | partial | Per call: text 30 s, image 60 s (`guarded_call(timeout_s=)`). Run 150 s (`AD_WALLCLOCK_S`). SSE keep-alive 15 s (`http/sse.py:92`). No DB statement timeout |
| Retries | done | Jittered, max 2, 429/5xx/connect only for images (`test_retries_on_429_then_succeeds`, `test_image_call_no_retry_on_timeout`, `test_image_call_still_retries_on_503`, `test_safety_block_not_retried`) |
| Fallback: text | done (tests only) | `test_fallback_serves_when_primary_fails`. Planner → deterministic table when no model answers. **Exercised in the load run**: every one of the ~80 runs logged `planner_fallback` with no model configured, and all completed |
| Fallback: image | done (tests only) | Flash-Lite ↔ Flash (`test_image_model_error_falls_back_to_the_other_model`). The dev ledger has `fallback_used=0` across 7,833 rows: the path has **never fired live**. A live fallback test was not run (no spending on live calls in this pass) |
| Fallback: vision judge | done | Fail closed. In the dev ledger, 87 `breaker_open` + 9 `ConnectError` on `google:gemini-3.8-flash`. Runs that hit these ended `needs_review` (2), never auto-passed |
| Circuit breaker | done | `llm/breaker.py`, `test_breaker_opens`, `test_breaker_half_open_after_reset`. State shown on `/ready` |
| Idempotency | done | `Idempotency-Key` required, 409 on body mismatch, content-keyed step cache |
| Caching | done | Plan, image, OCR and verdict caches through `guarded_call(cache_key=)`. Ledger hit rate 75.9% overall (OCR 86%, text/vision 60%, image 6%) |
| Backpressure | done | Runner: 4 concurrent, queue 20 → 429 `run-queue-full` with `Retry-After` (observed under load) |
| Health | done | `/health`, `/ready` report DB, extensions, each model role (client, configured, breaker) and Tesseract (version, 125 languages, scripts, shaping) |
| Graceful shutdown and recovery | done | Lifespan drains the runner and disposes the engine. Stale runs → `interrupted` at startup (6 orphans from F1 recovered this way) |
| Degraded modes | done | Load ran with **no models at all**: template planner + fake image + fake judge. Live judge outages ended `needs_review` |
| Data safety | done | Alembic 0001–0004 (5 revisions, each with `downgrade()`) (`test_migration.py`) |

**Degraded modes**
- **Image models down:** no new ads. Repeats are served from cache: 20 warm repeats made 0 image calls under load.
- **Judge down:** the verdict is `unverified` and the run ends `needs_review`.
- **Planner down:** the default table is used (`planner: default_table`).
- **DB down:** `/ready` returns 503.

---

## Numbers

| Metric | Value | How measured |
|---|---|---|
| p50 / p95 time to approved ad (live) | **48.6 s / 53.3 s** (target ≤ 60 s p50) | `evals/reports/20260926T061415Z-v2.md`, 20 golden briefs, Gemini |
| Uncached model latency p50 / p95 (live) | image **11.1 s / 19.0 s** (n=119); text+vision **12.0 s / 23.5 s** (n=1,069) | dev ledger `model_calls`, `cached=false` |
| Cost per approved ad | **$0.088** mean (eval report). Ledger: mean **$0.0877**, max **$0.2287** over 46 billed runs (cap $0.25) | ledger `SUM(est_cost_usd)` per `run_id` |
| Image calls per run | mean **2.0**, max **4** (cap 5) | ledger, `kind='image' AND NOT cached` |
| Total estimated spend to date | $6.36 (image $3.40 + text/vision $2.97) | ledger (list-price estimate, not billing) |
| First-attempt / after-repair pass | **95% / 100%** (20/20) | eval report v2 |
| Shipped exact text / native-text rate | **100% / 100%** (19, B20 excluded) | eval report v2 |
| Evaluator P / R, positive class FAIL | text **0.71** / 1.00 (**precision below the 0.85 target**) · product 1.00/1.00 · context 1.00/1.00 · composition 1.00/1.00 · technical 1.00/1.00 | eval report v2, n=41–56 per dimension |
| Human agreement (E-nat) | 90% (18/20), κ 0.615 | eval report v2 |
| Judge stability | 0 flips / 231 checks (10 items judged twice) | eval report v2 |
| Red-team pass | **11/11** (RT-01…RT-11) + live B20 overlay-only | `test_redteam.py` |
| Upload validation tests | **5 tests cover 8/8 behaviours** | `tests/integration/test_products.py` |
| Cache hit rate | **75.9%** overall (dev ledger, 7,833 rows). Duplicate-brief replay under load: **0 new image calls for 20 runs** | ledger; load ledger |
| Fallback verified | Offline only: `test_fallback_serves_when_primary_fails`, `test_image_model_error_falls_back_to_the_other_model`, planner table fallback in every load run. **Live: `fallback_used=0`**, not exercised | tests; ledger |
| Budget stop verified | `test_budget_cap`, `test_run_budget_reserves_list_price_and_stops`. Live max $0.2287 ≤ $0.25 | pytest; ledger |
| Cached read API (20 conns, 30 s) | `GET /v1/runs/{id}` **611 RPS, p50 30.9 / p95 39.5 ms** · `GET /v1/images/{id}` **578 RPS, 32.6 / 44.8 ms** · `GET /v1/runs` list 511 RPS, 34.5 / 79.0 ms · SSE full replay 806 RPS, 19.0 / 67.5 ms · `/health` 1,646 RPS, 7.5 / 36.6 ms · `/v1/ops/summary` 132 RPS, 152 / 225 ms | load smoke, 0 errors except 1 SSE `ReadError` in 16,119 |
| Run pipeline on fakes | 1 run: 1.47 s end to end. 20 concurrent: all passed, e2e p50 **8.7 s** / p95 **13.7 s**, admit p50 43 ms / p95 53 ms | load smoke |

---

## Load smoke (fake clients, no hosted model calls)

- **Setup.** Single uvicorn worker, M-series Mac. The client was a Python `httpx` asyncio script
  (`scratchpad/load.py`, not committed) on the same machine, so the RPS figures are a floor bounded by the client.
- **Product.** The product was `data/golden/products/P1.jpg`, uploaded through `POST /v1/products` (201, profile
  verified by the fake judge).
- **Brief.** AU / December / "Summer Sale 20% Off".

| Scenario | Limits | Result |
|---|---|---|
| Read path, 20 conns × 30 s | rate limit lifted | See the Numbers row. p99 ≤ 97 ms on every data route |
| 20 concurrent fresh runs | lifted | 20 × 202, 20 passed, wall 13.9 s, e2e p50 8.7 s / p95 13.7 s (4-slot runner) |
| 40 concurrent fresh runs | lifted | 24 × 202 (4 running + 20 queued), 16 × 429 `run-queue-full`, 24 passed. **F1: 6 orphaned `queued` rows** |
| 1 warm + 20 concurrent identical briefs | lifted | 20 passed, **0 new image calls**, e2e p50 7.4 s (the evaluator on CPU is now the bottleneck) |
| 10 concurrent identical *cold* briefs | lifted | 8 new image calls (F3), **2 runs `failed` IntegrityError** (F2) |
| 5 concurrent identical uploads | lifted | 1 × 201 + 4 × 200, no errors |
| 12 parallel runs, one IP | defaults (10/h, 2 concurrent) | 2 × 202, 10 × 429 `rate-limited` with `Retry-After`. 1 of the 2 admitted runs failed (F2) |
| `GET /v1/runs/{id}` flood, 20 conns × 10 s | default 120/min | 122 × 200, 13,421 × 429 at 1,355 RPS (rejections are cheap: p50 8.5 ms) |

**Bottlenecks observed**
1. Runner slots (4) set end-to-end latency under a burst.
2. The in-process evaluator (OCR, ΔE, composition) sets throughput once images come from cache.
3. `/v1/ops/summary` aggregates over the full ledger: 152 ms p50.

Live, the image-model latency (p50 11 s) and quota dominate everything above.

---

## Scale story (with numbers)

1. **Stateless API, work off the request path.**
   - `POST /v1/runs` answers in **43 ms p50 / 53 ms p95** (fakes; live adds the ledger and cache lookups).
   - Prototype: in-process runner with a Postgres event log (ADR-002).
   - Production: Cloud Tasks or Pub/Sub → Cloud Run workers claiming rows with `FOR UPDATE SKIP LOCKED`.
   - Admission and enqueue are atomic (F1 fixed: the queue slot is reserved before the row is committed).
2. **Idempotent, content-keyed steps.**
   - A repeat brief costs $0 in image calls (measured: 20/20 runs, 0 calls).
   - Ledger hit rate is 75.9%.
   - Add single-flight (F3) so concurrent duplicates also cost nothing. That matters for 20-market campaign fan-out.
3. **The bottleneck is image-model quota and cost.**
   - Cost is $0.088 per approved ad, with 2.0 image calls per run on average.
   - At 1,000 ads/day: ≈ $88/day, or ≈ $2.6k/month at list price.
   - Levers:
     - the Batch API (~50%) for overnight campaigns → ≈ $1.3k/month;
     - N=2 → N=1 candidates when first-attempt pass is 95% → ≈ −40% of image spend.
   - Split the per-provider semaphore by kind (F4) before raising concurrency.
4. **CPU-bound evaluation scales separately.**
   - With images cached, 20 concurrent runs still take 7.4 s p50, which is evaluator-bound in one process.
   - Move OCR, ΔE and composition to a worker pool (a process pool, then a separate service).
5. **Storage and tenancy.**
   - Blobs → GCS with signed URLs and lifecycle rules.
   - `tenant_id` is already on the root tables.
   - The limiter, breakers and daily-cap reservations → Redis once there is more than one instance. F5 needs a global
     reservation counter anyway.

---

## Known limits

- One labeller for the human labels. Human agreement κ is 0.615 on text.
- Text-dimension precision is **0.71**, below the 0.85 target: 6 false fails on 52 items, which cost extra repairs, not
  bad ads (recall is 1.00).
- The judge and generator are the same model family (Gemini). Veto-only authority limits the damage, and meta-eval
  measures it, but the bias is not eliminated.
- **No IPTC/XMP/C2PA provenance.** SynthID is provider-embedded and unverified, and the overlay pixels are not
  watermarked.
- The text guarantee covers only scripts the bundled fonts shape. Anything else gets 422 `unsupported-script`.
- Locale tables cover the golden geographies plus a conservative default. They do not replace a cultural review.
- The image fallback stays inside Google, so a Google outage means cache replay only. Live fallback has never fired
  (`fallback_used=0`).
- No auth, no tenancy enforcement, in-process limiter and breakers, local-disk blobs, single region.
- Costs are list-price estimates in the ledger, not reconciled with billing. The daily cap can be overshot (F5).
- Test runs share one test database and cannot run concurrently (G10).

---

## Remaining gaps, ranked by risk ÷ effort

Risk weighs likelihood in a demo or pilot × blast radius (money, stuck UI, wrong ad). Effort is minutes for one
engineer, including a regression test.

| Rank | Gap | Risk | Effort | Fix |
|---|---|---|---|---|
| 1 | **G8** `pip-audit` + `pnpm audit --prod` missing from CI (both clean today) | Medium: supply chain is unguarded going forward | **5 min** | 2 CI steps |
| 2 | **F4** one semaphore per provider for image, judge and planner | Medium: judge latency under concurrent runs, deadline hits → `needs_review` | **15 min** | Key `CallRuntime.semaphore` by `(provider, kind)` with per-kind limits |
| 3 | **G7** no DB `statement_timeout` | Medium: a slow query holds pool connections | **5 min** | `connect_args={"options": "-c statement_timeout=5000"}` (`db/session.py:25`) |
| 4 | **F3** no single-flight for identical in-flight briefs (4× spend) | Medium in live campaign fan-out; capped at 2× per IP today | **30 min** | At admission, return the active run with the same content key, or add a per-`cache_key` asyncio lock in `guarded_call` |
| 5 | **F5** daily cap checked only at admission (overshoot ≤ $0.50 single IP, ≤ $6 worst case) | Medium on a shared URL, low on localhost | **30 min** | In-process reservation counter checked before each image call, alongside `RunBudget.reserve` |
| 6 | **G6** CSP on web pages and JSON routes; narrow CORS `allow_headers` | Low on localhost; security reviewers look for it | **15 min** | `next.config.ts` CSP (the design's policy); API `default-src 'none'; frame-ancestors 'none'`; `allow_headers=["content-type","idempotency-key","last-event-id","x-request-id"]` |
| 7 | **G10** shared test DB breaks concurrent `make check` | Low in production, annoying for reviewers (42 false failures seen) | **20 min** | Per-worker database or schema. An uncommitted change seen during this pass appeared to address this |
| 8 | **D8** provenance (XMP `DigitalSourceType` + sidecar) | Low technical risk, real compliance and brand risk in production | **45 min** | Write the iTXt XMP on export plus a sidecar from `runs` + `model_calls`, and add `test_export_has_iptc_digital_source_type` |
| 9 | Live fallback proof (`fallback_used=0`) | Low | **15 min** | Add a `FAKE_IMAGE_SCRIPT` `error` behaviour for an offline demo of the Flash-Lite → Flash fallback that records `fallback_used` |
| 10 | **F6** decode timeout doesn't kill the thread | Low (40 MP cap, 30/h) | 20 min | Decode in a `ProcessPoolExecutor` with a kill on timeout |
