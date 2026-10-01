Golden dataset for this project. Every item's provenance and licence goes in SOURCES.md.

- `briefs.yaml`, `products/`, `LABELING.md` (rubric v2), `SOURCES.md`: shared by every version.
- `v1/`: the labelled orchestrator-3 run (20 briefs, E-nat + E-final, verdict snapshot, human labels). The human
  baseline; `labels_rubric1.csv` keeps the rubric-1 labels as committed. `labeling_sheet_composition.html`
  (`make golden-sheet-v1-composition`) fills the composition column and writes `v1/labels.csv` with rubric 2.
- `v2/`: the ADR-007 run (orchestrator-4: realistic scale, photographed-in-scene prompt, composition dimension),
  written by `make golden-run golden-export` (default `GOLDEN_VERSION=v2`).
