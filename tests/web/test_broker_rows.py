"""A broker's accounts in each currency are one account of `/api/statements`, judged as one."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from starlette.testclient import TestClient

from scrooge.models import CashBalance
from scrooge.pipeline import Statement
from scrooge.storage import db
from scrooge.web import app as webapp
from tests.storage.helpers import AUGUST, OCTOBER, SEPTEMBER, statement
from tests.test_networth import NOW, account, close, rate
from tests.web.builders import SITE

D = Decimal


def broker(month: date, *codes: str) -> Statement:
    """A statement of ACME, an invented broker, that lists cash in `codes`: 1000 of each."""
    balances = [CashBalance(f"ACME {code}", code, D(1000), D(1000)) for code in codes]
    return statement("ibkr", (), balances, filename=f"ACME {month:%Y-%m}.csv", month=month)


def test_a_broker_is_one_account_and_its_statements_are_listed_once(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    # ACME keeps francs, euros and dollars, and stops listing the euros after August; the
    # bank's last statement is of August too. Apart, the euros would be behind as the bank is.
    bank = account("migros", "Migros", "CHF", "500", month=AUGUST)
    close(conn, broker(AUGUST, "CHF", "EUR", "USD"), bank, month=AUGUST)
    close(conn, broker(SEPTEMBER, "CHF", "USD"))
    close(conn, broker(OCTOBER, "CHF", "USD"), month=OCTOBER)
    rate(conn, "EUR", date(2026, 8, 1), "0.9")
    rate(conn, "USD", date(2026, 8, 1), "0.8")
    conn.close()
    client = TestClient(webapp.create_app(path, currency="CHF"), base_url=SITE)
    sent = client.get("/api/statements").json()
    assert sent["errors"] == []
    # What the lead counts: two accounts, the bank behind, all in up to the bank's last day.
    shown = [(a["account"], a["parts"], a["committed"], a["behind"]) for a in sent["accounts"]]
    assert shown == [
        ("ACME", ["ACME CHF", "ACME EUR", "ACME USD"], "2026-10-31", False),
        ("Migros", ["Migros"], "2026-08-31", True),
    ]
    assert sent["covered"] == "2026-08-31"
    acme = sent["accounts"][0]
    assert acme["balance"] == "2700.00"
    assert [(c["currency"], c["converted"]) for c in acme["holds"]["cash"]] == [
        ("CHF", "1000.00"),
        ("EUR", "900.00"),
        ("USD", "800.00"),
    ]
    assert [s["filename"] for s in acme["statements"]] == [
        "ACME 2026-10.csv",
        "ACME 2026-09.csv",
        "ACME 2026-08.csv",
    ]
    assert acme["spans"] == [{"first": "2026-08-01", "last": "2026-10-31", "committed": True}]
