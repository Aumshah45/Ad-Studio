# PRD — Ad Studio

## Problem
> An image generation pipeline depends on structured context to refine its outputs. How do you guarantee that
> generation quality is achieved in the generation process?

The use case, goals and constraints are in [`problem.md`](problem.md).

## One-line summary
Ad Studio is for regional marketers who have to localise one product ad for many markets. It turns a product photo plus
*geography, season and must-say text* into a 1K display ad, and only releases an ad after it passes a measured
quality gate on product fidelity, context and exact text. Unlike prompting an image model and hoping, every ad it
releases comes with a scorecard, and the evaluator is itself tested against known failures.

## Wedge (why this beats a generic chatbot)
**Quality is enforced during generation, not checked afterwards.** A creative planner that knows the hemisphere
(Australia + December → *summer*) writes the spec. Each candidate is scored against that spec by deterministic checks
first (OCR character error rate, colour ΔE, keypoint identity) and a vision-model rubric second. A failing candidate
gets a *targeted* repair using the evaluator's reason. If the text still fails, it is rendered deterministically into a
reserved text-safe zone, so the required text is guaranteed. We also test the **evaluator**: planted failures with
certain labels give per-dimension precision and recall, so "the evaluator works" is a measured number, not a claim.

## Users and jobs to be done
| User | Job to be done | Today's workaround | Pain (evidence) |
|---|---|---|---|
| **Primary:** regional / performance marketer at a product brand (e.g. a SaaS or D2C vendor advertising across markets) | When I launch a campaign in several countries and seasons, I want on-brand localised display ads with my exact offer text, so I can ship all variants in a day without a designer per market | A designer adapts one master creative per market by hand, or the marketer prompts an image model and checks outputs by eye | Days per market. Generic image models misspell offers ("Summr Sael"), alter the product (colour, logo), and miss local context such as a snowy "December" ad for Sydney |
| Secondary: creative-ops reviewer / brand safety | When AI ads are generated at volume, I want each flagged failure to come with a reason, so I review only the borderline ones | Reviews every image manually | Doesn't scale; no consistent criteria |

## Requirements matrix
| # | Requirement | Deliverable that satisfies it | Where (path / section) | Status |
|---|---|---|---|---|
| T1 | Propose an image generation pipeline for display advertisement generation | Pipeline design (plan → generate → evaluate → repair → text fallback → approve) with diagram and rationale | `docs/architecture.md`, `docs/ai-design.md`, `TECHNICAL_REPORT.md §Design` | done |
| T1a | Uses Gemini 3.1 Flash-Lite Image **or** Gemini 3.1 Flash Image | Model gateway calling `gemini-3.1-flash-lite-image` / `gemini-3.1-flash-image`; model id recorded per image | pipeline code, ledger, `TECHNICAL_REPORT.md` | done |
| T1b | Input: a reference product image | Upload / file input, stored and passed as a reference image in every generation call | API + UI | done |
| T1c | Input: three structured text fields — target geography, season, freeform text that must be included in the image | Typed request schema `{geography, season, required_text}` with validation | API schema, UI form | done |
| T1d | Added context leads to refinement of the generated image | Creative planner that turns the fields into a structured spec (hemisphere-aware season, locale cues, palette, text zone). The spec drives the prompt and the evaluator rubric | planner + `docs/ai-design.md` | done |
| T1e | Text rendering is imperfect; the design must account for it | OCR check + targeted retry + deterministic overlay fallback; native-render pass rate reported separately | evaluator, pipeline, `TECHNICAL_REPORT.md §Limitations` | done |
| T2 | Decide an effective generation strategy, then implement the generation pipeline | Strategy ADR (candidates → evaluate → targeted repair, bounded retries) plus working implementation | `docs/architecture.md`, code, demo | done |
| C1 | Maximum resolution of generated images must be 1K (long edge ≤1024px) | `image_size="1K"` plus a post-decode assertion and downscale; `test_resolution_cap` | code + tests | done |
| T3 | Implement an evaluator that judges the quality of the outputs across ≈20 images output from the pipeline | Evaluator module plus a batch run over ≈20 pipeline outputs, with a stored report | `data/golden/`, `evals/reports/` | done |
| T4 | Demonstrate through automated tests that the evaluator identifies passing and failing outputs against defined quality metrics | pytest suite over stored images (offline, deterministic, cached vision verdicts), plus meta-eval precision/recall per dimension | `services/api/tests/`, eval report | done |
| T4a | Core dimension: context adherence | Rubric of binary checks derived from the spec (season/hemisphere, geography cues, no contradictions) | evaluator + tests | done |
| T4b | Core dimension: fidelity of the reference product | Product crop → colour ΔE, keypoint/homography identity, vision checklist (shape, logo, label, no duplicate) | evaluator + tests | done |
| T4c | Core dimension: text-rendering fidelity | OCR (Tesseract / Apple Vision) CER against the required text, exact match on critical tokens, vision read-back as second opinion | evaluator + tests | done |
| C2 | Tech stack: Python and TypeScript | Python API/pipeline, TypeScript web UI | repo | done |
| D1 | Results reproducible from the repository: golden dataset and tests committed, evals replay offline | Golden set in git, cached verdict snapshots, socket-blocked test and eval runs | `data/golden/`, `services/api/tests/` | done |

