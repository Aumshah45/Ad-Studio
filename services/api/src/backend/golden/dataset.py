"""Golden dataset files: briefs, output/planted manifests and human labels (ADR-004).

Layout. The *source* (`data/golden/`) holds briefs and products; each golden **version** has its
own *out* dir (`data/golden/v1/`, `data/golden/v2/`, ADR-007), and a dry run writes to a scratch
dir while reading the committed briefs:

    briefs.yaml, products/*.jpg, LABELING.md          (source)
    <version>/outputs/<brief>-<set>.png               (out: E-nat + E-final)
    <version>/outputs/manifest.jsonl
    <version>/planted/manifest.jsonl, planted/<id>.png (out: E-plant; only generated items as files)
    <version>/labels.csv                              (out: human labels, one row per image and set)
    <version>/cache/verdicts.json                     (out: FileCache snapshot for `make eval`)

v1 is the labelled orchestrator-3 run (the human baseline; `labels_rubric1.csv` keeps its rubric-1
labels as committed); v2 is the ADR-007 run (orchestrator-4) and the default out dir.
"""

import csv
import hashlib
import io
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, Field

from backend.core.settings import API_DIR

REPO_DIR = API_DIR.parents[1]
GOLDEN_DIR = REPO_DIR / "data" / "golden"
# v3 is the unseen hard-case set (own briefs.yaml, scored with the frozen ev-0.8 evaluator).
GOLDEN_VERSIONS: tuple[str, ...] = ("v1", "v2", "v3")
CURRENT_VERSION = "v2"


def golden_out(version: str = CURRENT_VERSION) -> Path:
    if version not in GOLDEN_VERSIONS:
        raise ValueError(
            f"unknown golden version {version!r} (known: {', '.join(GOLDEN_VERSIONS)})"
        )
    return GOLDEN_DIR / version


DRYRUN_DIR = API_DIR / "var" / "golden-dryrun"
# The brief's dimensions plus technical (rubric v1) and composition (rubric v2, ADR-007).
CORE_DIMENSIONS: tuple[str, ...] = ("technical", "text", "product", "context")
DIMENSIONS: tuple[str, ...] = (*CORE_DIMENSIONS, "composition")
CALIBRATION_PRODUCTS = frozenset({"P1", "P2", "P3"})  # ai-design §9.1 splits; P4-P5 are held out
RUBRIC_VERSION = "2"  # v2 (ADR-007): + composition; technical is file-level only
LABEL_COLUMNS = (
    "image_sha",
    "set",
    "brief_id",
    "technical",
    "text",
    "product",
    "context",
    "composition",
    "notes",
    "rubric_version",
)
ImageSet = Literal["nat", "final"]
Split = Literal["calibration", "heldout"]


def split_of(product_id: str) -> Split:
    return "calibration" if product_id in CALIBRATION_PRODUCTS else "heldout"


@dataclass(frozen=True)
class GoldenPaths:
    source: Path = GOLDEN_DIR
    out: Path = GOLDEN_DIR / CURRENT_VERSION

    @property
    def version(self) -> str | None:
        """`v1` / `v2` for a versioned out dir; None for a scratch dir."""
        return self.out.name if self.out.name in GOLDEN_VERSIONS else None

    @property
    def briefs(self) -> Path:
        """The out dir's own briefs.yaml if it has one (v3: unseen briefs), else the shared one."""
        own = self.out / "briefs.yaml"
        return own if own.exists() else self.source / "briefs.yaml"

    @property
    def rubric(self) -> Path:
        return self.source / "LABELING.md"

    @property
    def outputs(self) -> Path:
        return self.out / "outputs"

    @property
    def output_manifest(self) -> Path:
        return self.outputs / "manifest.jsonl"

    @property
    def planted(self) -> Path:
        return self.out / "planted"

    @property
    def planted_manifest(self) -> Path:
        return self.planted / "manifest.jsonl"

    @property
    def labels(self) -> Path:
        return self.out / "labels.csv"

    @property
    def verdicts(self) -> Path:
        return self.out / "cache" / "verdicts.json"

    @property
    def verdicts_rerun(self) -> Path:
        return self.out / "cache" / "verdicts_rerun.json"

    @property
    def sheet(self) -> Path:
        return self.out / "labeling_sheet.html"

    def product_file(self, golden: "GoldenSet", product_id: str) -> Path:
        return self.source / golden.products[product_id].file


# --- briefs ---------------------------------------------------------------------------------------


class GoldenProduct(BaseModel):
    file: str
    name: str


