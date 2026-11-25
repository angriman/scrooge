"""A broker's accounts in each currency as one account of the accounts page (`together.rows`)."""

from datetime import date
from decimal import Decimal

from scrooge import months, together
from scrooge.holds import Cash, Holds, MonthEnd
from scrooge.months import Coverage, Span
from scrooge.networth import CASH
from tests.storage.helpers import OWNER, PARTNER

D = Decimal
AUG, SEP, OCT = date(2026, 8, 31), date(2026, 9, 30), date(2026, 10, 31)


def file(number: int, last: date, source: str = "ibkr", committed: bool = True) -> months.Statement:
    return months.Statement(
        id=number,
        owner=OWNER,
        source=source,
        filename=f"statement {number}.csv",
        sha256=str(number),
        first=last.replace(day=1),
        last=last,
        checked=True,
        imported_at="2026-11-01T10:00:00",
        transactions=1,
        committed_at="2026-11-01T10:00:00" if committed else None,
    )


def held(code: str, amount: str, converted: str, *ends: tuple[date, str]) -> Holds:
    return Holds(
        D(converted),
        (Cash(CASH, code, D(amount), D(converted)),),
        None,
        tuple(MonthEnd(day, D(value)) for day, value in ends),
    )


# ACME, an invented broker: one statement a month lists its francs, euros and dollars; it
# stopped listing pounds after August. Migros is a bank, with a second account in euros of a
# statement of its own. Anna has dollars at ACME too.
MONTHLY = (file(1, AUG), file(2, SEP), file(3, OCT))
BROKER = {
    "ACME CHF": MONTHLY,
    "ACME EUR": MONTHLY,
    "ACME USD": (*MONTHLY, file(4, date(2026, 11, 30), committed=False)),
    "ACME GBP": MONTHLY[:1],
}
LINES = (
    Coverage(OWNER, "ACME CHF", "ibkr", OCT, None),
    Coverage(OWNER, "ACME EUR", "ibkr", OCT, None),
    Coverage(OWNER, "ACME GBP", "ibkr", AUG, None),
    Coverage(OWNER, "ACME USD", "ibkr", OCT, date(2026, 11, 30)),
    Coverage(OWNER, "Migros", "migros", OCT, None),
    Coverage(OWNER, "Migros EUR", "migros", SEP, None),
    Coverage(PARTNER, "ACME USD", "ibkr", OCT, None),
)
FILES = {
    **{(OWNER, name): listed for name, listed in BROKER.items()},
    (OWNER, "Migros"): (file(5, OCT, "migros"),),
    (OWNER, "Migros EUR"): (file(6, SEP, "migros"),),
    (PARTNER, "ACME USD"): (file(7, OCT),),
}
TRACKS = {
    (OWNER, "ACME CHF", "ibkr"): (Span(date(2026, 8, 1), OCT, True),),
    (OWNER, "ACME EUR", "ibkr"): (Span(date(2026, 8, 1), OCT, True),),
    (OWNER, "ACME GBP", "ibkr"): (Span(date(2026, 8, 1), AUG, True),),
    (OWNER, "ACME USD", "ibkr"): (
        Span(date(2026, 11, 1), date(2026, 11, 30), False),
        Span(date(2026, 8, 1), OCT, True),
    ),
    (OWNER, "Migros", "migros"): (Span(date(2026, 10, 1), OCT, True),),
}
HELD = {
    (OWNER, "ACME CHF"): held("CHF", "100", "100", (SEP, "50"), (OCT, "100")),
    (OWNER, "ACME EUR"): held("EUR", "1000", "900", (OCT, "900")),
    (OWNER, "ACME USD"): Holds(
        D("1300.00"),
        (Cash(CASH, "USD", D("1000"), D("800")),),
        D("500.00"),
        (MonthEnd(SEP, D("1200")), MonthEnd(OCT, D("1300"))),
    ),
    (OWNER, "ACME GBP"): held("GBP", "0", "0"),
    (OWNER, "Migros"): held("CHF", "700", "700", (OCT, "700")),
}


def test_a_brokers_currencies_are_one_account_and_other_accounts_stay_apart() -> None:
    rows = together.arranged(LINES, TRACKS, FILES, HELD)
    assert [(row.line.owner, row.line.account, row.parts) for row in rows] == [
        (PARTNER, "ACME USD", ("ACME USD",)),
        (OWNER, "ACME", ("ACME CHF", "ACME EUR", "ACME GBP", "ACME USD")),
        (OWNER, "Migros", ("Migros",)),
        (OWNER, "Migros EUR", ("Migros EUR",)),
    ]
    broker = rows[1]
    # Covered as far as its newest statement goes, with what is staged beyond it.
    assert broker.line == Coverage(OWNER, "ACME", "ibkr", OCT, date(2026, 11, 30))
    assert broker.spans == (
        Span(date(2026, 11, 1), date(2026, 11, 30), False),
        Span(date(2026, 8, 1), OCT, True),
    )
    # Each statement once, newest first, though it covers every currency.
    assert [s.id for s in broker.statements] == [4, 3, 2, 1]
    # What it holds is what its currencies hold together.
    assert broker.held == Holds(
        D("2300.00"),
        (
            Cash(CASH, "EUR", D("1000"), D("900")),
            Cash(CASH, "USD", D("1000"), D("800")),
            Cash(CASH, "CHF", D("100"), D("100")),
            Cash(CASH, "GBP", D("0"), D("0")),
        ),
        D("500.00"),
        (MonthEnd(SEP, D("1250")), MonthEnd(OCT, D("2300"))),
    )
    # An account alone is as it was.
    assert rows[2].line == LINES[4]
    assert rows[2].held == HELD[OWNER, "Migros"]
    assert rows[3].held is None


