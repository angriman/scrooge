"""A broker's accounts in each currency are one account of `/api/statements`, judged as one."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from starlette.testclient import TestClient

from scrooge.models import CashBalance
from scrooge.pipeline import Statement
from scrooge.storage import db
from scrooge.web import app as webapp
from tests.storage.helpers import AUGUST, OCTOBER, OWNER, SEPTEMBER, statement
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


def _built(path: Path, held: dict[date, tuple[str, ...]], banked: tuple[date, ...]) -> TestClient:
    """Lukas's statements: of ACME the currencies `held` lists by month, of the bank the months
    of `banked`; each month committed in turn."""
    conn = db.connect(path)
    db.migrate(conn, NOW)
    for month in sorted({*held, *banked}):
        bank = [account("migros", "Migros", "CHF", "500", month=month)] if month in banked else []
        acme = [broker(month, *held[month])] if month in held else []
        close(conn, *acme, *bank, month=month)
    rate(conn, "EUR", date(2026, 8, 1), "0.9")
    rate(conn, "USD", date(2026, 8, 1), "0.8")
    conn.close()
    return TestClient(webapp.create_app(path, currency="CHF"), base_url=SITE)


def test_a_currency_the_broker_stopped_listing_holds_nothing_back(tmp_path: Path) -> None:
    # The euros were all changed in August, and ACME lists them no more.
    later = ("CHF", "USD")
    held = {AUGUST: ("CHF", "EUR", "USD"), SEPTEMBER: later, OCTOBER: later}
    client = _built(tmp_path / "scrooge.db", held, (AUGUST, SEPTEMBER, OCTOBER))
    # The header's day, the notices and the month recapped all go on to October.
    people = client.get("/api/session").json()["people"]
    assert {p["name"]: p["covered"] for p in people}[OWNER] == "2026-10-31"
    assert client.get("/api/attention").json()["behind"] == []
    assert client.get("/api/month").json()["month"]["month"] == "2026-10"
    page = client.get("/months").text
    assert "ACME EUR" not in page
    assert "Lukas, ACME: committed 2026-08-01 to 2026-10-31" in page


def test_a_broker_behind_is_named_once_and_a_missing_month_is_a_gap(tmp_path: Path) -> None:
    # ACME's September is missing, and nothing of it came after October; the bank goes on.
    every = ("CHF", "EUR", "USD")
    banked = (AUGUST, SEPTEMBER, OCTOBER, date(2026, 11, 1), date(2026, 12, 1))
    client = _built(tmp_path / "scrooge.db", {AUGUST: every, OCTOBER: every}, banked)
    behind = client.get("/api/attention").json()["behind"]
    assert [(a["account"], a["committed"]) for a in behind] == [("ACME", "2026-10-31")]
    acme = client.get("/api/statements").json()["accounts"][0]
    assert (acme["account"], acme["behind"]) == ("ACME", True)
    assert acme["spans"] == [
        {"first": "2026-08-01", "last": "2026-08-31", "committed": True},
        {"first": "2026-10-01", "last": "2026-10-31", "committed": True},
    ]
    # The broker ends in October: Lukas's statements are all in up to then.
    people = client.get("/api/session").json()["people"]
    assert {p["name"]: p["covered"] for p in people}[OWNER] == "2026-10-31"
