"""Save the month as files to paste into the sheet by hand. Nothing here talks to Google.

One tab-separated file per destination, without a header, so the whole file pastes at the cell
named in `Pasted.where`. Numbers use the decimal mark of the sheet's locale (with the wrong one
the sheet takes them for text); dates are ISO, which every locale reads as a date.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from finanze.aggregate import Budget, BudgetRow
from finanze.models import SecurityTrade
from finanze.writers import assets
from finanze.writers.assets import Row, Value

TAB = "Budget Mensile"
FIRST_ROW = 5
DECIMAL_MARKS = {"it": ",", "en": "."}  # by the sheet's locale (File > Settings)
DEFAULT_LOCALE = "it"
_BUDGET_FILES = ("budget_uscite.tsv", "budget_entrate.tsv")
_ASSET_FILES = {"ETFs": "etfs.tsv", "Stocks": "stocks.tsv", "Bonds": "bonds.tsv"}


@dataclass(frozen=True, slots=True)
class Pasted:
    path: Path
    where: str  # where the file's content goes in the sheet
    rows: int


def write(
    folder: Path, budget: Budget, trades: Sequence[SecurityTrade], locale: str
) -> tuple[Pasted, ...]:
    """Write the month's files into `folder`, replacing those of an earlier run.

    A destination with no rows gets no file, and loses the one an earlier run left there.
    """
    mark = DECIMAL_MARKS[locale]
    uscite, entrate = _BUDGET_FILES
    targets: list[tuple[str, str, Sequence[Row]]] = [
        (uscite, f"{TAB}!A{FIRST_ROW}", _budget_rows(budget.uscite)),
        (entrate, f"{TAB}!F{FIRST_ROW}", _budget_rows(budget.entrate)),
    ]
    # Every row is built before any file is touched: a trade that fits no tab writes nothing.
    for log in assets.logs(trades):
        where = f"{log.tab}: below the log that starts at A{log.first_row}"
        targets.append((_ASSET_FILES[log.tab], where, log.rows))

    folder.mkdir(parents=True, exist_ok=True)
    written: list[Pasted] = []
    for name, where, rows in targets:
        if not rows:
            continue
        path = folder / name
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            writer.writerows([_text(value, mark) for value in row] for row in rows)
        written.append(Pasted(path, where, len(rows)))
    kept = {pasted.path.name for pasted in written}
    for name in (*_BUDGET_FILES, *_ASSET_FILES.values()):
        if name not in kept:
            (folder / name).unlink(missing_ok=True)
    return tuple(written)


def _budget_rows(rows: Sequence[BudgetRow]) -> list[Row]:
    return [[row.category, row.account, row.amount] for row in rows]


def _text(value: Value, mark: str) -> str:
    if isinstance(value, Decimal):
        return f"{value:f}".replace(".", mark)  # `f`: never an exponent, no thousands separator
    return value if isinstance(value, str) else value.isoformat()