def test_a_currency_the_broker_stopped_listing_does_not_leave_it_behind() -> None:
    apart = months.behind(LINES)
    assert apart[2]  # the pounds alone end in August
    rows = together.arranged(LINES, TRACKS, FILES, HELD)
    shown = [row.line for row in rows]
    assert months.behind(shown) == (False, False, False, False)
    assert months.covered(shown) == SEP  # Migros EUR, a statement of its own, ends there
    assert months.covered(LINES) == AUG


def test_accounts_named_by_a_currency_that_share_no_statement_stay_apart() -> None:
    lines: tuple[Coverage, ...] = (
        Coverage(OWNER, "ACME CHF", "ibkr", OCT, None),
        Coverage(OWNER, "ACME USD", "ibkr", OCT, None),
    )
    files = {(OWNER, "ACME CHF"): (file(1, OCT),), (OWNER, "ACME USD"): (file(2, OCT),)}
    rows = together.arranged(lines, {}, files, {})
    assert [row.line.account for row in rows] == ["ACME CHF", "ACME USD"]
    assert [(row.spans, row.held) for row in rows] == [((), None), ((), None)]
    # An account not named by a currency is never another's part, statement or not.
    lines = (Coverage(OWNER, "ACME", "ibkr", OCT, None), lines[1])
    files = {(OWNER, "ACME"): (file(2, OCT),), (OWNER, "ACME USD"): (file(2, OCT),)}
    rows = together.arranged(lines, {}, files, {})
    assert [row.parts for row in rows] == [("ACME",), ("ACME USD",)]
    # Nor are accounts of a currency joined under a name another account has: no two rows
    # would tell apart.
    lines += (Coverage(OWNER, "ACME CHF", "ibkr", OCT, None),)
    files[OWNER, "ACME CHF"] = (file(2, OCT),)
    rows = together.arranged(lines, {}, files, {})
    assert [row.parts for row in rows] == [("ACME",), ("ACME CHF",), ("ACME USD",)]


def test_a_statement_that_joins_two_groups_makes_them_one() -> None:
    # Francs and euros, then dollars and euros: the euros' statements join them all.
    lines = tuple(Coverage(OWNER, f"ACME {code}", "ibkr", OCT, None) for code in ("CHF", "USD"))
    lines += (Coverage(OWNER, "ACME EUR", "ibkr", OCT, None),)
    files = {
        (OWNER, "ACME CHF"): (file(1, SEP),),
        (OWNER, "ACME USD"): (file(2, OCT),),
        (OWNER, "ACME EUR"): (file(2, OCT), file(1, SEP)),
    }
    (row,) = together.arranged(lines, {}, files, {})
    assert (row.line.account, row.parts) == ("ACME", ("ACME CHF", "ACME EUR", "ACME USD"))
    assert [s.id for s in row.statements] == [2, 1]


def test_nothing_known_is_nothing_together() -> None:
    assert together.combined([]) is None
    one = HELD[OWNER, "Migros"]
    assert together.combined([one]) is one
    # Part of it known is not all of it: no total that looks complete and is not.
    assert together.combined([one, None]) is None
    known = {key: found for key, found in HELD.items() if key != (OWNER, "ACME GBP")}
    rows = together.arranged(LINES, TRACKS, FILES, known)
    assert (rows[1].line.account, rows[1].held) == ("ACME", None)


def test_a_brokers_currencies_are_closed_together_once_all_of_them_are() -> None:
    first, last = date(2026, 9, 30), OCT
    lines = (
        Coverage(OWNER, "ACME CHF", "ibkr", OCT, None, first),
        Coverage(OWNER, "ACME USD", "ibkr", OCT, None, None),
    )
    files = {(OWNER, "ACME CHF"): (file(1, OCT),), (OWNER, "ACME USD"): (file(1, OCT),)}
    [row] = together.arranged(lines, {}, files, {})
    assert (row.line.account, row.line.closed) == ("ACME", None)
    lines = (lines[0], Coverage(OWNER, "ACME USD", "ibkr", OCT, None, last))
    [row] = together.arranged(lines, {}, files, {})
    assert row.line.closed == last
