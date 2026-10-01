"""`python -m evals`: the golden eval (ai-design §9, ADR-004).

    python -m evals --all-versions  every golden version with outputs (data/golden/v1, v2) + the
                                    v1 -> v2 comparison (`make eval`)
    python -m evals                 replay cache/verdicts.json (no key, sockets blocked) -> report
    python -m evals --record        record the verdict snapshot (live only for missing answers)
    python -m evals --smoke         the scaffold's one-call gateway smoke check

`--golden DIR` reads outputs/planted/labels/cache from DIR (a dry run's scratch dir) while briefs
and product photos always come from data/golden. `--reports DIR` writes the report elsewhere.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from backend.core.settings import get_settings
from backend.golden.dataset import GOLDEN_DIR, GOLDEN_VERSIONS, GoldenPaths, golden_out
from backend.golden.harness import GoldenDataError, StaleSnapshotError
from evals.report import REPORTS_DIR


def _parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals")
    parser.add_argument("--golden", type=Path, default=golden_out(), help="dataset out dir")
    parser.add_argument(
        "--all-versions",
        action="store_true",
        help="report every golden version that has outputs, plus the v1 -> v2 comparison",
    )
    parser.add_argument("--source", type=Path, default=GOLDEN_DIR, help="briefs + products dir")
    parser.add_argument("--reports", type=Path, default=REPORTS_DIR)
    parser.add_argument("--record", action="store_true", help="record cache/verdicts.json")
    parser.add_argument("--rerun", type=int, default=0, help="with --record: judge N items twice")
    parser.add_argument("--baseline", action="store_true", help="reset the baseline to this run")
    parser.add_argument(
        "--assume-pass-labels",
        action="store_true",
        help="dry runs only: treat unlabelled E-nat/E-final images as all-pass",
    )
    parser.add_argument("--no-db", action="store_true", help="do not write an eval_reports row")
    parser.add_argument("--smoke", action="store_true", help="run the gateway smoke check")
    return parser.parse_args()


async def _smoke() -> None:
    from evals.suites import gateway_smoke

    print(await gateway_smoke.run())


async def _record(args: argparse.Namespace, paths: GoldenPaths) -> None:
    from backend.db.session import create_database
    from evals.golden import record

    settings = get_settings()
    db = create_database(settings.database_url)
    try:
        out = await record(paths, settings, sessionmaker=db.sessionmaker, rerun=args.rerun)
    finally:
        await db.dispose()
    print(
        f"verdicts: {out.entries} entries -> {paths.verdicts} "
        f"(snapshot hits {out.hits_snapshot}, DB-cache hits {out.hits_db}, live calls {out.live})"
    )
    if args.rerun:
        print(f"judge-stability rerun: {out.rerun_entries} entries -> {paths.verdicts_rerun}")


async def _register(report_path: Path, data: object) -> str | None:
    """An `eval_reports` row for the dashboard, when the DB is reachable."""
    from backend.db.session import create_database
    from backend.domain.adstudio.evalreport import EvalReportData
    from backend.golden.importer import register_report

    if not isinstance(data, EvalReportData):
        return None
    db = create_database(get_settings().database_url)
    try:
        async with db.sessionmaker() as session:
            row = await register_report(session, data, str(report_path))
            await session.commit()
            return str(row.id)
    except Exception as exc:  # noqa: BLE001 - the report files are the source of truth
        print(f"(no eval_reports row: database unavailable, {type(exc).__name__})")
        return None
    finally:
        await db.dispose()


async def _eval(args: argparse.Namespace, paths: GoldenPaths) -> None:
    from evals.golden import run_eval

    outcome = await run_eval(
        paths,
        get_settings(),
        reports_dir=args.reports,
        assume_pass=args.assume_pass_labels,
        reset_baseline=args.baseline,
    )
    data = outcome.report
    print(f"report: {outcome.path}")
    if data.dry_run:
        print("FAKE DRY RUN: images/verdicts come from the fake clients")
    for c in data.criteria:
        met = "—" if c.met is None else ("met" if c.met else "NOT MET")
        print(f"  {c.criterion:34s} {c.metric:52s} now={c.now!s:22s} {met}")
    if not args.no_db:
        row = await _register(outcome.path, data)
        if row:
            print(f"eval_reports row {row}")


async def _eval_all(args: argparse.Namespace) -> int:
    """Each version's report in version order (the newest is registered last, so the dashboard
    shows it), then the comparison rewritten onto the newest one."""
    from backend.golden.dataset import read_outputs
    from evals.golden import EvalOutcome, attach_comparison, run_eval

    outcomes: list[EvalOutcome] = []
    failed = 0
    for version in GOLDEN_VERSIONS:
        paths = GoldenPaths(source=args.source, out=golden_out(version))
        if not read_outputs(paths):
            print(f"{version}: no outputs yet ({paths.outputs}); skipped")
            continue
        try:
            outcome = await run_eval(
                paths,
                get_settings(),
                reports_dir=args.reports,
                reset_baseline=args.baseline,
            )
        except (GoldenDataError, StaleSnapshotError) as exc:
            print(f"{version}: error: {exc}", file=sys.stderr)
            failed += 1
            continue
        outcomes.append(outcome)
    attach_comparison(outcomes, reports_dir=args.reports)
    for outcome in outcomes:
        data = outcome.report
        print(f"{data.golden_version}: report {outcome.path}")
        for c in data.criteria:
            met = "—" if c.met is None else ("met" if c.met else "NOT MET")
            print(f"  {c.criterion:34s} {c.metric:52s} now={c.now!s:22s} {met}")
        if not args.no_db:
            row = await _register(outcome.path, data)
            if row:
                print(f"  eval_reports row {row}")
    if len(outcomes) > 1:
        print("comparison: " + " -> ".join(o.report.golden_version or "?" for o in outcomes))
    return 2 if failed else 0


def main() -> None:
    args = _parse()
    paths = GoldenPaths(source=args.source, out=args.golden)
    try:
        if args.all_versions:
            code = asyncio.run(_eval_all(args))
            if code:
                raise SystemExit(code)
        elif args.smoke:
            asyncio.run(_smoke())
        elif args.record:
            asyncio.run(_record(args, paths))
        else:
            asyncio.run(_eval(args, paths))
    except (GoldenDataError, StaleSnapshotError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