class GoldenBrief(BaseModel):
    id: str
    product: str
    geography: str
    season: str
    required_text: str
    tags: list[str] = Field(default_factory=list[str])
    aspect_ratio: Literal["1:1", "4:5"] = "4:5"

    @property
    def exclude_native_text(self) -> bool:
        """B20 (injection) is overlay-only: excluded from native-text metrics (reconciliation)."""
        return "exclude_native_text_metrics" in self.tags


class GoldenSet(BaseModel):
    version: int
    products: dict[str, GoldenProduct]
    briefs: list[GoldenBrief]

    def brief(self, brief_id: str) -> GoldenBrief:
        for b in self.briefs:
            if b.id == brief_id:
                return b
        raise KeyError(brief_id)


def load_golden(paths: GoldenPaths) -> GoldenSet:
    raw = cast(dict[str, Any], yaml.safe_load(paths.briefs.read_text(encoding="utf-8")))
    default_aspect = str(raw.get("aspect_ratio_default", "4:5"))
    briefs: list[dict[str, Any]] = []
    for item in cast(list[dict[str, Any]], raw["briefs"]):
        briefs.append({"aspect_ratio": default_aspect, **item, "geography": str(item["geography"])})
    return GoldenSet.model_validate(
        {"version": raw.get("version", 1), "products": raw["products"], "briefs": briefs}
    )


def golden_key(brief_id: str, pipeline_version: str, image_client: str) -> str:
    """`briefs.golden_key`: one golden run per brief, pipeline version and image client, so a fake
    dry run never satisfies (or blocks) the live run."""
    key = f"{brief_id}@{pipeline_version}@{image_client}"
    if len(key) > 32:
        raise ValueError(f"golden key too long: {key}")
    return key


# --- manifests ------------------------------------------------------------------------------------


class RunInfo(BaseModel):
    run_id: str
    status: str
    outcome: str | None = None
    reason: str | None = None
    first_attempt_pass: bool | None = None
    repair_count: int = 0
    cost_usd: float = 0.0
    latency_ms: int | None = None


class OutputItem(BaseModel):
    """One line of `outputs/manifest.jsonl`: a natural (first candidate) or final output."""

    id: str  # "<brief>-<set>"
    set: ImageSet
    brief_id: str
    product_id: str
    golden_key: str
    file: str  # relative to outputs/
    image_sha: str
    width: int
    height: int
    mime: str = "image/png"
    reference_sha: str
    spec: dict[str, Any]
    facts: dict[str, Any] | None = None
    candidate_kind: str
    attempt: int
    slot: int
    candidate_status: str
    requested_model: str | None = None
    model: str | None = None  # served model
    prompt_version: str | None = None
    image_client: str
    pipeline_version: str
    evaluator_version: str | None = None
    pipeline_verdict: str | None = None
    pipeline_dimensions: dict[str, bool | None] = Field(default_factory=dict[str, bool | None])
    text_verification: str | None = None  # overlay: ocr | construction
    product_box: list[int] | None = None  # 0-1000 [ymin, xmin, ymax, xmax] from the evaluation
    call_cost_usd: float | None = None  # the generating image call (None for the overlay)
    call_latency_ms: int | None = None
    run: RunInfo
    exclude_native_text: bool = False

    @property
    def split(self) -> Split:
        return split_of(self.product_id)


class PlantedItem(BaseModel):
    """One line of `planted/manifest.jsonl` (E-plant). Pure mutations are regenerated from
    (source image, params, seed); only `generated` items are stored as files."""

    id: str
    mutation: str
    kind: Literal["pure", "generated"]
    source_id: str  # the E-nat item (or, for generated items, the brief's E-nat item)
    source_sha: str
    brief_id: str
    product_id: str
    params: dict[str, Any]
    seed: int
    fails_dimensions: list[str]
    labels: dict[str, bool | None]
    image_sha: str
    file: str | None = None  # relative to planted/, generated items only
    generator_version: str
    verified: bool | None = None  # generated items: None until a human checked them

    @property
    def split(self) -> Split:
        return split_of(self.product_id)


