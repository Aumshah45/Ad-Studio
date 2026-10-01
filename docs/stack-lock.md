# Stack lock — Ad Studio

Recorded when the scaffold was generated on 2026-09-24 (profile core+web).

## Toolchain
| Tool | Version |
|---|---|
| node | v24.16.0 |
| pnpm | 11.10.0 |
| uv | 0.12.17 |
| Python (api venv) | 3.13.14 |
| PostgreSQL | 17.11 (Homebrew `postgresql@17`; `psql` on PATH is 16.15, Makefile prefers the 17 binaries) |
| jq | jq-1.7.1-apple |
| ffmpeg | 9.0.2 (not used by the scaffold) |

## API (services/api, uv.lock is authoritative)
- alembic 1.20.0
- fastapi 0.141.1
- google-genai 2.25.0
- groq 1.7.0
- httpx 0.28.1
- ocrmac 1.0.1 (macOS only, `sys_platform == 'darwin'`; brings pyobjc-framework-vision 12.2.2)
- openai 3.19.2
- opentelemetry-exporter-otlp-proto-http 1.44.0
- opentelemetry-instrumentation-fastapi 0.65b0
- opentelemetry-sdk 1.44.0
- pgvector 0.5.0
- psycopg 3.3.6
- pydantic 2.13.5
- pydantic-ai-slim 2.49.0
- pydantic-settings 2.15.0
- pyright 1.1.414
- pytest 9.1.1
- pytest-asyncio 1.4.0
- pyyaml 6.0.3
- ruff 0.16.8
- sqlalchemy 2.0.54
- structlog 26.1.0
- uvicorn 0.53.0

## Web (apps/web, pnpm-lock.yaml is authoritative)
- next 16.3.6, react 19.2.8, typescript ^5, tailwindcss ^4
- shadcn ^4.21.0 — preset `base-nova` (components on Base UI `@base-ui/react` ^1.8.0, not Radix; no `asChild`)
- @hey-api/openapi-ts ^0.99.0, vitest ^5.0.1, @testing-library/react ^16.3.3, jsdom ^30.1.1
- react-markdown ^10.1.0, remark-gfm ^4.0.1, rehype-sanitize ^6.0.0

## Models (from .env; never hardcoded)
- LLM_PRIMARY=google:gemini-3.8-flash, LLM_FALLBACKS=groq:llama-3.3-70b-versatile, LLM_JUDGE=groq:openai/gpt-oss-120b
- Image models for the feature (Gemini 3.1 Flash Image / Flash-Lite Image) are not wired yet; pricing table has
  list-price entries ($0.067 / $0.034 per image) under `google:gemini-3.1-flash-image` / `-flash-lite-image`.

## Deviations from the scaffold spec
- `guarded_call` returns `(result, cached)` and takes extra keyword args `prompt_name`, `encode`/`decode`
  (cache serialisation) and `runtime` (defaults to the app's runtime); `units` returns a `Units` object that can
  also carry `served_model` so fallback use is recorded.
- Ledger recorders live in `backend/llm/ledger.py` (`DbRecorder`, `MemoryRecorder`), dependencies in
  `backend/http/deps.py` — small extra modules not named in the spec.
- App state (engine, runtime, gateway) is created in `create_app()` rather than the lifespan so tests using
  `httpx.ASGITransport` (which does not run lifespan) work; the lifespan only disposes the engine.
- Unhandled-500 responses are produced by Starlette's outermost ServerErrorMiddleware, so the handler adds the
  security headers and `X-Request-ID` itself.
- Ruff config lives in root `ruff.toml` (so the PostToolUse format hook and `services/api` share it).
- pyright: strict on `src/`, standard mode on tests/evals/migrations.

## Deviations recorded during the build (Lane A)
- Slice 2: `model_calls.run_id` ships in its own revision `0001b_ledger_run_id` (nullable, indexed, no FK), because
  the ledger writes the column from slice 2 on and `0002_adstudio` (slice 3) does not exist yet. `0002_adstudio`
  adds the FK to `runs`. The revision ids keep the plan's `0002_adstudio` name.
- Slice 2: `VISION_JUDGE` (plan/architecture name) is used instead of ai-design's `LLM_VISION`. Its default
  `google:gemini-3.8-flash` is a placeholder until the slice-1 spike confirms it. Startup
  (`validate_model_roles` in `create_app`) rejects a vision role on a known text-only model and an image role on a
  non-`*-image` model, using a prefix allowlist in `llm/config.py`.
- Slice 2: `guarded_call` gained `timeout_s=` and `retry_on_timeout=`; `SafetyBlockedError` is never retried,
  is recorded with ledger `status="blocked"` and does not trip the breaker. `format_event` accepts any pydantic
  model and adds `id: <seq>` when the event has an integer `seq`.
- Slice 2: the test socket guard (`tests/netguard.py`, autouse) blocks non-loopback `connect` and DNS lookups;
  loopback stays allowed for the local Postgres.
- Slice 3: `pillow` 12.3.0 is added here rather than in slice 4, because the BlobStore's 1024 px assert on the
  generated write path decodes the image header. `PIL.features.check('raqm')` is True with the wheel.
- Slice 3: blobs are stored as `var/blobs/<sha[:2]>/<sha>` (no extension; the mime lives in `images.mime`) under
  `services/api/var/blobs` by default (`BLOB_DIR`, gitignored). The C1 cap is enforced twice: in
  `BlobStore.put_generated` and by the DB check `ck_images_generated_max_edge`.
- Slice 3: small schema additions beyond the architecture ER diagram: `evaluation_checks.evidence_data` (jsonb,
  UI evidence boxes), `planted_failures.source_image_id` (golden items can come from files), `run_decisions`
  `{action, reason, actor, previous_status, is_override}`, candidate status `blocked` (safety block). Evaluation
  dimension passes are nullable (null = unverified). Repositories live in `backend/db/repositories.py`.
- Slice 4: `python-multipart` 0.0.32 added. `MAX_BODY_BYTES` default lowered to 12 MiB and `MAX_UPLOAD_BYTES=10 MB`
  added (route check). Upload rejections use the architecture's problem types (`payload-too-large` 413,
  `unsupported-media-type` 415, `validation-error` 422) with an extra `reason` field on 422s (`too-small`,
  `animated`, `corrupt`, `pixel-format`, `decode-timeout`, `empty`). JPEG/MPO files are accepted and only the
  primary frame is used. Uploads are always re-encoded to metadata-free PNG, so `/v1/images` serves them as
  `image/png`; product idempotency is by the sha256 of that normalised PNG. `GET /v1/products/{id}` is added
  beside the contract's list/create. Reference OCR / injection flags on the upload (spec step 7) arrive with the
  product-profile slice.
- Slice 5: new settings `IMAGE_CLIENT=gemini|fake` (fake = local composite of the product photo, no key,
  no network; records model `test:fake-image` at $0), `IMAGE_API=interactions|generate_content`,
  `RUNS_MAX_CONCURRENT=4`, `RUNS_QUEUE_CAP=20`. With `IMAGE_CLIENT=gemini` and no key, `POST /v1/runs` returns
  503 `image-unconfigured` before any row is written. `GeminiImageClient` is **unverified** until the slice-1
  spike; the candidate <-> repair model fallback chain arrives with the full orchestrator (slice 12).
- Slice 5: generated images are decoded, downscaled (Lanczos, long edge exactly 1024 when larger) and re-encoded
  as metadata-free PNG before the blob write; the image cache stores a pointer to the blob (a missing blob
  regenerates once). Cache key: model, prompt name/version/text hash, input image shas, aspect, size, slot, salt
  (`fresh=true` salts with the run id).
- Slice 5: API shape additions: `RunAccepted.run_url`; `GET /v1/runs/{id}/events` also accepts `?after=<seq>`
  (for clients that can't set `Last-Event-ID`); events carry extra optional fields (`candidate.created.status`,
  `native_width/height`, `reason`; `run.finished.reason`); `GET /v1/meta/run-events` carries the union schema.
  `runs.approved_candidate_id` holds the gate-passed candidate; the human decision (slice 13) sets the status.
- Slice 5: `required_text` validation (422 `invalid-text`: NFC, outer whitespace trimmed, <= 80 chars, <= 3
  lines, no control/bidi/zero-width/tag/format characters except ZWJ/ZWNJ) lands now rather than in slice 25.
  Evaluations are persisted from the skeleton on (`evaluator_version` `ev-0.1`, technical checks only).
