"""`finanze <folder>`: parse a month of statements into files to paste into the sheet.

Without `--write` nothing is saved: the month is extracted, reconciled, classified and printed.
With `--write` the rows of `Budget Mensile` and of the asset tabs are saved under `output/`,
ready to be pasted by hand. Nothing here ever connects to the sheet.

`finanze import`, `finanze commit` and `finanze reopen` work on the database instead: see
`finanze import --help`.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

import yaml

from finanze import classification, dbcli, pipeline, records, review
from finanze.aggregate import Budget, BudgetRow
from finanze.classification import ConfigError, UnknownInstrumentError
from finanze.errors import ExtractionError
from finanze.models import SecurityTrade
from finanze.pipeline import Month
from finanze.records import DuplicateRecordError
from finanze.validation import ReconciliationError
from finanze.writers import paste
from finanze.writers.assets import UnknownTabError

OK, FAILED, NEEDS_REVIEW = 0, 1, 2
_ERRORS = (
    ExtractionError,
    ReconciliationError,
    ConfigError,
    UnknownInstrumentError,
    UnknownTabError,
    DuplicateRecordError,
)


def main(
    argv: Sequence[str] | None = None,
    out: TextIO = sys.stdout,
    ask: Callable[[str], str] = input,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in dbcli.COMMANDS:
        return dbcli.main(arguments, out, ask)
    args = _parser().parse_args(arguments)
    try:
        return _run(args, out, ask)
    except _ERRORS as exc:
        out.write(f"error: {exc}\n")
        return FAILED


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finanze", description=__doc__)
    parser.add_argument("folder", type=Path, help="folder holding one month of statements")
    parser.add_argument(
        "--config", type=Path, default=Path("finanze.yaml"), help="sheet locale, account names"
    )
    parser.add_argument(
        "--rules",
        type=Path,
        action="append",
        help="rules file(s), first wins (default: rules.local.yaml, then rules.yaml)",
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="ask for the category of unmatched rows and save the answers as rules",
    )
    parser.add_argument("--instruments", type=Path, default=Path("instruments.yaml"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output"),
        help="where --write and --dump save, in a folder per month (default: output)",
    )
    parser.add_argument(
        "--dump", action="store_true", help="save every transaction as transactions.csv"
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="save the files to paste into the sheet, and transactions.csv",
    )
    return parser


def _config(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not isinstance(data.get("accounts", {}), dict):
        msg = f"{path}: expected a mapping, with 'accounts' a mapping of names"
        raise ConfigError(msg)
    if data.get("locale", paste.DEFAULT_LOCALE) not in paste.DECIMAL_MARKS:
        msg = f"{path}: 'locale' must be one of {', '.join(paste.DECIMAL_MARKS)}"
        raise ConfigError(msg)
    return data


def _run(args: argparse.Namespace, out: TextIO, ask: Callable[[str], str]) -> int:
    config = _config(args.config)
    rule_files = args.rules or [Path("rules.local.yaml"), Path("rules.yaml")]
    instruments = classification.load_instruments(args.instruments)
    extractions, skipped = pipeline.load_folder(args.folder)

    def process() -> Month:
        rules = classification.load_rules(rule_files)
        return pipeline.process(extractions, rules, instruments, skipped)

    month = process()
    if args.review and month.uncategorized:
        review.ask_categories(month.uncategorized, rule_files[0], out, ask)
        month = process()
    month = month.renamed(_names(config))

    lines = [f"{month.month:%B %Y}: {len(extractions)} statements, all reconciled"]
    lines += [f"  {source}: {len(e.transactions)} transactions" for source, e in extractions]
    lines += [f"  skipped {path.name} (not a statement)" for path in month.skipped]
    lines += [
        f"  note: {account} includes rows with no balance to check against"
        for account in month.unreconciled
    ]
    out.write("\n".join(lines) + "\n")
    saved_in = args.output / f"{month.month:%Y-%m}"
    if args.dump or args.write:
        target = saved_in / "transactions.csv"
        _dump(month, target)
        out.write(f"  saved {target}\n")

    pending = month.uncategorized
    if pending:
        out.write(f"\n{len(pending)} transactions need a category (rerun with --review):\n")
        out.writelines(
            f"  {txn.date}  {txn.account:<12} {txn.amount:>10}  {txn.description}\n"
            for txn in pending
        )
        return NEEDS_REVIEW

    planned = month.budget()
    _print_budget(planned, month.trades, out)
    if not args.write:
        out.write("\nDry run: nothing saved. Use --write to save the files to paste.\n")
        return OK

    locale = str(config.get("locale", paste.DEFAULT_LOCALE))
    pasted = paste.write(saved_in, planned, month.trades, locale)
    out.write(f"\nSaved in {saved_in} (tab-separated, no header, {locale!r} numbers):\n")
    out.writelines(f"  {p.path.name:<20} {p.rows:>3} rows -> {p.where}\n" for p in pasted)
    out.write(
        f"Set {paste.TAB}!A1 to {month.month}, clear the old rows, paste values only, "
        'then review and click "Archivia Mese".\n'
    )
    return OK


def _names(config: dict[str, object]) -> dict[str, str]:
    accounts = config.get("accounts")
    return {str(k): str(v) for k, v in accounts.items()} if isinstance(accounts, dict) else {}


def _print_budget(planned: Budget, trades: Sequence[SecurityTrade], out: TextIO) -> None:
    sides: tuple[tuple[str, tuple[BudgetRow, ...]], ...] = (
        ("Uscite", planned.uscite),
        ("Entrate", planned.entrate),
    )
    for title, rows in sides:
        out.write(f"\n{title}\n")
        out.writelines(f"  {row.category:<26} {row.account:<14} {row.amount:>12}\n" for row in rows)
    if trades:
        out.write("\nSecurity trades\n")
        out.writelines(
            f"  {trade.asset_tab:<7} {trade.date}  {trade.ticker:<10} "
            f"{trade.quantity:>10} @ {trade.price}  {trade.currency}\n"
            for trade in trades
        )


def _dump(month: Month, target: Path) -> None:
    rows = records.records(month.transactions)  # may raise: before the old dump is truncated
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=records.FIELDS)
        writer.writeheader()
        writer.writerows(rows)
