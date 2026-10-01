"""`golden sheet`: `labeling_sheet.html`, a self-contained contact sheet for the human labelling
pass (slice 15, LABELING.md).

One card per E-nat and E-final image (and per generated planted item, which a human verifies):
the image, the brief (product, market, season -> effective season), the exact required text with
its code points, and pass/fail radios per dimension plus a note. **No evaluator output is written
into the page** (no verdicts, run status, repairs or outcome), so the labels stay blind. A small
inline script keeps answers in the browser (localStorage), can load an existing labels.csv to
resume, and downloads `labels.csv` in the format `read_labels` expects (rubric v2, with the
composition column, ADR-007).

`composition=True` is the v1 composition pass: the cards carry the existing (rubric-1) labels,
preloaded; only the composition column is open, plus technical so that a technical fail that was
really a composition problem can be corrected. Text, product and context are shown read-only. The
download is the full `labels.csv` with `rubric_version=2`.
"""

import base64
import html
import io
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from backend.domain.adstudio.spec import CreativeSpec
from backend.golden.dataset import (
    COMPOSITION_REASONS,
    DIMENSIONS,
    LABEL_COLUMNS,
    REASON_CODES,
    REASON_DIMENSIONS,
    RUBRIC_VERSION,
    GoldenPaths,
    GoldenSet,
    LabelRow,
    format_label,
    load_golden,
    read_labels,
    read_outputs,
    read_planted,
)

# In the composition pass only these are editable (technical: to correct a technical fail that was
# really composition); the rest is shown read-only.
COMPOSITION_EDITABLE = ("technical", "composition")
COMPOSITION_SHEET = "labeling_sheet_composition.html"

SET_TITLES = {
    "nat": "E-nat · first candidate (before any repair)",
    "final": "E-final · final output of the run",
    "plant": "E-plant · generated context item (verify)",
}


@dataclass
class Card:
    key: str  # image_sha|set
    image_sha: str
    set: str
    brief_id: str
    title: str
    src: str
    product: str
    market: str
    season: str
    text: str
    note: str = ""