## Success criteria (measurable, reported in TECHNICAL_REPORT.md)
| Criterion | Metric | Target | How measured |
|---|---|---|---|
| Evaluator catches failures | Recall on planted + human-labelled failures, **per dimension** (text, product, context) | ≥ 0.90 each | `make eval` → meta-eval report |
| Evaluator doesn't cry wolf | Precision on the same labelled set, per dimension | ≥ 0.85 each | `make eval` → meta-eval report |
| Evaluator agrees with humans | Overall pass/fail agreement with human labels on the ≈20 natural outputs | ≥ 85% | eval report |
| Quality gate works | Pass rate of shipped ads (all three dimensions) — first attempt vs after repair | report both; after-repair ≥ 90% | pipeline run over the golden briefs |
| Text is guaranteed | Shipped ads whose OCR text exactly matches the required text (after normalising case and whitespace) | 100% (native-render rate reported separately) | eval report |
| Resolution constraint | Outputs with long edge ≤ 1024 px | 100% | `test_resolution_cap` + batch check |
| Context refinement is correct | Planner's effective season on hemisphere test cases (e.g. AU/Dec → summer, CA/Dec → winter, equatorial) | 100% | `test_planner_hemisphere` |
| Evaluator tests are reproducible | Test suite runs offline and deterministic (cached verdicts) | 0 network calls, green | `make check` |
| Cost | $ per approved ad (generation + judging) | ≤ $0.25 | ledger |
| Latency | p50 time to an approved ad | ≤ 60 s | ledger |

## Success metrics
- North star: **after-repair approved-ad rate at 100% exact text**, with evaluator recall ≥ 0.90 per dimension.
- Supporting: first-attempt pass rate, mean repair attempts per ad, human-agreement rate.
- Guardrail metrics (must not get worse): evaluator precision per dimension, cost per approved ad, p50 latency.

## Scope
| Must (demo fails without it) | Should (if time) | Won't (out of scope) |
|---|---|---|
| All goals T1–T4 and C1 (above) | Best-of-N candidate selection with Flash-Lite, Flash reserved for repair | Brand-kit / logo / font upload per customer |
| **M1** Evaluate → targeted repair → deterministic text fallback loop, each step visible with reasons | Multiple aspect ratios per placement (1:1, 4:5, 16:9) | Model fine-tuning or local image models (model policy: hosted only) |
| **M2** Hemisphere/locale-aware creative planner whose spec drives both the prompt and the evaluator rubric | Non-Latin required text (e.g. Hindi, Japanese) as a stress case | Online CTR / A/B testing (production story only) |
| **M3** Web UI: brief form → live plan → candidates with scorecards → approved ad; evaluator dashboard with per-dimension precision/recall and planted-failure examples | Human labelling UI for the ≈20 outputs | Video or animated ads |
| | Adversarial required-text handling (prompt injection in the freeform field) shown in the demo | Multi-tenant auth |

