# Problem

**Enrichment of image generation using structured context.** An image generation pipeline that depends on
structured context to refine its outputs: how do you *guarantee* that generation quality is achieved during the
generation process, rather than hoping for it afterwards?

## Use case

Display-ad generation for a marketer localising one product ad for many markets. The input is a **reference product
image** plus three structured text fields:

- **target geography**,
- **season**,
- **freeform text that must appear in the ad**.

This context should *refine* the generated image, not just be pasted into a prompt. Image models do not render text
reliably, so the design has to account for imperfect text rendering.

## Goals

1. Design an image generation pipeline for display ads on **Gemini 3.1 Flash-Lite Image** or **Gemini 3.1 Flash
   Image**, driven by the product image and the three fields.
2. Choose an effective generation strategy and implement it. Generated images are at most **1K** (long edge
   ≤ 1024 px).
3. Build an evaluator that judges the quality of the pipeline's outputs across a set of about 20 images.
4. Show with automated tests that the evaluator separates passing from failing outputs on defined quality metrics,
   covering at least **context adherence**, **reference-product fidelity** and **text-rendering fidelity**.

## Constraints

- Python and TypeScript.
- Hosted models only; no fine-tuning or local AI models (OCR excepted).
- Results must be reproducible from the repository: the golden dataset and tests are committed.