def _data_uri(data: bytes, max_edge: int = 900) -> str:
    with Image.open(io.BytesIO(data)) as img:
        rgb = img.convert("RGB")
    rgb.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    rgb.save(buf, format="JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _codepoints(text: str) -> str:
    """Non-ASCII characters spelled out, so accents, dashes and symbols can be checked exactly."""
    special = [c for c in dict.fromkeys(text) if ord(c) > 127]
    return " · ".join(f"{c} U+{ord(c):04X} {unicodedata.name(c, '?').title()}" for c in special)


def _inline_md(text: str) -> str:
    out = html.escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", out)


def rubric_html(markdown: str) -> str:
    """LABELING.md as HTML (paragraphs, bold, code and the one table)."""
    parts: list[str] = []
    table: list[list[str]] = []
    for line in [*markdown.splitlines(), ""]:
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not all(set(c) <= {"-", ":"} for c in cells):
                table.append(cells)
            continue
        if table:
            head, *body = table
            parts.append(
                "<table><thead><tr>"
                + "".join(f"<th>{_inline_md(c)}</th>" for c in head)
                + "</tr></thead><tbody>"
                + "".join(
                    "<tr>" + "".join(f"<td>{_inline_md(c)}</td>" for c in row) + "</tr>"
                    for row in body
                )
                + "</tbody></table>"
            )
            table = []
        if line.startswith("# "):
            continue
        if line.strip():
            parts.append(f"<p>{_inline_md(line)}</p>")
    return "\n".join(parts)


def _brief_fields(golden: GoldenSet, spec: dict[str, Any], brief_id: str) -> tuple[str, str, str]:
    brief = golden.brief(brief_id)
    product = golden.products[brief.product].name
    try:
        s = CreativeSpec.model_validate(spec)
        market = s.geo.country_name
        season = (
            f"{brief.season} → {s.season.effective_season.replace('_', ' ')} "
            f"({s.geo.hemisphere} hemisphere)"
        )
    except Exception:  # noqa: BLE001 - a spec from another version: show the raw brief
        market, season = brief.geography, brief.season
    return f"{brief.product} · {product}", market, season


def build_cards(paths: GoldenPaths, *, embed: bool = True) -> list[Card]:
    golden = load_golden(paths)
    outputs = read_outputs(paths)
    order = {"nat": 0, "final": 1}
    cards: list[Card] = []
    for item in sorted(outputs, key=lambda i: (i.brief_id, order.get(i.set, 2))):
        data = (paths.outputs / item.file).read_bytes()
        product, market, season = _brief_fields(golden, item.spec, item.brief_id)
        cards.append(
            Card(
                key=f"{item.image_sha}|{item.set}",
                image_sha=item.image_sha,
                set=item.set,
                brief_id=item.brief_id,
                title=f"{item.brief_id} · {SET_TITLES[item.set]}",
                src=_data_uri(data) if embed else f"outputs/{item.file}",
                product=product,
                market=market,
                season=season,
                text=golden.brief(item.brief_id).required_text,
            )
        )
    for planted in read_planted(paths):
        if planted.kind != "generated" or not planted.file:
            continue
        data = (paths.planted / planted.file).read_bytes()
        source = next((o for o in outputs if o.id == planted.source_id), None)
        product, market, season = _brief_fields(
            golden, source.spec if source else {}, planted.brief_id
        )
        forced = planted.params.get("forced", {})
        cards.append(
            Card(
                key=f"{planted.image_sha}|plant",
                image_sha=planted.image_sha,
                set="plant",
                brief_id=planted.brief_id,
                title=f"{planted.id} · {SET_TITLES['plant']}",
                src=_data_uri(data) if embed else f"planted/{planted.file}",
                product=product,
                market=market,
                season=season,
                text=golden.brief(planted.brief_id).required_text,
                note=(
                    "Generated with a deliberately contradicting scene "
                    f"({html.escape(str(forced.get('setting', '')))}). Context should fail; "
                    "label every dimension as you see it."
                ),
            )
        )
    return cards


def _fieldset(i: int, d: str, *, composition_pass: bool) -> str:
    locked = composition_pass and d not in COMPOSITION_EDITABLE
    cls = "dim focus" if composition_pass and d == "composition" else "dim"
    return f"""<fieldset class="{cls}"{" disabled" if locked else ""}><legend>{d}</legend>
<label><input type="radio" name="{i}-{d}" value="pass" data-dim="{d}"> pass</label>
<label><input type="radio" name="{i}-{d}" value="fail" data-dim="{d}"> fail</label>
</fieldset>"""


def _card_html(i: int, card: Card, *, composition_pass: bool = False) -> str:
    radios = "".join(_fieldset(i, d, composition_pass=composition_pass) for d in DIMENSIONS)
    points = _codepoints(card.text)
    return f"""<article class="card" data-key="{html.escape(card.key)}" data-sha="{card.image_sha}"
 data-set="{card.set}" data-brief="{card.brief_id}">
<img src="{card.src}" alt="{html.escape(card.title)}" loading="lazy">
<div class="meta">
<h2>{html.escape(card.title)}</h2>
<dl>
<dt>Product</dt><dd>{html.escape(card.product)}</dd>
<dt>Market</dt><dd>{html.escape(card.market)}</dd>
<dt>Season</dt><dd>{html.escape(card.season)}</dd>
</dl>
<p class="req-label">Required text (must appear exactly)</p>
<p class="req">{html.escape(card.text)}</p>
{f'<p class="points">{html.escape(points)}</p>' if points else ""}
{f'<p class="note">{card.note}</p>' if card.note else ""}
<div class="dims">{radios}</div>
<label class="notes">Notes
<input type="text" data-notes placeholder="optional; required if unsure"></label>
</div>
</article>"""


TEMPLATE = Path(__file__).with_name("labeling_sheet.template.html")


def _ref_src(paths: GoldenPaths, file: str, embed: bool) -> str:
    if embed:
        return _data_uri((paths.source / file).read_bytes(), 360)
    return os.path.relpath(paths.source / file, paths.out)


def preload(paths: GoldenPaths) -> dict[str, dict[str, str]]:
    """The existing labels as the page's initial answers (key `sha|set`)."""
    rows: dict[str, dict[str, str]] = {}
    for (sha, image_set), row in read_labels(paths.labels).items():
        entry = {d: format_label(v) for d, v in row.dims().items()}
        entry["notes"] = row.notes
        rows[f"{sha}|{image_set}"] = entry
    return rows


MODE_NOTE = """<p><strong>Composition pass (rubric v2).</strong> Each image's existing labels
are loaded. Fill <strong>composition</strong> for every image (realistic scale and natural
integration, see the table). Text, product and context are read-only. Technical is open only to
correct a technical fail that was really a composition problem (an oversized or pasted-looking
product): set it back to pass and add a note such as <code>tech->comp</code>. The download is the
full labels.csv, rubric 2.</p>"""


def render_sheet(paths: GoldenPaths, *, embed: bool = True, composition: bool = False) -> str:
    golden = load_golden(paths)
    cards = build_cards(paths, embed=embed)
    if composition:
        cards = [c for c in cards if c.set in ("nat", "final")]
    version = paths.version or "scratch"
    refs = "".join(
        f'<figure><img src="{_ref_src(paths, p.file, embed)}" alt="{pid} reference">'
        f"<figcaption>{pid} · {html.escape(p.name)}</figcaption></figure>"
        for pid, p in golden.products.items()
    )
    rubric = paths.rubric.read_text(encoding="utf-8") if paths.rubric.exists() else ""
    save_as = f"data/golden/{version}/labels.csv" if paths.version else "labels.csv"
    values = {
        "TITLE": f"Golden {version} composition pass"
        if composition
        else f"Golden {version} labels",
        "N": str(len(cards)),
        "RUBRIC_VERSION": html.escape(RUBRIC_VERSION),
        "RUBRIC_JSON": json.dumps(RUBRIC_VERSION),
        "COLUMNS": json.dumps(list(LABEL_COLUMNS)),
        "DIMS": json.dumps(list(DIMENSIONS)),
        "STORE": json.dumps(
            f"golden-labels-{version}-r{RUBRIC_VERSION}" + ("-composition" if composition else "")
        ),
        "PRELOAD": json.dumps(preload(paths) if composition else {}, sort_keys=True),
        "SAVE_AS": html.escape(save_as),
        "MODE_NOTE": MODE_NOTE if composition else "",
        "RUBRIC": rubric_html(rubric),
        "REFS": refs,
        "CARDS": "\n".join(
            _card_html(i, c, composition_pass=composition) for i, c in enumerate(cards)
        ),
    }
    page = TEMPLATE.read_text(encoding="utf-8")
    for key, value in values.items():
        page = page.replace(f"@@{key}@@", value)
    return page


def sheet_path(paths: GoldenPaths, *, composition: bool = False) -> Path:
    return paths.out / COMPOSITION_SHEET if composition else paths.sheet


def write_sheet(paths: GoldenPaths, *, embed: bool = True, composition: bool = False) -> int:
    html_text = render_sheet(paths, embed=embed, composition=composition)
    target = sheet_path(paths, composition=composition)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html_text, encoding="utf-8")
    return html_text.count('class="card"')


