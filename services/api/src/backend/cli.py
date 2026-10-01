"""Golden CLI (architecture "Offline batch and eval path", ADR-004).

    python -m backend.cli golden run      [--only B01,B02] [--concurrency 2] [--budget 5] [--force]
    python -m backend.cli golden profile  [--force]
    python -m backend.cli golden export   [--no-verdicts] [--no-live]
    python -m backend.cli golden import   [--reports DIR]
    python -m backend.cli golden plant    [--assume-pass] [--seed 16] [--no-generate]
    python -m backend.cli golden sheet    [--link] [--composition | --reasons]
    python -m backend.cli golden repair-probe [--only V12] [--out FILE]

Common options: `--version v1|v2` (the golden version dir, data/golden/<version>; default v2),
`--golden DIR` (an explicit out dir for outputs/, planted/, labels.csv and cache/, e.g. a scratch
dir; overrides --version), `--source DIR` (briefs.yaml and products; default data/golden) and
`--fake` (the fake image and vision clients: a key-less dry run). The DB and blob store come from
DATABASE_URL and BLOB_DIR, so a dry run can point both at scratch locations.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from backend.core.errors import AppError
from backend.core.settings import Settings, get_settings
from backend.golden.dataset import CURRENT_VERSION, GOLDEN_DIR, GoldenPaths, golden_out


def _settings(args: argparse.Namespace) -> Settings:
    settings = get_settings()
    if args.fake:
        settings = settings.model_copy(
            update={"image_client": "fake", "vision_client": "fake", "fake_image_script": ""}
        )
    return settings


def _paths(args: argparse.Namespace) -> GoldenPaths:
    out = args.golden if args.golden is not None else golden_out(args.version)
    return GoldenPaths(source=args.source.resolve(), out=out.resolve())


async def _run(args: argparse.Namespace) -> int:
    from backend.golden.context import build_context
    from backend.golden.runner import golden_run

    ctx = build_context(_settings(args))
    only = {b.strip() for b in args.only.split(",") if b.strip()} if args.only else None
    try:
        outcomes = await golden_run(
            ctx,
            _paths(args),
            only=only,
            concurrency=args.concurrency,
            budget_usd=args.budget,
            force=args.force,
        )
    finally:
        await ctx.close()
    print(
        f"{'brief':6s} {'status':13s} {'outcome':8s} {'repairs':>7s} {'cost':>9s} "
        f"{'latency':>9s}  note"
    )
    for o in outcomes:
        latency = f"{o.latency_ms / 1000:.1f}s" if o.latency_ms else "—"
        print(
            f"{o.brief_id:6s} {o.status:13s} {o.outcome or '—':8s} {o.repair_count:7d} "
            f"${o.cost_usd:8.4f} {latency:>9s}  {o.note}"
        )
    total = sum(o.cost_usd for o in outcomes)
    print(f"golden runs: {len(outcomes)} briefs, ledger spend ${total:.4f}")
    return 0 if all(o.status not in ("pending", "failed") for o in outcomes) else 1


async def _profile(args: argparse.Namespace) -> int:
    from backend.golden.context import build_context
    from backend.golden.runner import profile_products

    ctx = build_context(_settings(args))
    try:
        outcomes = await profile_products(ctx, _paths(args), force=args.force)
    finally:
        await ctx.close()
    for o in outcomes:
        f = o.facts or {}
        print(
            f"{o.product_id} {'re-profiled' if o.refreshed else 'current':11s} "
            f"{f.get('profile_version', '?'):5s} {f.get('category', '')!s:32.32s} "
            f"size_class={f.get('size_class')} cm={f.get('approx_max_dimension_cm')} "
            f"surface={f.get('typical_surface')!r}"
        )
    return 0 if all((o.facts or {}).get("status") == "verified" for o in outcomes) else 1


async def _export(args: argparse.Namespace) -> int:
    from backend.golden.context import build_context
    from backend.golden.export import golden_export

    ctx = build_context(_settings(args))
    paths = _paths(args)
    try:
        result = await golden_export(
            ctx,
            paths,
            record_verdicts=not args.no_verdicts,
            allow_live=not args.no_live,
            only={b.strip() for b in args.only.split(",") if b.strip()} if args.only else None,
        )
    finally:
        await ctx.close()
    print(f"exported {len(result.items)} images -> {paths.output_manifest}")
    if result.missing:
        print(f"no finished golden run for: {', '.join(result.missing)}")
    print(f"labels.csv: {result.labels_added} human labels merged from the DB")
    if not args.no_verdicts:
        print(f"verdicts: {result.verdict_entries} entries -> {paths.verdicts}")
    if result.verdict_note:
        print(result.verdict_note)
    return 0 if result.items and not result.missing else 1


async def _import(args: argparse.Namespace) -> int:
    from backend.db.session import create_database
    from backend.golden.importer import golden_import
    from backend.storage.blobs import BlobStore

    settings = _settings(args)
    db = create_database(settings.database_url)
    try:
        result = await golden_import(
            db.sessionmaker,
            BlobStore(settings.blob_root),
            _paths(args),
            tenant=settings.default_tenant,
            reports_dir=args.reports.resolve(),
            max_upload_bytes=settings.max_upload_bytes,
        )
    finally:
        await db.dispose()
    print(
        f"imported {result.images} output images, {result.labels} human labels, "
        f"{result.planted} planted items, {result.evaluations} evaluations, golden runs "
        f"{result.runs_created} created / {result.runs_linked} linked"
        + (f"; eval report {result.report}" if result.report else "; no eval report found")
    )
    return 0


async def _plant(args: argparse.Namespace) -> int:
    from backend.golden.plant import plant_generated, plant_pure, write_planted

    paths = _paths(args)
    pure = await plant_pure(paths, seed=args.seed, assume_pass=args.assume_pass)
    items = list(pure.items)
    skipped = list(pure.skipped)
    if args.keep_generated:
        from backend.golden.plant import keep_generated

        items += keep_generated(paths, items)
    elif not args.no_generate:
        from backend.golden.context import build_context

        ctx = build_context(_settings(args))
        try:
            generated = await plant_generated(ctx, paths, seed=args.seed, start=len(items))
        finally:
            await ctx.close()
        items += generated.items
        skipped += generated.skipped
    write_planted(paths, items)
    by_kind: dict[str, int] = {}
    for item in items:
        by_kind[item.mutation] = by_kind.get(item.mutation, 0) + 1
    print(
        f"planted {len(items)} items from {pure.bases} all-pass E-nat bases -> "
        f"{paths.planted_manifest}"
    )
    for mutation, n in by_kind.items():
        print(f"  {mutation:22s} {n}")
    for note in skipped:
        print(f"  skipped: {note}")
    return 0


async def _sheet(args: argparse.Namespace) -> int:
    from backend.golden.sheet import REASONS_SHEET, sheet_path, write_reasons_sheet, write_sheet

    paths = _paths(args)
    if args.reasons:
        n = write_reasons_sheet(paths, embed=not args.link)
        print(f"reason sheet: {n} cards -> {paths.out / REASONS_SHEET}")
        return 0
    n = write_sheet(paths, embed=not args.link, composition=args.composition)
    print(f"labelling sheet: {n} cards -> {sheet_path(paths, composition=args.composition)}")
    return 0


async def _repair_probe(args: argparse.Namespace) -> int:
    import dataclasses
    import json

    from backend.golden.context import build_context
    from backend.golden.repair_probe import run_probe
    from backend.storage.blobs import BlobStore

    ctx = build_context(_settings(args))
    paths = _paths(args)
    only = {b.strip() for b in args.only.split(",") if b.strip()} if args.only else None
    try:
        outcomes = await run_probe(ctx, paths, only)
    finally:
        await ctx.close()
    out = args.out or paths.out / "repair_probe.json"
    images = out.parent / "repair_probe"  # every step's image, for a re-score elsewhere
    images.mkdir(parents=True, exist_ok=True)
    for o in outcomes:
        for n, step in enumerate(o.steps):
            name = f"{o.brief_id}-s{o.slot}-{n}-{step.kind}.png"
            (images / name).write_bytes(BlobStore(ctx.settings.blob_root).read(step.image_sha))
    out.write_text(
        json.dumps([dataclasses.asdict(o) for o in outcomes], indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"{'draft':9s} {'failed':22s} {'status':13s} {'reason':16s} {'repairs':>7s} {'cost':>9s}")
    for o in outcomes:
        failed = ",".join(o.draft_failed + [f"{d}?" for d in o.draft_unverified]) or "-"
        print(
            f"{o.brief_id}-s{o.slot:<3d} {failed:22s} {o.status:13s} {o.reason or '-':16s} "
            f"{o.repairs:7d} ${o.cost_usd:8.4f}  {' > '.join(s.kind for s in o.steps)} {o.note}"
        )
    print(f"repair probe: {len(outcomes)} failing drafts -> {out}")
    return 0 if all(o.status != "pending" for o in outcomes) else 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m backend.cli")
    groups = parser.add_subparsers(dest="group", required=True)
    golden = groups.add_parser("golden", help="golden dataset steps")
    commands = golden.add_subparsers(dest="command", required=True)

    def command(name: str, help_: str) -> argparse.ArgumentParser:
        p = commands.add_parser(name, help=help_)
        p.add_argument(
            "--golden", type=Path, default=None, help="out dir (default data/golden/<version>)"
        )
        p.add_argument(
            "--version",
            default=CURRENT_VERSION,
            help="golden version out dir: v1 | v2 (default) | v3",
        )
        p.add_argument("--source", type=Path, default=GOLDEN_DIR)
        p.add_argument("--fake", action="store_true", help="fake image + vision clients (dry run)")
        return p

    run = command("run", "run every golden brief through the pipeline")
    run.add_argument("--only", default="", help="comma-separated brief ids")
    run.add_argument("--concurrency", type=int, default=2)
    run.add_argument("--budget", type=float, default=5.0, help="batch spend cap in USD")
    run.add_argument(
        "--force",
        action="store_true",
        help="start a new run even if the brief's latest run is finished (it supersedes it)",
    )
    profile = command("profile", "(re-)profile the golden products (size class, cm, surface)")
    profile.add_argument("--force", action="store_true", help="re-profile current facts too")
    export = command("export", "write outputs/, manifest.jsonl, labels.csv, cache/verdicts.json")
    export.add_argument("--no-verdicts", action="store_true")
    export.add_argument("--only", default="", help="comma-separated brief ids")
    export.add_argument("--no-live", action="store_true", help="never call OCR/the judge live")
    imp = command("import", "load the golden files into the DB for the dashboard")
    imp.add_argument(
        "--reports", type=Path, default=Path(__file__).resolve().parents[2] / "evals" / "reports"
    )
    plant = command("plant", "build E-plant (planted failures)")
    plant.add_argument(
        "--assume-pass", action="store_true", help="dry runs: unlabelled E-nat = all-pass"
    )
    plant.add_argument("--seed", type=int, default=16)
    plant.add_argument("--no-generate", action="store_true", help="skip the 6 generated items")
    plant.add_argument(
        "--keep-generated",
        action="store_true",
        help="regenerate the pure items only; keep the manifest's generated items (and files)",
    )
    probe = command("repair-probe", "live repair loop on each failing first-round draft")
    probe.add_argument("--only", default="", help="comma-separated brief ids")
    probe.add_argument("--out", type=Path, default=None, help="default <out dir>/repair_probe.json")
    sheet = command("sheet", "write labeling_sheet.html")
    sheet.add_argument("--link", action="store_true", help="link images instead of embedding them")
    sheet.add_argument(
        "--composition",
        action="store_true",
        help="composition pass: existing labels preloaded, only composition (+ technical) open",
    )
    sheet.add_argument(
        "--reasons",
        action="store_true",
        help="reason codes for the product/context fails (labels preloaded; analysis R1b)",
    )

    args = parser.parse_args(argv)
    handlers = {
        "run": _run,
        "profile": _profile,
        "export": _export,
        "import": _import,
        "plant": _plant,
        "sheet": _sheet,
        "repair-probe": _repair_probe,
    }
    try:
        code = asyncio.run(handlers[args.command](args))
    except AppError as exc:
        print(f"error: {exc.type}: {exc.detail or exc.title}", file=sys.stderr)
        code = 2
    except (RuntimeError, LookupError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
