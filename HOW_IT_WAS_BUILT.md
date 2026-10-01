# How it was built

I built Ad Studio with a coding agent (Claude Code) doing most of the typing, and me doing the deciding: writing the
requirements, making every consequential call, reviewing the actual outputs, and measuring the results. This page
explains how that worked, what I decided myself, and what went wrong along the way.

## 1. Tools and timeline
- **Coding agent:** Claude Code (Anthropic), used throughout the build.
- **Process pack:** my own Claude Code workflow pack, prepared in advance. It contains workflow steps, checklists,
  templates and reference notes on techniques, and **no code, data or design** for this project. Everything in this
  repository was produced during the build.
- **Timeline:** built from an empty folder over three days, 2026-09-24 to 2026-09-26.
- **Runtime AI services used by the product** (product dependencies, not coding help): the Gemini API (3.1 Flash-Lite
  Image and 3.1 Flash Image for generation and repair, a Gemini Flash model for planning cues and vision judging), and
  Tesseract and Apple Vision OCR locally.

## 2. Requirements I supplied
The agent worked from written architectural requirements, produced and approved stage by stage:

| Document | What it specifies |
|---|---|
| [`docs/problem.md`](docs/problem.md) | The problem, use case, goals and constraints |
| [`docs/prd.md`](docs/prd.md) | Users, requirements matrix (every goal and constraint → deliverable), success criteria with targets, scope, assumptions |
| [`docs/architecture.md`](docs/architecture.md) | Components, data model, API contract, run lifecycle and events, failure modes, scaling path; the key architecture decisions recorded inline; a reconciliation section that overrides conflicting wording |
| [`docs/ai-design.md`](docs/ai-design.md) | Pipeline, model routing, prompts, evaluator design per dimension, golden dataset and planted failures, metric definitions, guardrails, budgets |
| [`docs/ux.md`](docs/ux.md) | Demo path, screens, every UI state, AI UX patterns, copy, visual system, accessibility |
| [`docs/production-readiness.md`](docs/production-readiness.md) | Threat model (OWASP LLM Top 10), security baseline, reliability, scale story, red-team cases |
| [`CLAUDE.md`](CLAUDE.md) | Engineering rules the agent followed (all model calls through one gateway, prompts versioned, typed outputs, no secrets, regenerate the API client) |

## 3. How I directed the agent
1. **Decode.** From the problem statement, the agent wrote a PRD with a requirements matrix and six open design
   questions with default answers. I adopted the defaults and recorded them as assumptions.
2. **Design.** Four design passes ran in parallel (system architecture, AI pipeline and evaluation, UX, production
   readiness), followed by a reconciliation of the conflicts between them and a critical review of the design.
3. **Decide.** I made the consequential choices at a checkpoint (see §4).
4. **Plan.** The agent turned the design into ordered vertical slices with acceptance criteria and named tests. I
   approved the plan.
5. **Build.** Separate build agents worked in lanes (API, web, data/evals), one slice at a time. Each slice was given
   its plan row and the design sections it relied on, had to pass `make check` (lint, types, offline tests, contract
   drift), and was committed on its own. I verified each slice before the next started.
6. **Review, label and evaluate.** After each live run I reviewed the ads myself and labelled them blind to the
   evaluator. My review of the first run (products too big and looking pasted on) led to a new *composition*
   dimension and a second run. The agent produced an offline evaluation report and a failure analysis of every
   disagreement with my labels; I decided which fixes to apply and made the labelling rulings, and the agent
   implemented and re-measured them.
7. **Unseen test and freeze.** I had the fixable limitations fixed: a second OCR engine, product colours in the
   prompt, more scripts. Then the evaluator was **frozen** (ev-0.8). I picked 16 hard briefs from an agent-drafted
   menu for an unseen v3 set, which was generated and scored with the frozen evaluator, and the live repair path was
   measured on its failing drafts. I labelled v3 blind; afterwards the agent showed me three drafts whose text visibly
   differed from the copy, I changed those three to fail, and both label versions are reported.

## 4. Decisions I made
Full list with reasons: [`docs/decision-brief.md`](docs/decision-brief.md).
- **Generation strategy:** Flash-Lite drafts → targeted Flash repairs → deterministic text overlay, rather than Flash
  best-of-N or always-overlay.
- **Inputs:** an ISO country picker plus a free-text season resolved by code tables, rather than fixed lists or
  free-text geography.
- **Scope:** core end-to-end loop first; extras only if they move a metric or the demo.
- **Test policy:** tests assert behaviour, and metric targets are reported rather than gating CI.
- **Golden products:** approved each Wikimedia Commons photo and its licence before download.
- **Environment:** approved the OCR language packs and the text-shaping library install.
- **Labelling:** blind human labels on all 80 golden images (v1 and v2), a reason-coded relabel of v1, verification
  of the generated planted failures, and rulings: small label text is a note, not a fail; prop text is stray text;
  the pink→red bottle is acceptable shade variation.
- **Composition:** added it as its own dimension after reviewing v1 (ADR-007), and kept v1 as a labelled baseline.
- **Evaluator fixes:** chose which failure-analysis fixes to apply, tuned on calibration products only.
- **Disputed labels:** kept my original text labels and reported the disagreements as disputed, rather than
  relabelling to reach the text-precision target.

## 5. What went wrong, and how I directed the fixes
- **Scope was re-planned twice.** The v3 set was first dropped, then reinstated; the decisions and cuts are in
  [`docs/decision-brief.md`](docs/decision-brief.md).
- **The load test found two concurrency bugs** (a duplicate image insert race, and an orphaned queued run after a 429).
  Both were fixed with regression tests.
- **No API key at first.** The build ran against fake image and vision clients, with fake outputs kept out of the
  golden set, so the live run was a single command once billing was active.
- **OCR misreads** (a degree sign read as `"` or `*`, Devanagari on coloured bands). Fixed with per-script and
  contrast-stretched OCR reads that keep the best one, rather than by loosening the exact-text rule.
- **The colour check missed a 120° hue shift** because the weighted average hid it. Fixed with a per-dominant-colour
  ΔE limit.
- **False "stray text" on product labels.** Fixed by excluding the product's box from the stray-text scan.
- **Parallel build agents collided on the shared test database.** From then on only one lane ran the API test suite
  at a time.
- **Gaps between the web and the API** (missing brief fields, run links, provenance on evaluation items). These were
  fed back from the web lane to the API lane as follow-up requirements.
- **Billing on the Gemini account** (free tier, then prepaid balance). The agent diagnosed each error from the API
  response and Google's billing documentation; I completed the billing steps.
- **Usage limits interrupted agents.** They were resumed from their own context, and no work was lost.
- **The first live ad drew the prompt's « » delimiters into the headline, and the text check still passed.** The
  headline slot was redesigned and extra characters in the headline zone now fail.
- **Product facts left over from the fake client** were reused for one product in the first batch. That product was
  re-profiled with the real judge and its briefs re-run.
- **Products oversized and looking pasted on** (found in my review). Scale is now derived from real-world size, with
  composition checks and repairs.
- **The recorder silently dropped judge answers during a network outage.** Recording now fails loudly and verifies
  every snapshot by replaying it.
- **A script error marked every dimension as failed on the planted rows I had verified.** Caught by the failure
  analysis and corrected.

## 6. What I did directly
- Set the problem, the scope and every checkpoint decision above.
- Reviewed each stage's output, approved the plan, and accepted or redirected each slice.
- Approved each external download (product photos and licences) and the software installs.
- Set up the Gemini API key and billing.
- Labelled the golden outputs blind to the evaluator, reviewed the final results, and reviewed this write-up.