# --- v1 reason relabel (analysis R1b) ------------------------------------------------------------
# Only the v1 images a human failed on product or context. Each failed product/context dimension
# gets a required reason code; scale / pasted move the fail to composition (the page sets that
# dimension to pass and composition to fail, visibly, and the labeller can override). The download
# is the full labels.csv: these rows updated, every other row written back unchanged.
REASONS_SHEET = "labeling_sheet_reasons.html"
REASONS_TEMPLATE = Path(__file__).with_name("labeling_sheet_reasons.template.html")
REASON_LABELS = {
    "scale": "scale (too big / too small for the scene)",
    "pasted": "pasted (looks superimposed, no contact shadow, wrong light)",
    "shape": "shape (reshaped, different or duplicated product)",
    "colour": "colour (recoloured)",
    "label_text": "label text (logo / label changed, erased or invented)",
    "season": "season (contradicts the effective season)",
    "geography": "geography (wrong locale)",
    "people": "people (people / body parts with the product)",
    "other": "other (explain in the note)",
}
REASON_EDITABLE = ("product", "context", "composition")


def reason_targets(labels: dict[tuple[str, str], LabelRow]) -> dict[str, list[str]]:
    """`sha|set` -> the failed product/context dimensions of each E-nat / E-final row."""
    out: dict[str, list[str]] = {}
    for (sha, image_set), row in labels.items():
        if image_set not in ("nat", "final"):
            continue
        failed = [d for d in REASON_DIMENSIONS if getattr(row, d) is False]
        if failed:
            out[f"{sha}|{image_set}"] = failed
    return out


def _row_json(row: LabelRow) -> dict[str, str]:
    entry = {
        "image_sha": row.image_sha,
        "set": row.set,
        "brief_id": row.brief_id,
        "notes": row.notes,
        "rubric_version": row.rubric_version,
    }
    entry.update({d: format_label(v) for d, v in row.dims().items()})
    return entry


def _reason_block(i: int, dim: str) -> str:
    options = "".join(
        f'<option value="{c}">{html.escape(REASON_LABELS[c])}</option>' for c in REASON_CODES
    )
    return f"""<div class="reason" data-reason-dim="{dim}">
<label>Why did <strong>{dim}</strong> fail? <span class="req-star">required</span>
<select name="{i}-reason-{dim}" data-reason="{dim}" required>
<option value="">choose a reason</option>{options}</select></label>
<input type="text" data-reason-note="{dim}" placeholder="note (optional; say why for other)">
<p class="auto" data-auto="{dim}" hidden></p>
</div>"""