def read_jsonl[M: BaseModel](path: Path, model: type[M]) -> list[M]:
    if not path.exists():
        return []
    rows: list[M] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(model.model_validate_json(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(r.model_dump(mode="json"), sort_keys=True, ensure_ascii=False) for r in rows
    ]
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def read_outputs(paths: GoldenPaths) -> list[OutputItem]:
    return read_jsonl(paths.output_manifest, OutputItem)


def read_planted(paths: GoldenPaths) -> list[PlantedItem]:
    return read_jsonl(paths.planted_manifest, PlantedItem)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dataset_version(paths: GoldenPaths) -> str:
    """Content hash of everything the metrics depend on (briefs, manifests, labels, snapshot)."""
    digest = hashlib.sha256()
    for path in (
        paths.briefs,
        paths.output_manifest,
        paths.planted_manifest,
        paths.labels,
        paths.verdicts,
    ):
        digest.update(path.name.encode())
        digest.update(path.read_bytes() if path.exists() else b"-")
    return digest.hexdigest()[:12]


# --- labels ---------------------------------------------------------------------------------------

_TRUE = {"pass", "ok", "1", "true", "yes", "y", "p"}
_FALSE = {"fail", "0", "false", "no", "n", "f"}


def parse_label(value: str | None) -> bool | None:
    v = (value or "").strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return None


def format_label(value: bool | None) -> str:
    return "" if value is None else ("pass" if value else "fail")


class LabelRow(BaseModel):
    image_sha: str
    set: str  # nat | final | plant
    brief_id: str
    technical: bool | None = None
    text: bool | None = None
    product: bool | None = None
    context: bool | None = None
    composition: bool | None = None  # rubric v2; None on rubric-1 rows (not labelled)
    notes: str = ""
    rubric_version: str = RUBRIC_VERSION

    def dims(self) -> dict[str, bool | None]:
        return {d: getattr(self, d) for d in DIMENSIONS}

    @property
    def has_composition(self) -> bool:
        """Rubric v2+ rows label composition; rubric-1 rows predate it (ADR-007)."""
        return self.rubric_version not in ("", "1")

    @property
    def complete(self) -> bool:
        dims = DIMENSIONS if self.has_composition else CORE_DIMENSIONS
        return all(getattr(self, d) is not None for d in dims)

    @property
    def all_pass(self) -> bool:
        """Every labelled dimension passes (composition counts once it is labelled)."""
        return self.complete and all(v is not False for v in self.dims().values())


def read_labels(path: Path) -> dict[tuple[str, str], LabelRow]:
    """labels.csv keyed by (image_sha, set). Missing file -> no labels."""
    if not path.exists():
        return {}
    rows: dict[tuple[str, str], LabelRow] = {}
    reader = csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig")))
    for raw in reader:
        sha = (raw.get("image_sha") or "").strip()
        if not sha:
            continue
        row = LabelRow(
            image_sha=sha,
            set=(raw.get("set") or "").strip(),
            brief_id=(raw.get("brief_id") or "").strip(),
            technical=parse_label(raw.get("technical")),
            text=parse_label(raw.get("text")),
            product=parse_label(raw.get("product")),
            context=parse_label(raw.get("context")),
            composition=parse_label(raw.get("composition")),
            notes=(raw.get("notes") or "").strip(),
            rubric_version=(raw.get("rubric_version") or "1").strip(),
        )
        rows[(row.image_sha, row.set)] = row
    return rows


def labels_csv(rows: Sequence[LabelRow]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(LABEL_COLUMNS)
    for r in sorted(rows, key=lambda r: (r.brief_id, r.set, r.image_sha)):
        writer.writerow(
            [
                r.image_sha,
                r.set,
                r.brief_id,
                *(format_label(v) for v in r.dims().values()),
                r.notes,
                r.rubric_version,
            ]
        )
    return buf.getvalue()


def write_labels(path: Path, rows: Sequence[LabelRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(labels_csv(rows), encoding="utf-8")


# --- reason codes (v1 relabel, analysis R1b) ------------------------------------------------------
# `labeling_sheet_reasons.html` asks, for every product/context fail, why it failed, and writes the
# answer into notes as `reason:<dimension>=<code>`. scale / pasted are composition causes.
REASON_CODES: tuple[str, ...] = (
    "scale",
    "pasted",
    "shape",
    "colour",
    "label_text",
    "season",
    "geography",
    "people",
    "other",
)
COMPOSITION_REASONS = frozenset({"scale", "pasted"})
REASON_DIMENSIONS: tuple[str, ...] = ("product", "context")
_REASON_RE = re.compile(r"reason:(\w+)=(\w+)")


def parse_reasons(notes: str) -> dict[str, str]:
    """`reason:<dimension>=<code>` tokens in a label's notes -> {dimension: code}."""
    return {d: c for d, c in _REASON_RE.findall(notes or "") if c in REASON_CODES}