- Slice 5: the fake client and synthetic test fixtures render text with a wide-coverage system font
  (`backend/domain/adstudio/fonts.py`: Arial Unicode on macOS, DejaVu/Noto on Linux, Pillow's default otherwise)
  until slice 11 bundles Noto under `assets/fonts/`.
- Slice 8: planner tables live in `backend/domain/adstudio/data/` (`iso3166.yaml` all 249 codes,
  `countries.yaml` ~95 markets with latitude/climate/wet months/currency/script/OCR langs/city aliases/default
  cues, `seasons.yaml` months + named seasons + 27 holidays with per-country/per-hemisphere dates,
  `locale_policy.yaml`, `cue_defaults.yaml`). `geography_code` must be an ISO code (422 `unknown-geography`);
  an ISO code without market data resolves with hemisphere/climate `unknown` (a month gives season
  `unspecified`), the conservative policy block, the default cue table (no planner call) and a `should`
  (not `must`) geo-plausibility check. `geography_detail` is used only when it matches the alias table (a city
  in another country is 422 `geography-mismatch`; unknown free text is dropped and never reaches a model).
- Slice 8: an unknown season term goes to `season_resolve` v1 (wrapped untrusted text) whose output is limited
  to months 1-12, a closed set of season names or a holiday id from `seasons.yaml`; anything else, a low
  confidence answer or no configured model is 422 `unknown-season`. Resolution runs at `POST /v1/runs` (before
  any spend) and is stored in `runs.config.resolution`, so the run does not call the model again.
- Slice 8: `CreativeSpec` (`spec_version` `cs-1`) adds `policy` (`LocalePolicy{source, avoid, notes}`),
  `planner_model`, `planner_note` and `season.holiday_ids` to ai-design §13. Planner cues are sanitised (quote,
  guillemet and bracket characters stripped) before they reach the image prompt. Until the product profile
  (slice 10) exists the product category is generic and the text zone is always the top band.
  `injection_signals` on the required text sets `mode=overlay_only`: the text is then never sent to the image
  model (clean-plate prompt), and such runs end `needs_review` until the overlay lands (slice 11).
- Slice 8: `regex` (already in the lock transitively) is now a direct dependency (`textnorm` uses `\p{Sc}`).
- Slice 9: `numpy` 2.5.3, `pytesseract` 0.3.13 and `rapidfuzz` 3.14.6 added. SSIM for the `not_copy` check is
  a small numpy implementation (uniform 7x7 window at 256 px width) rather than scikit-image.
- Slice 9: thresholds live in `backend/domain/adstudio/evaluator/evaluator_config.yaml` (`evaluator_version`
  `ev-0.2`). The verdict covers the dimensions implemented so far (technical, text); product and context join
  it with the vision slice (10), so until then a run can pass on technical + text alone.
- Slice 9: OCR is Tesseract only (Apple Vision stays deferred, slice 31). Each read goes through
  `guarded_call(kind="other", model="local:tesseract-5.5.3")`, so it is timed, in the ledger and cached by
  (engine, lang, psm, sha256 of the exact pixels) in the runtime cache (`model_cache` in the app). Language:
  the market's Latin-script packs first, then `eng`; other scripts map to `jpn`, `hin`, `kor`, `chi_*`, ...;
  no installed pack or no Tesseract -> the text dimension is `unverified` (fails closed to `needs_review`).
- Slice 9: stray text excludes words that match the reference photo's own OCR read (ratio >= 80, or a
  partial match >= 90) until the product box from the vision profile (slice 10) can exclude the product
  region; stylised label text can still be flagged meanwhile. Extra words inside the zone beyond the aligned
  window are not yet a failure. Flagged (`overlay_only`) text fails with `overlay_pending` until the overlay
  (slice 11). The VLM read-back is a `TextReadback` protocol with veto-only logic; the default `NoReadback`
  makes no call (the vision reader arrives in slice 10).
- Slice 9: observed OCR limits at the strict `cer_max=0`: Tesseract reads `°` as `"` (B15 "−20°C") and, with
  the fake's font, `4th` as `Ath` (B09). Those fail text today and will route to repair/overlay.
- Slice 9: the fake image client adds mild grain so its flat gradient is not flagged as a placeholder.
- Slice 10: `scikit-image` 0.26.0 added (with scipy 1.18.1) for `rgb2lab` / `deltaE_ciede2000`. OpenCV is not
  added: GrabCut is replaced by a border-colour background mask (pixels > dE76 12 from the median border colour of
  the crop; the whole crop when the mask is < 20%), k-means is a seeded numpy k-means++ (k=3, 4096 sampled
  pixels), and ORB keypoints and the label-token recall of §5.3 steps 4-5 are deferred (identity rests on colour +
  the VLM checklist). The colour rule adds a second clause to §5.3: besides the weighted ΔE <= 18, no dominant
  colour with weight >= 0.10 may drift past ΔE 25, because the weighted mean dilutes a recoloured part (P1's red
  band under a +120° hue shift scored 16.3 weighted but 36 on the red colour; 20%-darker lighting stays <= 7).
- Slice 10: `LlmGateway.run_vision(prompt, images=, output_type=, text=, labels=, key_parts=)` runs pydantic-ai
  with `BinaryContent` parts on `VISION_JUDGE` at temperature 0, always cached by (model, prompt name/version,
  output schema, text, sha256 of every image, key parts = evaluator version) and recorded in the ledger as
  `kind=text`, `operation=vision.<prompt>`. A `FileCache` runtime replays without a key; a miss raises
  `CacheMissError`, which the evaluator re-raises instead of treating as "judge down". Vision calls use the text
  timeout (`LLM_TIMEOUT_S`) and the gateway's single `VISION_JUDGE` (no `LLM_VISION_ALT` fallback yet).
