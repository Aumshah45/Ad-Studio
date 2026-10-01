# Evaluator ev-0.8 — FROZEN

ev-0.8 is frozen: it is the evaluator of record for every evaluation after 2026-09-26 (the planned
unseen v3 set, first dropped for time, was reinstated and is scored with it; the freeze is recorded so
any later measurement uses exactly this evaluator). Nothing may change its thresholds, judge prompts or
judge cache identity without bumping `evaluator_version` and writing a new record here.
`tests/unit/test_evaluator_frozen.py` fails otherwise.

- **Frozen code:** git `5149ea5221ca2d677f81bb3618d46d21e81cc3f8` (the commit that recorded the ev-0.8
  snapshots and reports).
- **Reports:** `services/api/evals/reports/20260926T073233Z-v1.md`, `20260926T073328Z-v2.md`.
- **What ev-0.8 is:** the OCR ensemble (Tesseract 5.5.3 + Apple Vision `macos27.0-r3`, language correction
  off, + the VLM read-back; a text FAIL needs two readers to agree), the hue-drift note (measured, never a
  fail), ev-0.7's R2–R6 fixes. Judge `google:gemini-3.8-flash`.
- **Tuning:** none after the ensemble rule; labels untouched. Calibration P1–P3, held-out P4–P5.

Machine-readable record (read by the test):

```json
{
  "evaluator_version": "ev-0.8",
  "git_sha": "5149ea5221ca2d677f81bb3618d46d21e81cc3f8",
  "config_sha256": "d4b615bf2b6cfe8720824a37e1276ec9b5122834a5d1579b9086113e68636a08",
  "judge_cache_version": "ev-0.5",
  "judge_prompts": {"ad_inspect": "2", "context_judge": "3", "composition_judge": "2"},
  "vision_model": "google:gemini-3.8-flash",
  "ocr": {"tesseract": "5.5.3", "apple_vision": "macos27.0-r3"}
}
```

`config_sha256` is the sha256 of `evaluator_config.yaml` parsed and dumped as canonical JSON (sorted keys,
no whitespace), so comments may change but no value can.
