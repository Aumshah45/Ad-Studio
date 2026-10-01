# Decision brief

For each key decision: what was chosen, the main alternative, and the one-sentence reason.

| # | Decision | Chosen | Alternative | Why (one sentence) | Evidence |
|---|---|---|---|---|---|
| 1 | Focus | A measured quality gate around generation (evaluate → repair → guarantee) | Better prompting alone | Prompting can't guarantee anything; a gate with a measured evaluator can | `docs/problem.md` |
| 2 | Open design questions | Default assumptions, recorded | Block until answered | Nothing to wait for; the defaults are disclosed in the PRD | `docs/prd.md` §Decisions on the open questions |
| 3 | Pattern | Rung 2 fixed workflow | Agent with tools | Steps are known; cheaper, deterministic and testable offline | ADR-001, `ai-design.md §1` |
| 4 | Generation strategy | 2 Flash-Lite drafts → ≤ 2 targeted Flash repairs → deterministic text overlay | Flash best-of-3 | Text guaranteed at a measured $0.088/ad (estimate was ≈ $0.12); the per-component ablations (A5/A6) were not run | ADR-005 |
| 5 | Inputs | ISO country picker + free-text season resolved by a code table | Fixed lists | Hemisphere correct by construction while still accepting "Diwali" | ADR-006 |
| 6 | Judge authority | Veto, not pardon: deterministic checks decide text and product; the VLM can only add failures | VLM decides | Vision models autocorrect "Summr", and text in the image can inject | `architecture.md` §Reconciliation |
| 7 | Release | Gate proposes, human approves export | Auto-release | Brand safety; overrides become labels for the agreement metric | `ux.md`, reconciliation |
| 8 | Scope | Core loop first; deferred extras as later slices only if they move a metric or the demo | Build everything / cut everything | End-to-end first, then add depth | design review |
| 9 | Test policy | Tests assert behaviour; targets are reported, not gating | Targets gate CI | Avoids tuning thresholds just to turn CI green | PRD success criteria |
| 10 | Label-text strictness | Fail only on brand name / large label text; small print is a note | Any garbled text fails | Micro-text garbling is common and repairs rarely fix it; the note keeps it visible (80% of v2 finals) | `data/golden/LABELING.md` |
| 11 | Composition dimension + v2 run | New must-pass dimension (realistic scale, natural integration); v1 kept as baseline | Fold into context; evaluator-only | My review of v1 found oversized/pasted products; human composition pass 55% → 100% | ADR-007, v2 report |
| 12 | v1 relabel with reason codes | Scale causes moved from product/context to composition | Keep mixed labels | 9/9 product fails and 5/10 context fails were scale, so the metrics now measure what they claim | `data/golden/v1/labels.csv` (reason codes) |
| 13 | Evaluator fixes | Analysis-ranked fixes R2–R6, tuned on P1–P3 only | Tune on everything | Agreement 75% → 90% on v2 without touching held-out products | `analysis-2026-09-26.md` |
| 14 | Disputed text labels | Keep as labelled, report as disputed | Relabel to hit the text-precision target | Relabelling to reach a number looks like gaming; every remaining false alarm is a dispute | `TECHNICAL_REPORT.md` §8 |
| 15 | Prop text | Legible third-party prop text fails text | Allow it | An ad shouldn't carry another brand's copy | `LABELING.md` ruling |
| 16 | Pink bottle rendered red | Acceptable shade variation; hue drift measured, not failed | Hue check fails it | Only one chromatic product for calibration; colours now go into the prompt | ev-0.8 |
| 17 | Unseen test set | Freeze the evaluator (ev-0.8), then run 16 new hard briefs (v3) picked by me and committed before generation | Keep tuning on v1/v2 | Only data never tuned on shows real performance | `v3-unseen-2026-09-26.md` |

## What's next
1. A second labeller for inter-rater agreement, and a larger unseen set with more real product, context and
   composition failures.
2. A cross-vendor vision judge to measure same-family bias, and online CTR A/B tests feeding thresholds per placement.
3. A durable job queue with object storage, the remaining load-test fixes (F3–F5) and per-tenant brand kits.