def _reason_card_html(i: int, card: Card, ref_src: str, failed: list[str]) -> str:
    dims = "".join(
        f"""<fieldset class="dim" data-field="{d}"{"" if d in REASON_EDITABLE else " disabled"}>
<legend>{d}</legend>
<label><input type="radio" name="{i}-{d}" value="pass" data-dim="{d}"> pass</label>
<label><input type="radio" name="{i}-{d}" value="fail" data-dim="{d}"> fail</label>
<span class="was" data-was="{d}"></span>
</fieldset>"""
        for d in DIMENSIONS
    )
    reasons = "".join(_reason_block(i, d) for d in failed)
    return f"""<article class="card" data-key="{html.escape(card.key)}" data-sha="{card.image_sha}"
 data-set="{card.set}" data-brief="{card.brief_id}">
<div class="pics">
<figure><img src="{card.src}" alt="{html.escape(card.title)}" loading="lazy">
<figcaption>Ad</figcaption></figure>
<figure class="ref"><img src="{ref_src}" alt="reference product" loading="lazy">
<figcaption>Reference · {html.escape(card.product)}</figcaption></figure>
</div>
<div class="meta">
<h2>{html.escape(card.title)}</h2>
<dl>
<dt>Product</dt><dd>{html.escape(card.product)}</dd>
<dt>Market</dt><dd>{html.escape(card.market)}</dd>
<dt>Season</dt><dd>{html.escape(card.season)}</dd>
<dt>Required text</dt><dd class="req-inline">{html.escape(card.text)}</dd>
</dl>
{reasons}
<div class="dims">{dims}</div>
<p class="orig-notes" data-orig-notes></p>
</div>
</article>"""


def render_reasons_sheet(paths: GoldenPaths, *, embed: bool = True) -> str:
    """The v1 relabel sheet: failed product/context rows only, labels preloaded, blind."""
    golden = load_golden(paths)
    labels = read_labels(paths.labels)
    targets = reason_targets(labels)
    cards = [c for c in build_cards(paths, embed=embed) if c.key in targets]
    missing = set(targets) - {c.key for c in cards}
    if missing:
        raise ValueError(f"labels without an output image: {sorted(missing)}")
    by_key = {f"{sha}|{s}": row for (sha, s), row in labels.items()}
    preload = {k: _row_json(by_key[k]) for k in targets}
    unchanged = {k: _row_json(r) for k, r in by_key.items() if k not in targets}
    version = paths.version or "scratch"
    save_as = f"data/golden/{version}/labels.csv" if paths.version else "labels.csv"
    card_html: list[str] = []
    for i, card in enumerate(cards):
        product_id = golden.brief(card.brief_id).product
        ref = _ref_src(paths, golden.products[product_id].file, embed)
        card_html.append(_reason_card_html(i, card, ref, targets[card.key]))
    values = {
        "TITLE": f"Golden {version} fail reasons",
        "N": str(len(cards)),
        "NDIMS": str(sum(len(v) for v in targets.values())),
        "RUBRIC_VERSION": html.escape(RUBRIC_VERSION),
        "RUBRIC_JSON": json.dumps(RUBRIC_VERSION),
        "COLUMNS": json.dumps(list(LABEL_COLUMNS)),
        "DIMS": json.dumps(list(DIMENSIONS)),
        "STORE": json.dumps(f"golden-labels-{version}-r{RUBRIC_VERSION}-reasons"),
        "PRELOAD": json.dumps(preload, sort_keys=True),
        "UNCHANGED": json.dumps(unchanged, sort_keys=True),
        "ORDER": json.dumps(list(by_key)),
        "TARGETS": json.dumps(targets, sort_keys=True),
        "COMP_REASONS": json.dumps(sorted(COMPOSITION_REASONS)),
        # keep the file's line endings so unchanged rows stay byte-identical
        "EOL": json.dumps("\r\n" if b"\r\n" in paths.labels.read_bytes() else "\n"),
        "SAVE_AS": html.escape(save_as),
        "RUBRIC": rubric_html(
            paths.rubric.read_text(encoding="utf-8") if paths.rubric.exists() else ""
        ),
        "CARDS": "\n".join(card_html),
    }
    page = REASONS_TEMPLATE.read_text(encoding="utf-8")
    for key, value in values.items():
        page = page.replace(f"@@{key}@@", value)
    return page


def write_reasons_sheet(paths: GoldenPaths, *, embed: bool = True) -> int:
    html_text = render_reasons_sheet(paths, embed=embed)
    target = paths.out / REASONS_SHEET
    target.write_text(html_text, encoding="utf-8")
    return html_text.count('class="card"')
