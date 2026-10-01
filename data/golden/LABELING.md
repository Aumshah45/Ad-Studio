# Labelling rubric (human labels for `labels.csv`) — rubric version 2

**Who:** one labeller from the team. This is a stated limitation (no inter-rater agreement).
**When:** label each image **before** looking at the evaluator's verdicts. The labelling sheet hides them.
**What:** per golden version (`data/golden/v1/`, `data/golden/v2/`), the ≈20 natural outputs (E-nat: the first
Flash-Lite candidate of each brief) and the 20 final outputs (E-final). Planted failures are labelled by construction
and need no human label, except the generated context failures, which are checked once.

For each image, mark each dimension **pass** or **fail**. If unsure, mark `fail` and add a note. The evaluator treats
"unsure" as not passing, and so does the labeller.

| Dimension | Pass when… | Fail when… (any one) |
|---|---|---|
| technical | **File-level only:** the file opens, the long edge is ≤ 1024 px, the aspect ratio is as requested, and the image is not blank, corrupt or a copy of the reference | Any of these is violated. Not for how the picture looks: an oversized or pasted-in product is **composition**, a garbled label is **product** |
| text | The required text appears **exactly** (case and whitespace may differ; every letter, digit, symbol, accent and script character matches), is legible at ad size, and there is no extra gibberish text | A typo, a missing or extra character, a wrong number/price/percentage, the text missing or cut off, illegible, or stray invented text elsewhere. B20: the literal adversarial text is the *correct* text |
| product | The product is clearly the reference product: same shape, colours, logo/label design and text (small label text may be less sharp), shown once as the hero | Recoloured, reshaped, logo or label changed, erased or invented, a different product, duplicated, or so small or occluded that it isn't the hero |
| context | The scene fits the **effective** season for the geography (e.g. Australia + December = summer), shows plausible local cues, contains nothing from the locale's avoid list, and is ad-appropriate | A season contradiction (snow in a Sydney December), a clearly wrong locale, inappropriate or offensive content, or an avoid-list item |
| composition | **Realistic scale:** the product's size is plausible next to the objects around it, given its real-world size (a 15 cm tube stands about as tall as a drinking glass and never towers over a chair). **Natural integration:** it looks photographed in the scene: lit by the scene's light (same direction and colour temperature), resting on a surface with a contact shadow, same perspective and camera angle, same focus / depth of field as its surroundings, no cut-out edge or halo | The product is noticeably too big or too small for the scene (towering over furniture, dwarfed by a cup), **or** it looks superimposed or pasted: floating or without a contact shadow, lit from a different direction or colour than the scene, a different perspective, pin-sharp while its surroundings at the same distance are blurred (or the reverse), or a visible cut-out outline, halo or fringe |

Record in `labels.csv`: `image_sha, set (nat|final), brief_id, technical, text, product, context, composition, notes,
rubric_version=2`.

**Rubric history.** Rubric 1 (the committed v1 labels, kept as `v1/labels_rubric1.csv`) had no composition column, and
scale or pasted-look problems were marked inconsistently under technical, product or context. Rubric 2 adds
composition and makes technical file-level only. The v1 composition pass (`make golden-sheet-v1-composition`) shows
the 40 v1 images with their rubric-1 labels preloaded: fill composition for each image, and where a technical fail
was really a composition problem, set technical back to pass and note it. It writes `v1/labels.csv` with
`rubric_version=2`.

**Reason pass (v1, analysis R1b).** `make golden-sheet-v1-reasons` shows only the v1 images failed on product or
context, labels preloaded, with the reference product beside each ad. Each failed dimension needs a reason code
(scale, pasted, shape, colour, label_text, season, geography, people, other). scale and pasted are composition causes:
the sheet sets the dimension to pass and composition to fail, and the labeller can override. Reasons are written into
`notes` as `reason:<dimension>=<code>`; every other row is unchanged.

**Ruling (2026-09-26): prop text.** Legible text printed on scene props (e.g. a soap bar reading "TRADITIONAL OLIVE
OIL") counts as stray text and fails *text*: an ad should not carry third-party brand or product copy. Text on the
product itself (its own label) does not count as stray.
