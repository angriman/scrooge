from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finanze.aggregate import Budget, BudgetRow
from finanze.writers import assets, paste
from tests.writers.test_assets import BILL, GOOG, VT, _trade

MONTH = date(2026, 9, 1)
BUDGET = Budget(
    MONTH,
    uscite=(
        BudgetRow("Affitto", "Migros", Decimal("1850.00")),
        BudgetRow("Spesa", "Revolut CHF", Decimal("1234567.80")),
    ),
    entrate=(BudgetRow("Stipendio", "Migros", Decimal("9000.05")),),
)


def _lines(path: Path) -> list[list[str]]:
    return [line.split("\t") for line in path.read_text(encoding="utf-8").splitlines()]


def test_each_destination_gets_a_headerless_tab_separated_file(tmp_path: Path) -> None:
    folder = tmp_path / "output" / "2026-09"  # created on demand
    pasted = paste.write(folder, BUDGET, [VT, GOOG, BILL], "it")
    assert [(p.path.name, p.where, p.rows) for p in pasted] == [
        ("budget_uscite.tsv", "Budget Mensile!A5", 2),
        ("budget_entrate.tsv", "Budget Mensile!F5", 1),
        ("etfs.tsv", "ETFs: below the log that starts at A27", 1),
        ("stocks.tsv", "Stocks: below the log that starts at A13", 1),
        ("bonds.tsv", "Bonds: below the log that starts at A11", 1),
    ]
    assert sorted(path.name for path in folder.iterdir()) == sorted(p.path.name for p in pasted)
    assert (folder / "budget_uscite.tsv").read_text(encoding="utf-8") == (
        "Affitto\tMigros\t1850,00\nSpesa\tRevolut CHF\t1234567,80\n"  # no thousands separator
    )
    assert _lines(folder / "budget_entrate.tsv") == [["Stipendio", "Migros", "9000,05"]]
    assert _lines(folder / "etfs.tsv") == [["VT", "2026-09-15", "48", "148,375", "7122", "USD"]]
    assert _lines(folder / "stocks.tsv") == [["GOOG", "2026-09-16", "5", "USD"]]
    assert _lines(folder / "bonds.tsv") == [
        ["US-T Govt Bill Dec24'26", "98,25", "40", "39300,00", "2026-09-09", "2026-12-24"]
    ]


def test_english_locale_keeps_the_decimal_point(tmp_path: Path) -> None:
    paste.write(tmp_path, BUDGET, [VT], "en")
    assert _lines(tmp_path / "budget_entrate.tsv") == [["Stipendio", "Migros", "9000.05"]]
    assert _lines(tmp_path / "etfs.tsv")[0][3] == "148.375"


def test_numbers_are_never_written_with_an_exponent(tmp_path: Path) -> None:
    lot = _trade("VT", "ETFs", date(2026, 9, 1), "4E+1", "1E-7", cost="0.000004")
    paste.write(tmp_path, Budget(MONTH, (), ()), [lot], "it")
    assert _lines(tmp_path / "etfs.tsv")[0][2:5] == ["40", "0,0000001", "0,000004"]


def test_a_rerun_replaces_the_files_and_drops_those_with_no_rows_left(tmp_path: Path) -> None:
    paste.write(tmp_path, BUDGET, [VT, GOOG], "it")
    (tmp_path / "transactions.csv").write_text("kept", encoding="utf-8")
    before = (tmp_path / "budget_uscite.tsv").read_bytes()

    pasted = paste.write(tmp_path, Budget(MONTH, BUDGET.uscite, ()), [GOOG], "it")
    assert [p.path.name for p in pasted] == ["budget_uscite.tsv", "stocks.tsv"]
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "budget_uscite.tsv",
        "stocks.tsv",
        "transactions.csv",  # not ours to remove
    ]
    assert (tmp_path / "budget_uscite.tsv").read_bytes() == before


def test_a_trade_that_cannot_be_placed_writes_nothing(tmp_path: Path) -> None:
    folder = tmp_path / "2026-09"
    crypto = _trade("BTC", "Crypto", date(2026, 9, 1), "1", "1", cost="1")
    with pytest.raises(assets.UnknownTabError):
        paste.write(folder, BUDGET, [VT, crypto], "it")
    assert not folder.exists()


_amounts = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("9999999.99"), places=2)
_rows = st.lists(st.builds(BudgetRow, st.just("Spesa"), st.just("Migros"), _amounts), max_size=8)


@given(uscite=_rows, entrate=_rows, locale=st.sampled_from(sorted(paste.DECIMAL_MARKS)))
def test_pasted_amounts_add_up_to_the_budget(
    tmp_path_factory: pytest.TempPathFactory,
    uscite: list[BudgetRow],
    entrate: list[BudgetRow],
    locale: str,
) -> None:
    folder = tmp_path_factory.mktemp("paste")
    paste.write(folder, Budget(MONTH, tuple(uscite), tuple(entrate)), [], locale)
    mark = paste.DECIMAL_MARKS[locale]
    for name, rows in (("budget_uscite.tsv", uscite), ("budget_entrate.tsv", entrate)):
        path = folder / name
        assert path.exists() == bool(rows)
        read = [Decimal(line[2].replace(mark, ".")) for line in _lines(path)] if rows else []
        assert sum(read) == sum(row.amount for row in rows)