- Slice 10: `VISION_CLIENT=gemini|fake` added. `fake` gives scripted favourable verdicts and locates the product
  where the fake image client pasted it (key-less dev and tests only). `gemini` without a key is "unconfigured":
  product and context are `unverified`, so every run ends `needs_review` (the reconciliation's fail-closed rule).
  The integration tests use `fake`; `test_judge_down_fails_closed_to_needs_review` covers both unconfigured and
  a mid-run timeout.
- Slice 10: prompts `product_profile` v1, `ad_inspect` v1, `context_judge` v1. `ad_inspect`'s checklist adds
  `single_instance` and `is_hero` to A.8's list (the slice asks for "no duplicate" and "is the hero"). The rubric
  and the product summary are sent inside `<untrusted>` wrappers. Checklist `unsure` fails the gate
  (low_confidence); a missing answer is `unverified`. Repair hints are built from check ids, code labels and
  numbers only, never from the VLM's evidence prose (production-readiness T2); the evidence is shown to users.
- Slice 10: the context checks are `spec.rubric` (context dimension) plus `ctx.no_avoided_elements` compiled from
  `spec.policy.avoid` (must, pass = "no"), compiled at evaluation time rather than stored in the spec. Should-checks
  are persisted as `evaluation_checks` rows (with `evidence_data.severity`) but only must-checks gate; the
  context score is the should-check pass rate.
- Slice 10: product profile stored in `products.reference_facts` as `{status: verified|not_product|unverified,
  profile_version: pp-1, prompt, model, <ProductProfile fields>, label_injection_flags}` with
  `facts_version = pp-1+product_profile@1`. It is computed at upload when a judge is configured (upload latency
  grows by one vision call) and otherwise lazily at the start of a run; an outage stores `unverified` and is retried
  next time. Label text runs through `injection_signals` and is only quoted as "label text to preserve". The
  profile's category, colours and label text now also fill the `ad_generate` prompt's product block.
- Slice 10: the evaluator (`ev-0.3`) always reports all four dimensions once technical passes. Text now excludes
  OCR words inside the VLM product box (+2%) from the stray check, and the `ad_inspect` blind read-back vetoes the
  headline and flags text outside the zone and product box (`vlm_stray_text`, label text from the profile
  excepted). An empty VLM transcription is treated as "no opinion" rather than a veto.
- Slice 11: `fonttools` 4.66.0 added (glyph coverage from each font's cmap). Fonts in `services/api/assets/fonts/`
  (5.3 MB, OFL 1.1, provenance and sha256 in `SOURCES.md`): `NotoSans-Bold.ttf` and
  `NotoSansDevanagari-Bold.ttf` from `notofonts/notofonts.github.io` (hinted static TTF) and `NotoSansJP-Bold.otf`
  from `notofonts/noto-cjk` `Sans/SubsetOTF/JP` (4.4 MB, already the upstream JP subset, so not subset further).
  No Hangul/Thai/Arabic font is bundled. `PIL.features.check('raqm')` is True in the venv (Pillow 12.3.0 wheel).
- Slice 11: `POST /v1/runs` returns 422 `unsupported-script` when no bundled font has a glyph for a character of
  `required_text` (whitespace and ZWJ/ZWNJ excepted), so Korean, Thai, Arabic and Chinese-only hanzi outside
  the JP subset are refused at input. Each character is drawn with the script's preferred font or, failing that,
  another bundled font (Noto Sans Devanagari has no Latin letters, so mixed Hindi/English text uses both).
  A script that needs shaping without libraqm is refused at render time (`overlay-script-unsupported`).
- Slice 11: the overlay band is **opaque** (ai-design §4.4 says alpha 0.92: at 0.92 a wrong native headline
  visibly ghosts through the band). The text colour is black/white by WCAG contrast against the composited band
  (every golden brief >= 6.3:1), not the spec's `text_color`. Fit is a binary search per exact line breaking
  (spaced scripts break at spaces, unspaced scripts between grapheme clusters, explicit newlines are kept) with
  8% padding and a 28 px floor (`overlay-does-not-fit` below it). `Overlay.render()` returns a PNG of the same
  size; `Overlay.verify()` returns `ocr` / `construction` (no OCR pack for the script) / `failed` and only looks
  at the headline checks (stray text elsewhere is the router's precondition).
- Slice 11: `Repairer.plan(evaluation, spec, facts=, state=RepairState(...)) -> RepairPlan` and
  `Repairer.request(plan, spec, reference=, candidate=) -> ImageRequest` are the seams for the slice-12 loop.
  Repair prompts `ad_repair_product` v1, `ad_repair_context` v1 and `ad_repair_text` v1 (`llm/prompts/ad_repair.py`)
  are filled from `FailureCode`s (check ids, measured numbers, colour names, spec cues, escaped OCR literals),
  never from VLM evidence prose. `TEXT_REPAIR_ATTEMPTS=1` added. Routing order adds one row to §4.3: a
  product/context dimension that is `unverified` (judge down) goes straight to `needs_review`. A text-only
  failure whose product box overlaps the zone by > 5% or with stray text goes to a clean plate (the overlay's
  preconditions); with no repair left it is `needs_review`.
- Slice 11: the text evaluator gained two deterministic zone reads, kept only when they give a lower CER (the
  "min over engines" of §5.2): the exact zone contrast-stretched (Tesseract `hin` misread black Devanagari on
  a teal band, CER 0.22, and reads it exactly once stretched) and, for market-pack reads like `nor+eng`, the
  script's own pack alone (`nor+eng` reads "°" as "*", `eng` reads "−20°C" exactly). OCR reads per image go from
  4 to 5-6.
- Slice 12: the orchestrator (`pipeline.Orchestrator`, `pipeline_version` `orchestrator-1`) generates the N
  first-round candidates and evaluates them in parallel, writing rows/events in slot order. Selection and the base
  choice follow ai-design §4.2/§4.3; the repair loop calls `Repairer.plan()` with the budget projection and
  re-plans with `can_afford_image_call=False` when the routed call does not fit. When every first-round call is
  safety-blocked, one fresh `regenerate` candidate is tried (it counts as a repair). A non-safety image-call failure
  (breaker open, retries exhausted, 5xx) is tried once on the other image model if its price fits the budget.
- Slice 12: candidate `kind` keeps `initial` for first-round/regenerated candidates (the task's "candidate"; the web
  client already keys on `initial`) and gains `clean_plate` (migration `0003_orchestrator`); `repair` and `overlay`
  are unchanged. Lineage is `parent_candidate_id` (on the row, in `CandidateView` and in `candidate.created`).
  `0003_orchestrator` also adds `runs.best_candidate_id` (the candidate a `needs_review` run shows for the human
  decision; `RunDetail.best_candidate_id`) and makes evaluations unique per `(image, run, candidate, version)`, since
  two candidates can share an image.
- Slice 12: `RunBudget` (`domain/adstudio/budget.py`) reserves the **list price of the configured model**
  (`IMAGE_MODEL_*`, e.g. $0.034 / $0.067) before every image call, even when the fake client serves it at $0, so
  budget behaviour is testable offline; a cache hit refunds its reservation, and text/vision spend is read from the
  ledger rows of the run. `MAX_IMAGE_CALLS=5` added. Budget or image-call exhaustion routes a text-only failure to
  the overlay (ai-design §4.3) and anything else to `needs_review` (`run.finished.reason` = `budget` |
  `image_calls`); the **deadline** ends `needs_review` at the next step boundary even if an overlay were possible
  (no hard kill of an in-flight call; each image call keeps its own 60 s timeout). `budget.warning` is emitted once
  at 80% of the cap (`reason=threshold`) and when a call is refused (`exhausted` | `deadline`).
- Slice 12: the daily cap is checked at `POST /v1/runs` (before any row or model call) against the ledger's
  `SUM(est_cost_usd)` since 00:00 UTC: 429 `daily-budget-exceeded` with `Retry-After` until midnight UTC.
- Slice 12: the overlay candidate is re-evaluated by `Evaluator.evaluate_overlay()`: technical + text (OCR, no VLM
  read-back) on the new pixels, product and context inherited from the base (ai-design §4.4 step 6; checks marked
  `inherited`). `verification=construction` (no OCR pack) passes text through an `overlay_construction` check.
  Clean plates are evaluated with `text_mode=overlay_only` (text = `overlay_pending`), so the stray-text/zone-empty
  check of §2 step 12 is not yet run on them. Injection-flagged runs now end `passed` / outcome `overlay`.
- Slice 12: RunEvent additions (all optional, additive): `candidate.created.parent_candidate_id` and kind
  `clean_plate`; `repair.started.{action,row,model,attempt}` (`instruction` is the router's code-built reason, never
  VLM prose); `fallback.applied.{from_candidate_id,verification}`; `budget.warning.{reason,image_calls,
  max_image_calls}`; `run.finished.best_candidate_id`.
- Slice 12: `FAKE_IMAGE_SCRIPT` (fake client only) scripts failures per request label (`ImageRequest.label/slot`):
  `ok|typo|recolor|stray|blank|block`, counted per run id and label; the script string joins the image cache key.
  A `repair_text` edit on the fake repaints the candidate's headline band. `DbCache.put` is now a Postgres upsert
  (parallel evaluations OCR'd identical crops and raced on the same cache key).
- Slice 13: `POST /v1/runs/{id}/decision` `{action, reason?, actor="reviewer"}` (actor `^[A-Za-z0-9._@-]{1,48}$`,
  no auth yet) decides only `passed` / `needs_review` runs (409 `run-not-decidable` otherwise, including a second
  decision); approving `needs_review` needs a reason of >= 3 characters (422 `override-reason-required`) and a
  candidate image (409 `nothing-to-approve`). **Rejecting a `passed` run is also recorded as an override** and
  labelled (`overall_ok=false`, dimensions null); an approve override labels the image all-ok. Labels use
  `rubric_version=decision-1` and `notes=reason`, upserted per `(image, human:<actor>)`. Approval sets
  `runs.approved_candidate_id` (the gate's candidate, or `best_candidate_id` on an override, so the "approved only
  if overall_pass" invariant now reads "... or a human override in `run_decisions`") and the candidate status
  `approved`. Each decision appends a `run.status` (approved | rejected) event; the SSE stream still closes at
  `run.finished`, but a replay includes the rows logged after it.
- Slice 13: `POST /v1/runs/{id}/cancel` returns `RunCancelled{run_id, status, cancelled}`; 409 `run-not-active` for
  a run that is not queued/running. The task is cancelled (`PipelineDeps.cancel_requests` makes `execute_run` end it
  `cancelled` rather than `interrupted`), then `mark_cancelled` covers a queued task that never started.
- Slice 13: `GET /v1/runs` returns `Page[RunSummary]` (brief fields, cost, latency, repairs, `thumbnail_url` of
  the approved else best candidate); `status` and `origin` are validated enums (422). `/v1/ops/summary` gains
  `runs: RunMetrics` over the last `run_window` runs of the tenant: count, by_status, approved_rate (human
  approved / finished), gate_pass_rate, first_attempt_pass_rate, mean_repairs, overlay_rate, override_count,
  cost_per_approved_ad (spend of all finished runs / approved), cost_per_gate_pass, p50/p95 ms (pipeline latency of
  gate-passed runs; the human's decision time is not included), active, queued. `/ready` gains `adstudio`: rows for
  the candidate/repair image models and the vision judge (spec, client, ledger model id, configured, breaker) and
  OCR (engine, version, pack count, per-script support for Latn/Deva/Jpan/Hang/Hans, libraqm); its own
  `status` is `degraded` when a row is unconfigured or OCR is missing, and the top-level `/ready` status is
  unchanged.
- Slice 13: the `0002_adstudio` downgrade now clears `model_calls.run_id` before dropping `runs`, so a downgrade
  followed by an upgrade can re-add the foreign key (it failed once any run had ledger rows).
- Slice 14: golden tooling lives in `backend/golden/` (dataset, context, runner, export, importer, harness,
  sheet) with the CLI in `backend/cli.py` (`python -m backend.cli golden run|export|import|plant|sheet`).
  `briefs.golden_key` is `<brief>@<pipeline version>@<image client>` (e.g. `B01@orchestrator-1@gemini`), so a
  fake dry run never satisfies or blocks the live run. Golden runs use `fresh=false` (image calls cached) and a
  **batch budget** (`--budget`, default $5: stop new runs when golden spend + $0.25 per in-flight run would pass
  it); the API's $3/day cap is not applied to the CLI. Runs go through `RunRunner` (heartbeats) at concurrency 2;
  a stale active run from a crashed batch is marked `interrupted` and re-run.
- Slice 14: files: `outputs/<brief>-{nat,final}.png` + `manifest.jsonl` (spec, product facts, model, prompt
  version, candidate kind/attempt/slot, pipeline verdict, product box, generating-call cost/latency from the
  ledger, run status/outcome/repairs/cost/latency). E-nat = attempt 0, lowest slot with an image; E-final = the
  gate's approved candidate else `best_candidate_id`. `export` also writes `cache/verdicts.json` by re-running
  the evaluator over nat+final with a recording cache backed by the DB cache: a **live** judge call happens
  only for answers the run did not already pay for (in practice the full re-evaluation of overlay finals, which
  the pipeline scores with `evaluate_overlay`); `--no-live` forbids it. Export merges human labels from the DB
  into `labels.csv` and never overwrites existing rows. The DB `labels` table has no technical column, so
  import stores technical only through `overall_ok`.
- Slice 14: `FileCache` snapshots gained a `meta` block (OCR version + packs, vision client + model, evaluator
  version); `TesseractOcr.lang_for` now reads packs through `languages()` so the replay's `SnapshotOcr` rebuilds
  the recorded cache keys **without a Tesseract binary**. The fake vision client is routed through
  `guarded_call` + the runtime cache (`CachedVisionClient`) so dry runs exercise the same record/replay path.
  An OCR cache miss is collected and raised (`StaleSnapshotError`) because `evaluate_text` turns OCR errors into
  `unverified`.
- Slice 14: `labeling_sheet.html` embeds 900 px JPEG previews (self-contained, `--link` links the files); the
  page template is `backend/golden/labeling_sheet.template.html`. It carries no evaluator output (no verdicts,
  run status, outcome or repairs); answers autosave in localStorage and download as `labels.csv`.
- Slice 16: no OpenCV: `cv2.inpaint` -> `skimage.restoration.inpaint_biharmonic` on a 2x-downscaled crop (only
  masked pixels replaced); GrabCut -> the evaluator's border-colour background mask. `text_typo` re-renders
  the zone with the overlay renderer (Noto, basic layout for Latin so it is byte-reproducible) instead of a
  "similar font" over an inpainted zone; `text_stray` is plain text in the colour contrasting its background
  (Tesseract misreads heavily outlined text). `product_swap` uses the most colour-dissimilar golden product
  (CIEDE2000 on the reference photos); `product_logo_erased` erases product pixels far (dE76 > 25) from the
  product's body colour, only on products with a label/logo (P1, P2, P5 or facts with label text).
  `product_duplicate` fits a 0.3-0.6x copy into the free side. Technical: upscale to 2048, centre crop to the
  wrong aspect, flat mean-colour image, JPEG truncated to 60%. Controls: identity, JPEG q80, brightness +8%,
  both. Seeds per item = sha256(seed:item id); pure items are regenerated from params at eval/import time and
  their sha is checked (a mismatch is a warning, not an error).
- Slice 16: the 6 generated items (`context_season` 4: B01/B02/B06/B10 flipped summer<->winter;
  `context_geo` 2: B04 -> Tokyo, B13 -> London) use the brief's own spec with a forced draft/geo and cleared
  negatives, through `Generator` + `ad_generate` on `IMAGE_MODEL_CANDIDATE` (cached, ledger-recorded), and
  are judged against the brief's **original** rubric. Only their context label is set (fail); the other
  dimensions come from a human `labels.csv` row with `set=plant` (the sheet includes them). `--assume-pass`
  (unlabelled E-nat = all-pass bases) is for dry runs only.
- Slice 16: duplicates and context contradictions are caught by the VLM only (no deterministic duplicate
  check, ORB deferred), so their `test_evaluator_flags_*` tests script the fake judge's verdict and assert the
  gate reacts; the other classes are caught by deterministic checks on synthetic ads from the golden photos.
- Slice 17: `make eval` = `python -m evals` now runs the golden eval (the scaffold's gateway smoke check is
  `python -m evals --smoke`). Suites `evals/suites/evaluator_meta.py` and `pipeline_quality.py` compute from
  typed `ItemRecord`s (`domain/adstudio/evalreport.py`, shared with the API and importer). An `unverified`
  dimension counts as FAIL; a dimension not assessed (technical failed) is excluded and counted. After-repair
  pass = runs ending `passed` or human-`approved` (golden runs are not human-decided). Shipped exact text =
  replayed `ocr_cer` = 0, or `construction` for overlay finals without an OCR pack, reported separately.
  The report also carries hemisphere accuracy (planner, 12 cases) and network attempts during the replay
  (process socket guard) so every PRD criterion has a row. `make eval` refuses to run (no report, no
  baseline) before golden outputs exist. `baseline.json` stores the criteria values of the first report.
- Slice 17: `/v1/evals/summary` and `/v1/evals/items` read the newest `eval_reports` row (`make eval` writes
  one when the DB is reachable; `make golden-import` registers `latest.json`); items come from the report and
  are joined to `images` by sha256 for URLs (null until imported); cursors are opaque offsets. `EvalSummary`
  is a superset of the contract (dry_run, κ, per-split, planted classes, criteria, counts).
- Slice 17: judge stability = `make eval-record RERUN=10` records `cache/verdicts_rerun.json` (10 E-nat items
  judged again with every cache bypassed); `make eval` compares their VLM checks.
- Slices 14-17 dry run: `make golden-dryrun` runs the whole chain on the fake clients against the **test
  DB** with blobs/outputs/reports under `services/api/var/golden-dryrun/` (gitignored); reports are marked
  `dry_run` / "FAKE DRY RUN".
- Slice 25: `/v1/llm/complete|stream` stay in the OpenAPI schema (so the generated client does not depend on
  the env) but a router dependency answers 404 `not-found` unless `LLM_DEV_ROUTES=true`, or it is unset and
  `APP_ENV=dev`. Tests default `llm_dev_routes=True`; `test_llm_endpoints_disabled_in_prod` covers the rest.
- Slice 25: per-IP admission limits are in-process sliding windows (`backend/http/limits.py`), keyed by the
  socket peer (`X-Forwarded-For` is not trusted; behind a proxy every client shares one address). New settings
  `RUNS_PER_HOUR_PER_IP=10`, `RUNS_CONCURRENT_PER_IP=2`, `PRODUCTS_PER_HOUR_PER_IP=30` (0 = off; tests default
  to off). 429 `rate-limited` + `Retry-After` (until the oldest slot leaves the hour, or 30 s for the
  concurrency cap). A slot is taken inside `prepare` (only a new run; an idempotent replay never counts) and
  given back when the request then fails (422/429/503), so a rejected brief or upload does not use the hour.
  A run is "concurrent" while the runner holds its task.
- Slice 25: only `google:` (and the offline `test:`) specs may hold a role that receives customer images
  (`VISION_JUDGE`, `IMAGE_MODEL_CANDIDATE`, `IMAGE_MODEL_REPAIR`): `validate_model_roles` refuses others at
  startup, and `LlmGateway.run_vision` refuses them at call time (503 `image-provider-refused`); the Gemini
  image client already refused non-`google:` specs. Groq Llama-4 / OpenRouter Gemini are no longer accepted
  as `VISION_JUDGE` even though they see images.
- Slice 25: `required_text` also rejects hidden line breaks (U+2028/U+2029, `Zl`/`Zp`) and blocklisted terms
  (422 `text-policy`, RT-09): `domain/adstudio/data/text_blocklist.txt`, whole words after NFKC/casefold and
  undoing digit/symbol swaps. The list deliberately holds explicit terms and profanity only (no slurs yet, no
  words with common innocent uses such as "naked", "nude", "escort"); extending it is a human review task.
- Slice 25: `escape_literal` now also turns straight double quotes into “ ” and every line-break character
  (`\r \n \t \v \f` U+0085 U+2028 U+2029) into a space; prompt versions bumped: `ad_generate` 2,
  `ad_repair_product` 2, `ad_repair_text` 2 (old image-cache entries no longer match).
- Slice 25: `injection_signals` `role_override` also matches "ignore all rules/instructions/checks",
  "approve/pass this ad|image" and "mark all checks pass" (RT-07's planted label).
- Slice 25: logging writes to the current `sys.stdout` (so cached loggers follow a swapped stream) and the PII
  redaction processor recurses into dicts/lists. `POST /v1/runs` logs `run_created` with the required text's
  length and a sha256 prefix only.
- Slice 25: production-readiness ids already covered under other names (no duplicate test added):
  `test_judge_cannot_pardon_deterministic_fail` = `test_vlm_cannot_pass_failed_deterministic_check`;
  `test_repair_prompt_uses_failure_codes_only` = `test_repair_routing_table` (asserts the judge's prose never
  reaches a repair prompt); `test_job_budget_stops_repair_loop` = `test_budget_cap`;
  `test_daily_cap_refuses_new_job_429` = `test_daily_cap_blocks_new_runs`; `test_image_timeout_not_retried` =
  `test_image_call_no_retry_on_timeout`; upload T4 ids = `tests/integration/test_products.py`. The
  duplicate-brief case is `test_duplicate_brief_makes_zero_new_image_calls`: a new key makes a new run whose
  image steps are all cache hits (0 new image calls); it does not return the existing run.
- Evals/batch API follow-ups (after slices 22/23): `EvalItem` gains `product_name`, `geography_code`, `season`,
  `required_text`, `aspect_ratio`, `tags` (read from `data/golden/briefs.yaml`, not stored in the report) and
  `run_id` (the newest golden run with a candidate showing that image; null for planted items). `EvalSummary` gains
  `pipeline` (the full `PipelineMetrics`: `mean_repairs`, `status_counts` = run status counts, ...),
  `agreement_by_dimension` (per image set `nat|final`, then per dimension, with κ), `stability`, `hemisphere`,
  typed `provenance` (judge client/model, OCR engine/version/language packs, image clients/models/prompt versions,
  evaluator prompt versions, snapshot file name only, never its absolute path) and `warnings` (FAKE DRY RUN,
  missing labels, stale snapshot, stale report vs the live evaluator version or the golden files' dataset hash,
  then the report's own). New read-only `GET /v1/golden/sources` returns `SOURCES.md` verbatim plus the parsed
  product table (id, file, role, source title/url, author, licence/url, share_alike, name). The architecture
  contract table was not edited (lane A); these are additive fields.
- `golden import` now creates or links golden runs from `outputs/manifest.jsonl`: linked when the run id (or a
  golden run owning the E-final image) exists; otherwise the brief (by `golden_key`), the run under its original id,
  only the E-nat and E-final candidates (E-final's parent = E-nat; intermediate repair candidates are not in the
  manifest), the pipeline's evaluation per candidate (dims/verdict from the manifest; checks copied from the latest
  report when its evaluator version matches) and replay events ending in `run.finished`. Imported runs carry
  `config.imported` and `created_at`/`finished_at` = import time. The report writer adds `ocr_engine`,
  `ocr_version`, `ocr_languages`, `image_prompt_versions`, `prompt_versions`, `snapshot_recorded_at` and
  `snapshot_evaluator_version` to `provenance` and a "stale snapshot" warning. `test_golden_manifest_matches_files`
  now deletes its B01/B20 golden runs at the end: they pointed at tmp blobs and made a following
  `make golden-dryrun` (same test DB) skip those briefs and fail to export.

## Slice 1 spike — first attempt (2026-09-25)
- Key valid (models list HTTP 200, 61 models). Present: `gemini-3.1-flash-lite-image`, `gemini-3.1-flash-image`,
  `gemini-3.8-flash` (vision judge, text call HTTP 200).
- Image calls blocked: HTTP 429 `RESOURCE_EXHAUSTED` on `generate_content_free_tier_requests`
  (`…PerDayPerProjectPerModel-FreeTier`): the project has no billing, so image-model quota is 0.
- Interactions API calls timed out at 90 s in the same state (re-test once billing is on).
- Pending after billing: working surface, native 4:5 size, latency, cost. Script: `services/api/scripts/spike_image.py`.

## Slice 1 spike — result (2026-09-25, after prepay billing)
- Billing: AI Studio **Prepay** project. Free Trial Cloud credit is not usable for the Gemini API; prepaid balance
  must be > 0 before eligible Google Developer Program credits apply (Google billing docs).
- Working surface: **Interactions API** for both models (`IMAGE_API=interactions`); the earlier timeouts were the
  billing block, not the API.
- `google:gemini-3.1-flash-lite-image` 4:5 "1K" → **928×1152 JPEG**, 10.9 s. `google:gemini-3.1-flash-image` 4:5 →
  **928×1152 JPEG**, 20.1 s. Long edge 1152 > 1024, so the always-downscale step is required (as designed).
- Quality (B01-style prompt, P1 reference): product, logo and label preserved; headline "Summer Sale — 30% OFF"
  rendered correctly by both; both added an unrequested secondary product with its own printed text (a stray-text
  case for the evaluator).
- `VISION_JUDGE=google:gemini-3.8-flash` text call HTTP 200.

## First live golden run — delimiter fix (2026-09-25)
- Bug (B01, run `a12026bb`, `ad_generate@2`): both Gemini candidates drew the prompt's `«…»` delimiters around the
  headline, and text still passed with CER 0 (normalisation folded «» to `"` and dropped it; the best-window CER
  ignored everything outside the window).
- Prompt: `ad_generate` **v3** and `ad_repair_text` **v3** put the headline copy alone on its own prompt line(s) after
  a line stating how many lines follow ("one headline line per prompt line:"), then "End of headline copy." and "add
  no quotation marks, guillemets, brackets or punctuation that is not in the copy". The repair prompt puts the OCR
  read on its own labelled line too. New `escape_line` (line breaks -> space only) is used for those lines, so the
  copy's own «, » and `"` reach the model unchanged (the old ‹ › swap would have made the model draw the wrong glyph
  and fail exact text). `escape_literal` (line breaks + guillemet/quote swaps) stays for the inline `«literal»` data
  slots (label text, stray tokens), which still use « ». RT-11 now asserts the block is verbatim and that the only
  guillemets in the prompt are the copy's own.
- Evaluator **ev-0.4**: `textnorm.normalise(..., keep=)` keeps quote/guillemet/bracket characters (variants folded)
  that the required text does not contain (`delimiters_to_keep`). Content of each zone read outside the matched
  window counts as extra characters in `ocr_cer` (value = window CER + extra chars / copy length; `data.window_cer`,
  `data.extra`, `signals.zone_extra`): a delimiter glyph, or a token of >= `zone_extra_min_alnum` (2) letters/digits
  that is not a word of the copy, from a word with conf >= `stray_min_conf` (60) whose centre is inside the exact zone
  (not the 5% crop margin), read by >= `zone_extra_min_sources` (2) of the zone reads. The VLM read-back (veto only)
  compares with the same kept delimiters. No new check name (the web lane's labels and the "shipped exact text"
  metric keep working: extras make `ocr_cer` fail with a value > 0).
- Known gaps: a duplicated word of the copy ("Sale Sale") is not an extra; a single-character extra is tolerated;
  words centred in the zone's 5% crop margin are neither extras nor stray text. B01's `Li 12` read was foliage at
  y=232 (zone ends at 225), conf 57/28, so it is correctly ignored.
- `PIPELINE_VERSION` -> `orchestrator-2` (golden keys `B01@orchestrator-2@gemini`, batch label
  `golden:orchestrator-2`). The golden key does not include prompt versions, and the old B01 run ended `passed`, so
  without the bump `golden run` would have skipped B01 and `golden export` would have exported the old run. The old
  run stays in the DB under the `orchestrator-1` key (history); nothing reads it for export or the batch budget.
- Evidence: the two stored v2 B01 candidates re-evaluated with ev-0.4 (vision answers from the ev-0.3 cache, $0)
  both fail text: `ocr_cer` 0.0952 (window 0), "Not in the required text: '«', '»'", plus `vlm_readback`.
  Re-run `78428746` (`ad_generate@3`): headline drawn without delimiters on all 4 candidates, text passes on all 4;
  run `needs_review` after 2 product repairs (slot 0 recoloured the mug yellow; the others garbled the label's small
  subtext per the VLM). Cost $0.2287, 102.7 s.

## Label notes and stalled repairs (2026-09-25, human decision)
- `ad_inspect` **v2**: `label_text_preserved` split into `main_label_text_preserved` (brand name and large, prominent
  label text; gates product) and `label_subtext_preserved` (small secondary text). `evaluator_config.yaml`
  `product.note_checks: [label_subtext_preserved]`: a `no`/`unsure` there is a **note**, never a failure: the check is
  stored `passed=true` with evidence "Note (not a failure): minor label detail degraded. …" and
  `evidence_data.note=true` (the scorecard shows evidence on passing checks, so no schema or migration change);
  `signals.notes` on the product dimension. Eval report: `PipelineMetrics.minor_label_detail_rate` /
  `minor_label_detail_n` (final images with a note / evaluated final images), a row in the pipeline table;
  `CheckRecord.note`. Additive API fields, client regenerated.
- Stalled repairs: after a failing repair edit (not a regenerate), SSIM(output, parent) at the check resolution
  (`technical.image_similarity`) >= `repair.stall_ssim` (0.98, in `evaluator_config.yaml`) stops further image-model
  calls for the run; the planner then routes as if no call were affordable (overlay for a text-only failure,
  otherwise `needs_review` with reason `repair_stalled`). Fake image behaviour `echo` (returns its input) tests it.
- **evaluator_version ev-0.5.** Web: `evidence.ts` `quoted()` splits the OCR sentence on its fixed wording, so a read
  containing « » (drawn delimiters) is shown whole.
- `PIPELINE_VERSION` -> `orchestrator-3` (new golden keys; the `orchestrator-2` B01 run is superseded like the
  first one). `make golden-run BUDGET=7` passes `--budget` (default 5).
- First full live batch (`orchestrator-3`): P1's `reference_facts` were written by the offline `test:fake-vision`
  judge (cached during the keyless build; the golden upload is deduplicated by content and reused the row), so
  B01-B04 ran with "no printed text", no colours and a near-full-frame reference box. `profile.needs_profile` now
  also re-profiles facts from a `test:` model once a real judge is configured. `golden run --force` (`make golden-run
  FORCE=1`) starts a new run for a finished brief; export takes the latest, so it supersedes the old one (no rows
  deleted). B01-B04 were re-run that way.

## Composition dimension and golden v2 tooling (2026-09-25, ADR-007)
- **Generation.** `product_profile` **v2** (`PROFILE_VERSION` pp-2): `size_class`, `approx_max_dimension_cm`,
  `typical_surface`; pp-1 facts are re-profiled once any judge is configured (`needs_profile`), and
  `make golden-profile [FORCE=1]` re-profiles the golden products (done live: P1 mug 11 cm, P2 peanut jar 30 cm, P3
  sneakers 30 cm, P4 bottle 25 cm, P5 tube 15 cm). `sizing.py` replaces the fixed 0.45–0.6 product scale: framing
  (`close_up` 35–55 cm / `medium` 80–120 cm / `wide` 2–3 m of scene height) → scale = size / scene height, clamped to
  the hero band 0.18–0.6; a category keyword table sizes pp-1 facts. `creative_planner` **v2** returns `framing` and
  `scale_references`; spec **cs-2** `Composition` gains framing, scale range, size, surface and references (older specs
  still validate with 0.5). `ad_generate` **v4**: sized next to the named objects, photographed in the scene.
- **Evaluator ev-0.6.** Fifth dimension `composition`: `composition_judge` **v1** (third parallel vision call, the ad
  only) answers `comp.realistic_scale` and `comp.natural_integration` (must, unsure fails low_confidence);
  `comp.scale_sanity` compares the ad_inspect box area with the expected range (note outside it; fail only beyond
  `area_extreme_over` 4× / `area_extreme_under` 0.12×). **Deviation:** the ad_inspect / context_judge cache key part
  is now `judge_cache_version` (ev-0.5, `evaluator_config.yaml`) instead of the evaluator version, because their
  prompts and inputs did not change; this is what lets the v1 re-score reuse every recorded answer (40 live calls,
  all composition). `GatewayVisionClient(cache_version=...)`.
- **Schema/API (additive).** Migration `0004_composition`: `evaluations.composition_pass`, `labels.composition_ok`,
  `'composition'` in `ck_evaluation_checks_dimension`. Changed API fields: `Dimension` literal (+`composition`) in
  `RepairStartedEvent.target_dimension` and the `dimension` filter of `GET /v1/evals/items`; `EvaluationDoneEvent.
  dimensions` and `EvaluationView.dimensions` (`GET /v1/runs/{id}`) carry a `composition` key for ev-0.6
  evaluations; checks with `dimension="composition"`; `SpecSummary.product_scale` (`ProductScaleView`: framing,
  scale_min/max, size_cm, size_class, resting_surface, scale_references); `RunMetrics.dimension_pass_rates`
  (`GET /v1/ops/summary`); `EvalSummary.golden_version`, `EvalSummary.comparison` (`GoldenComparison` /
  `VersionSummary`) and `?golden_version=` on `/v1/evals/summary` and `/v1/evals/items`; `repair.started.action` may be
  `repair_composition`.
- **Repair.** Routing row `composition` (after product and context) → `repair_composition` on Flash,
  `ad_repair_composition` **v1** (size in cm and % of height, named objects, contact shadow, scene light, perspective,
  depth of field, no cut-out edge; codes only, no judge prose). Unverified composition → needs_review; the stall stop
  applies unchanged. `PIPELINE_VERSION` **orchestrator-4**.
- **Golden layout.** `data/golden/{outputs,cache,labels.csv,labeling_sheet.html}` moved to `data/golden/v1/`
  unchanged except `cache/verdicts.json` (re-recorded with ev-0.6: +40 composition answers); `labels_rubric1.csv`
  freezes the rubric-1 labels. v2 is the default out dir (`GOLDEN_VERSION`, CLI `--version`). `make eval` with no
  OUT/GOLDEN_VERSION runs `python -m evals --all-versions`: reports `<stamp>-<version>.md`, `baseline-<version>.json`,
  and the v1 → v2 comparison on the newest report. Rubric **v2** (composition; technical is file-level only);
  `make golden-sheet-v1-composition` writes `v1/labeling_sheet_composition.html`. Human overall agreement compares
  only the dimensions a label covers (rubric-1 labels leave composition out). Planted `comp_oversize` (≈1.8× in place,
  capped below the zone: v1 bases get ≈1.4×) and `comp_pasted`, 3 each, only from bases a human passed on
  composition, so none are built for v1 until its composition pass is labelled; generator `plant-2`.
- **Live check (not the batch).** B01, B13, B17 on orchestrator-4 (`golden run --only`, exported to a scratch dir,
  not `data/golden/v2`): all three passed first attempt, native text, 0 repairs, composition pass on E-nat and
  E-final (runs `66d4a1d0`, `23c73e39`, `cab29180`; $0.088 / $0.085 / $0.088, 53 / 37 / 44 s). Re-scoring the 20 v1
  finals with ev-0.6: 5 fail composition (B04, B06, B17, B19, B20; B17 "tube appears gigantic on the floor" next to
  chairs), E-nat 4 (B04, B06, B17, B19). Total ledger spend of the check (profiles, 3 runs, 40 v1 composition calls,
  export): $0.37.

## Recording fails loudly on failed judge calls (2026-09-26)
- `make eval-record` (v2, RERUN=10) hit 3 `ConnectError`s and then an open breaker for 29 vision calls. The
  evaluator turns judge errors into `unverified`, so `record()` wrote a snapshot without those 32 answers and
  exited 0, and `make eval` then failed with "32 evaluator answers are missing". The stability rerun lost all
  of its vision answers the same way, and its calls went to the in-memory ledger.
- `SnapshotRecorder.failed` lists the keys that went live without an answer, and `live` counts only answered
  calls. `record()` saves the successful answers (merged with the old snapshot) and raises
  `IncompleteRecordingError` (exit 2); re-running retries only the gaps. A complete recording is then replayed
  offline (`verify_snapshot`), so a record/replay key mismatch fails at record time. The rerun uses the DB
  ledger (`record_engine(use_db_cache=False)`). Test: `tests/unit/test_golden_record.py`.
- Re-recorded v2: 96 + 30 live vision calls, $0.378; snapshot 468 entries, rerun 82 (flip rate 1/211).

## Evaluator ev-0.7: failure-analysis fixes R2–R6 (2026-09-26)
Every change maps to `services/api/evals/reports/analysis-2026-09-26.md`. Reports `20260926T060231Z-v1`, `20260926T060249Z-v2`.
- **R2, generator plant-3.** The product classes (recolour, swap, duplicate, logo-erased) leave composition and context unasserted (None); their images are unchanged (byte-identical to plant-2), and so are the 6 generated context items and their label rows (`make plant KEEP_GENERATED=1`, CLI `--keep-generated`). Seamless blending was tried first and rejected: a numpy/scikit-image product mask (border-colour distance, random walker, colour-model classifier) could not separate a white mug from a white wall or a dark tube from dark wood, and the inpainted erasures left new smears.
- **R3, `composition_judge` v2.** `CompositionVerdict.scale` (the scene object whose real size is most certain, its size in cm, the product's implied size in cm) and `pasted_signs` (up to 3) come before the checks. New check `comp.scale_ratio`: implied / profile size must lie in `composition.scale_ratio_min/max`. Tuned on the calibration products only (v1+v2, P1–P3; grid lo 0.30–0.95, hi 1.10–2.60): 0.6–1.25, calibration F1 0.80 → 0.87, held-out unchanged (0.89). The judge's estimates cluster at 1.0, so the margin is thin. `pasted_signs_max` stays null (not tuned: the integration verdict has no false positives on v2). Repairs map a failed ratio to `COMPOSITION_SCALE`.
- **R4, OCR hardening.** `text.stray_min_alnum` 3 → 4. When OCR misses the copy only by a dropped accent, a missing tail of the last display line, or trailing junk, and the vision read-back reads the copy exactly, the zone is re-read at `text.reread_upscale` (4×): exact means pass, still different means `unverified` (never a pass on the VLM's word). Content before the copy, quotes or brackets, and substitutions are never benign, so every planted text class stays at 100%.
- **R5, read-back stray text.** Product boxes grow by `product_box_margin`. A read overlapping a box by half its area counts as on the product, and label text also matches with spaces and symbols ignored. This changed no current item: the remaining read-back false positives are prop text (a soap cube's "TRADITIONAL OLIVE OIL") and a swapped-in product that the inspector didn't box.
- **R6, `context_judge` v3 (v2 was never released).** Listed contradictions are examples, and any single one is enough; season cues must be dominant, not incidental. The v1 reason pass showed "people" as a distinct cause, so a compiled must-check `ctx.people_coherent` (`context.people_check_severity: must`) was added. Its wording was tuned once, on calibration: B09-nat was caught, B11 was not, and there were no false positives on v1 or v2.
- **Prompt bug.** `ad_generate` v5 / `ad_repair_text` v4 put the whole copy on ONE prompt line and state the number of display lines in words (v4's per-line prompt made v2 B13-nat draw "Only / AED 49"). `PIPELINE_VERSION` orchestrator-5. The golden images were not regenerated.
- **Test determinism.** The fake image client's grain used PIL `effect_noise`, which draws from C `rand()` global state, so a fixture ad differed with test order: `test_evaluator_flags_swapped_product` flipped at ΔE ≈ 18. The grain is now seeded numpy noise.
- **Cost.** Live judge calls for new keys, v1+v2, three recordings: $1.47 ledger. That is $0.68 for R3–R6 and $0.79 for the two people-check passes. The judge-stability rerun snapshot is stale (its keys predate ev-0.7): the report says "skipped", and `make eval-record RERUN=10` refreshes it for about $0.10.

## Evaluator ev-0.8, part 1: OCR ensemble (2026-09-26)
- **Second OCR engine: Apple Vision** (`AppleVisionOcr`, deferred plan item 31). Dependency `ocrmac` (macOS
  marker). **Deviation:** the engine calls `VNRecognizeTextRequest` through pyobjc directly instead of
  `ocrmac.OCR`, because ocrmac does not expose `usesLanguageCorrection`, and Vision's language correction
  autocorrects typos (measured: with it on, PL10's stray `*` and PL05's `Prpmavera` were "fixed"). Accurate
  level, correction off, word boxes from `boundingBoxForRange`. Reads go through `guarded_call("other",
  "ocr.apple_vision")`, keyed by macOS version + request revision + languages + pixel sha, so they are
  ledgered, cached and recorded in `cache/verdicts.json` (meta `ocr2`); replay needs no macOS
  (`SnapshotAppleVisionOcr`). `OCR_APPLE_VISION=true` (default) turns it on where available; the app warms it
  in the background at startup (first request loads the models). Tests set it off, so API tests behave the
  same on Linux; the live-OCR tests are parametrised over "Tesseract" and "Tesseract + Apple Vision" (the
  latter skipped off macOS). Vision languages: market Tesseract packs map to Vision codes (`VISION_LANGS`),
  Devanagari uses `hi-IN`.
- **Agreement rule** (`text.py` docstring): a text FAIL needs two readers (both engines, or one engine plus
  the read-back) to see the same error key (`chg`/`ins` at a copy position, an `extra` zone token or
  delimiter, a `missing` critical token). Otherwise the zone is re-read at `reread_upscale` (4x) with both
  engines; an engine still sees an error only if **every** one of its reads (all preprocessings, both sizes)
  shows it. Agreement fails, nobody passes, anything else is `unverified`. **Interpretation recorded:** the
  brief says "if it still disagrees, return unverified"; Tesseract reads PL29's bold "4" as "A" at every
  scale and preprocessing tried (2x-8x, Otsu, channel contrast), so a per-engine best read would leave PL29
  unverified forever. One of its 4x preprocessings reads the "4" (while misreading other letters), so the
  engine does not consistently see the error; that is the rule used. One reader only (a single engine and no
  read-back) keeps the old strict rule.
- **Stray text:** agreement between two readers fails. A token only one engine reads, which the other engine
  (also re-reading its region at 4x) and the read-back's blind transcription both lack, is a note (OCR noise,
  analysis E4). A read-back-only stray is re-read in its region with both engines: confirmed fails,
  unconfirmed is `unverified` (B18's embossed "TRADITIONAL OLIVE OIL" is unreadable by both engines at any
  scale, so OCR silence does not refute the VLM), except when the judge found no instance of the product
  (`product_missing`): label text on an unlocated product can't be told from stray text, and the failed
  product check owns the image (a note; PL18's swapped-in "Sun Bum"). Profile label strings now also exclude
  OCR stray words (they only ever excluded read-back strays).
- `readback_checks` / `readback_matches` / `benign_difference` are gone (the ensemble subsumes ev-0.7's veto
  and R4 special cases); `vlm_strays` is the public read-back stray filter. `evaluate_text(ocr2=,
  product_missing=)`, `Evaluator(ocr2=)`, `Overlay.verify(ocr2=)`. Health reports
  `engine="tesseract+apple_vision"` (no schema change).
- Test DB fix (coordinator request, its own commit): `0002_adstudio` nulls orphan `model_calls.run_id`s
  before adding the FK, and the integration session fixture truncates whatever app tables exist before
  and after migrating (`prepare_test_db`); regression test `test_interrupted_migration_test_recovers`.


## Evaluator ev-0.8, part 2: product colours in the prompt, hue drift measured (2026-09-26)
- **Root cause of the pink→red drift:** `color_name` picks the nearest of a few RGB anchors and names P4's
  #DC2E63 "red", so every P4 prompt asked for a red bottle. Product colours are now named by HLS hue family
  (`prompting.hue_family`: achromatic by lightness; tan/brown for low-chroma oranges; light/dark modifiers).
  `color_name` is unchanged for the band, text and scene palette.
- `ad_generate` **v6**: `same colours (pink #DC2E63, charcoal #494A49)` plus a COLOURS line: "the main
  colour is pink (#DC2E63), not red or magenta; the other colours are … Keep these exact hues". The "not"
  hues are the two neighbouring families. `ad_repair_product` **v3**: the same colour names and line, and
  drift problems read "red (#C81E32) instead of pink (#DC2E63)". `PIPELINE_VERSION` **orchestrator-6**. The
  v1/v2 golden images were not regenerated (brief); the unseen v3 set measures the effect.
- **Hue-drift note** (ev-0.8, product): for each reference dominant colour with CIELAB chroma ≥
  `hue_chroma_min` (20) and weight ≥ `cluster_min_weight`, the hue-angle difference to its matched ad
  colour; the largest ≥ `hue_drift_note_deg` (10°, not tuned) makes the `hue_drift` check a note. It always
  passes (user ruling: red-vs-pink is acceptable shade variation). Reported as its own rate
  (`PipelineMetrics.hue_drift_rate`, `hue_drift_n`, `hue_drift_by_product`; additive API fields, contract
  regenerated). The minor-label-detail rate now counts only the label-subtext note (it counted any note).
  Measured on v2 before recording: P4 13–22° on all 8 images (the E6 finding), P5 3/4 finals (pale-yellow
  cap), P3 1/4, P1/P2 none.

## Evaluator ev-0.8, part 3: Korean, Thai and Arabic (2026-09-26)
- Fonts (OFL, `services/api/assets/fonts/SOURCES.md`): Noto Sans Arabic Bold and Noto Sans Thai Bold
  (unmodified), Noto Sans KR Bold **subset** to Hangul + jamo + ASCII/Latin-1 + punctuation (4.8 MB → 2.0 MB,
  all layout features kept; Hanja dropped). `OFL-Arabic.txt`, `OFL-Thai.txt` added; KR is under `OFL-CJK.txt`.
- The overlay prefers them for `Kore`/`Hang`, `Thai` and `Arab`; the 422 `unsupported-script` now lists them
  as supported (Hebrew, Georgian, Hanja-only text etc. are still refused). libraqm 0.10.5 (FriBiDi,
  HarfBuzz 14.2.1) shapes Thai marks and Arabic joining.
- **Arabic is right to left:** the overlay resolves each line into directional segments (a small UAX #9:
  RTL paragraph; numbers and Latin runs LTR; a `%`, `,` or `.` touching a digit joins the number) and lays
  them out right to left, each font run shaped by raqm with an explicit direction. Without this, "30%" in
  Arabic copy was drawn "%30" (the `%` is in a different font run); both OCR engines caught it.
- OCR reading order is RTL-aware (`reading_order(rtl=)`, `RTL_SCRIPTS`), so Arabic words come out in logical
  order. Tesseract's Arabic packs are `ara+eng`: `ara` alone reads the Latin "30%" as "3090". Tesseract packs
  `kor`, `tha`, `ara` come from `tesseract-lang` (already installed). Apple Vision covers ko-KR, th-TH, ar-SA.
- Tests: overlay OCR-exact for "겨울 세일 30%", "ลดราคา 50%" and "تخفيضات الصيف 30%" (Tesseract, and the
  ensemble on macOS), the bidi segment order, raqm-less refusal for Thai/Arabic, and the 422 now on Hebrew.

## Evaluator ev-0.8, part 4: re-measured and frozen; ablation A1 (2026-09-26)
- `make eval-record RERUN=10` (v2) and `make eval-record GOLDEN_VERSION=v1`: every judge answer came from the
  snapshot (no prompt or judge-model change); the new keys are local OCR (Tesseract re-reads, Apple Vision:
  526 reads). Live judge calls: only the 10-item stability rerun (30 calls, $0.093; 0 flips in 231 checks).
  Snapshots: v2 639 entries (+ rerun 115), v1 309. Meta `ocr2` records Apple Vision `macos27.0-r3`.
- Nothing was tuned: no threshold changed after the ensemble rule, and labels are untouched.
- **Ablation A1** (`evals/suites/ablation_layers.py`, in `make eval`): each item's verdict per layer
  (`ItemRecord.layers`): *deterministic* (text re-evaluated with the read-back off: `harness.without_readback`,
  recorded and replayed like the main pass), *vlm* (read-back, count/checklist, rubric, composition judge),
  *combined*. P/R/F1 per dimension on each report, pooled v1+v2 on the newest one. A layer that owns every
  check of a dimension takes the dimension's own verdict (severities apply).
- **Disputed rulings** (agent log Stage 5, "reported as disputed", not implemented until now):
  `evaluator_meta.DISPUTED` (v1 B12-nat text, B06-nat product; v2 B13-nat, B16-nat text; derived plants
  inherit). Reports show P/R as labelled (criteria unchanged) and with those pairs left out.

## CI platform and Linux OCR (2026-09-26)
- **CI never ran.** `astral-sh/setup-uv@v10` does not exist (the latest major is `v7`), so `api` and `contract` failed
  before any step. gitleaks got a 403 listing the PR's commits because the token had no `pull-requests: read`.
- **Golden replay is platform-bound.** Evaluator and snapshot cache keys hash PNG bytes. Pillow 12.3.0's bundled
  zlib-ng compresses differently on x86_64 than on arm64, while the decoded pixels agree. On Linux, every golden
  product normalises to a different sha than the manifest, and `make eval` stops with "normalises to a different
  reference". The snapshots were recorded on an arm64 Mac, so the `api` job runs on `macos-latest` (arm64) with
  Homebrew `postgresql@17`, `pgvector`, `tesseract` and `tesseract-lang` (human decision). The platform-independent
  fix is pixel-based keys plus a re-key of the snapshots on the recording Mac; it is not done.
- **Linux OCR timeouts.** Ubuntu's Tesseract (5.3.4, an OpenMP build) spin-waits. Alongside the evaluator's other CPU
  work, a 0.2 s zone read measured 30–40 s and hit the 30 s OCR timeout, so clean ads came out `unverified`.
  `evaluator/ocr.py` now sets `OMP_THREAD_LIMIT=1` (default only; an explicit value wins), and the reads take
  0.2–0.7 s again.
- **Tesseract 5.3.4 vs 5.5.3.** On 5.3.4 the evaluator still fails `«…»` headlines on `ocr_cer`, but the closing `»`
  is read by too few passes to be listed. `test_text_fails_on_rendered_delimiters` asserts both glyphs, so it passes
  only on 5.5.x (the macOS job).

## Golden v3 and the repair probe (2026-09-26)
- **v3 layout.** `GOLDEN_VERSIONS` gains `v3`; a version dir may carry its own `briefs.yaml`
  (`GoldenPaths.briefs` prefers it; v1/v2 keep the shared file). Products still come from `data/golden/products`.
- **`golden repair-probe`** (`make golden-repair-probe`, `backend/golden/repair_probe.py`): each non-passing
  first-round draft of a version's latest golden runs goes through `Orchestrator.loop` in its own probe run
  (`batch_label=repair-probe:<pipeline>`, a brief copy without `golden_key`, so export and the batch spend
  ignore it). The stored spec is reused, the draft is re-evaluated (cached reads), and the per-run budget is
  charged for the N first-round calls first. It uses two private pipeline helpers (`_load`, `_finish`) rather
  than widening the pipeline API. Ledger rows carry the probe run id (`bind_run`, as in `execute_run`).
- **Linux run.** Tesseract 5.3.4 with all language packs (`tesseract-ocr-all`), Postgres 16 + pgvector, loopback
  auth `trust` in the container. v3's snapshot keys are x86_64 PNG bytes.