## Assumptions
1. Using **both** named image models is allowed (Flash-Lite for candidates, Flash for repair/final), and a hosted Gemini
   text/vision model may be used for planning and judging.
2. A deterministic text overlay, used only as a fallback after native rendering fails, is acceptable. We report the
   native-rendering pass rate separately so nothing is hidden.
3. Reference product images: we supply 4–6 of our own photos or openly licensed images (licence recorded); no
   external dataset is assumed.
4. "≈20 images" means ≈20 natural pipeline outputs. Planted failures derived from them for evaluator tests are an
   addition, not a substitute.
5. The required text is rendered exactly as given (never translated or corrected), in whatever script it is written.
6. Default placement is a 1:1 or 4:5 display ad at 1K.
7. Human labels on the ≈20 natural outputs come from one labeller (the author), with a written rubric. This is noted
   as a limitation.

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Image-model API access / billing not enabled (no free tier for image models) | Med | High | Check keys at bootstrap; cache every generation; stored golden outputs let evals and tests run offline |
| Model id / API surface differs from docs (Interactions API vs `generate_content`) | Med | Med | Spike one call first; gateway abstracts it |
| Vision-model judge is noisy or non-deterministic | High | Med | Deterministic signals first; temperature 0; binary checks; cache verdicts by image hash + rubric version; measure judge vs labels |
| Product fidelity metrics fooled by scene lighting / pose change | Med | Med | Crop via vision bounding box; combine ΔE + keypoints + checklist; calibrate thresholds on labelled items |
| OCR misreads stylised ad typography → false text failures | Med | Med | Two OCR engines + vision read-back; flag disagreements; measure precision |
| Product image licensing | Low | Med | Own photos or CC-licensed with attribution file |
| Cost overrun from repair loops | Low | Low | Max 2 repairs, per-ad budget cap in ledger |

## Open design questions
1. **Use both image models, plus a Gemini text/vision model for planning and judging?** This decides model
   routing and cost. *Default: yes, Flash-Lite for candidates, Flash for repair, a Gemini Flash text model for plan and
   judge.*
2. **Is a deterministic text overlay acceptable, when it is used only as a fallback after native rendering fails?** This
   decides whether "guaranteed text" is a valid strategy. *Default: yes, disclosed, with the native-render rate
   reported separately.*
3. **Where do the reference product images come from?** This decides the golden dataset. *Default:
   our own photos or openly licensed images, licences recorded.*
4. **Should the ≈20 evaluated images be natural pipeline outputs only, or may they include planted failures?** This
   decides how the evaluator tests are structured. *Default: ≈20 natural outputs, plus separate programmatic planted
   failures.*
5. **Should the required text ever be translated for the geography?** This decides planner behaviour. *Default: no,
   rendered exactly as given.*
6. **Any target ad placement or aspect ratio?** *Default: 1:1 and 4:5 at 1K.*

## Decisions on the open questions
- All six are resolved as their stated defaults; Assumptions 1–7 stand and are disclosed in `TECHNICAL_REPORT.md`.

## Demo narrative (hypothesis)
1. A marketer needs a winter-sale ad for Canada and a summer-sale ad for Australia, both "in December", from one
   product photo, and today each takes a designer a day.
2. They upload the photo and type "Australia / December / 'Summer Sale — 30% OFF'". The plan shows *effective season:
   summer (southern hemisphere)*. Candidates stream in with scorecards; one is rejected because the text rendered as
   "Summr", a targeted repair fixes it, and the approved ad passes every gate.
3. The proof: the evaluator dashboard. Planted failures are caught (per-dimension precision/recall), first-attempt vs
   after-repair pass rate is shown, text accuracy is 100%, and cost and latency per approved ad come from the ledger.

## Shape hint
Decision-table **G (content generation with quality constraints)**, pattern ladder **rung 2 (fixed workflow)**. The
steps (plan → generate → evaluate → repair → fallback) are known in advance, so code decides the order. That is cheaper
than an agent, deterministic, and easy to evaluate step by step.
