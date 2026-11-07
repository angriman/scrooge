import sqlite3
from collections.abc import Iterator
from datetime import date

import pytest

from finanze import query, report
from finanze.report import Shortcut
from finanze.storage import db
from tests import test_savings as rows
from tests.storage.helpers import CLOSED_AT, OWNER, PARTNER, close

END = rows.END


@pytest.fixture(scope="module")
def conn() -> Iterator[sqlite3.Connection]:
    """The rows of the savings tests: his from January 2025, hers from January 2026."""
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    close(connection, OWNER, (rows.START, END), END, ("migros", rows.LUKAS))
    close(connection, PARTNER, (date(2026, 1, 1), END), END, ("migros", rows.ANNA))
    yield connection
    connection.close()


def test_without_a_range_it_is_the_last_twelve_months(conn: sqlite3.Connection) -> None:
    shown = report.run(conn, owner=OWNER, currency="CHF")
    assert (shown.savings.first, shown.savings.last) == (date(2025, 9, 1), END)
    assert shown.income.periods[0].name == "2025-09"
    assert shown.spending.periods[-1].name == "2026-08"


def test_one_end_given_is_a_range(conn: sqlite3.Connection) -> None:
    shown = report.run(conn, owner=OWNER, last=date(2025, 3, 31), currency="CHF")
    assert (shown.savings.first, shown.savings.last) == (date(2025, 1, 1), date(2025, 3, 31))


def test_shortcuts_name_the_years_of_the_statements(conn: sqlite3.Connection) -> None:
    expected = (
        Shortcut("Last 12 months", "", ""),
        Shortcut("2026", "2026", "2026"),
        Shortcut("2025", "2025", "2025"),
        Shortcut("All", "2025", ""),
    )
    assert report.run(conn, owner=OWNER, currency="CHF").shortcuts == expected
    hers = report.run(conn, owner=PARTNER, currency="CHF").shortcuts
    assert [shortcut.label for shortcut in hers] == ["Last 12 months", "2026", "All"]


def test_nothing_committed_has_no_shortcuts() -> None:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    shown = report.run(connection, currency="CHF")
    assert (shown.shortcuts, shown.savings.totals) == ((), None)
    with pytest.raises(query.QueryError):
        report.run(connection, owner="Nobody", currency="CHF")
