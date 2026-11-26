"""What each account of `/api/statements` holds by part, and its balance month by month."""

from decimal import Decimal
from pathlib import Path

from starlette.testclient import TestClient

from scrooge import networth, staging
from scrooge.models import CashBalance
from scrooge.storage import db
from scrooge.web import app as webapp
from tests.storage.helpers import OCTOBER, OWNER, PARTNER, batch, statement
from tests.test_holds import two_months
from tests.test_networth import NOW, account, close, day, rate
from tests.web.builders import SITE


def test_each_account_says_what_it_holds_and_what_it_held_at_each_month_end(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    two_months(conn)
    conn.close()
    client = TestClient(webapp.create_app(path, currency="CHF"), base_url=SITE)
    sent = client.get("/api/statements").json()
    accounts = {(a["owner"], a["account"]): a for a in sent["accounts"]}
    # The broker's dollars and euros are one account, as its statements are.
    broker = accounts[OWNER, "IBKR"]
    assert (broker["balance"], broker["parts"], sent["errors"]) == (
        "5880.00",
        ["IBKR EUR", "IBKR USD"],
        [],
    )
    assert broker["holds"] == {
        "cash": [
            {"asset": "Cash", "currency": "USD", "amount": "4500.00", "converted": "3600.00"},
            {"asset": "Cash", "currency": "EUR", "amount": "2000.00", "converted": "1800.00"},
        ],
        "securities": "480.00",
        "months": [
            {"day": "2026-07-31", "balance": "5800.00"},
            {"day": "2026-08-31", "balance": "5800.00"},
            {"day": "2026-09-30", "balance": "5880.00"},
        ],
    }
    assert accounts[OWNER, "Migros"]["parts"] == ["Migros"]
    assert (accounts[OWNER, "Migros"]["typable"], accounts[OWNER, "Migros"]["typed"]) == (
        "none",
        [],
    )
    assert (broker["typable"], broker["typed"]) == ("none", [])
    bank = accounts[OWNER, "Migros"]["holds"]
    assert (bank["cash"], bank["securities"]) == (
        [{"asset": "Cash", "currency": "CHF", "amount": "700.00", "converted": "700.00"}],
        None,
    )
    card = accounts[OWNER, "Swisscard"]["holds"]["cash"]
    assert [(c["asset"], c["amount"]) for c in card] == [("Card", "-150.00")]
    assert accounts[PARTNER, "Migros"]["balance"] == "300.00"
    missing = client.get("/api/statements", params={"currency": "JPY"}).json()
    assert missing["errors"]
    assert missing["total"] is None
    assert [a["holds"] for a in missing["accounts"]] == [None] * len(missing["accounts"])


def test_an_account_kept_in_a_currency_without_rates_says_so(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    two_months(conn)
    close(conn, account("revolut", "Revolut GBP", "GBP", "100"), owner=PARTNER)
    conn.close()
    client = TestClient(webapp.create_app(path, currency="CHF"), base_url=SITE)
    sent = client.get("/api/statements").json()
    assert sent["errors"] == ["No exchange rate is stored for GBP."]
    gbp = next(a for a in sent["accounts"] if a["account"] == "Revolut GBP")
    assert (gbp["balance"], gbp["holds"], gbp["committed"]) == (None, None, "2026-09-30")


def test_accounts_total_matches_overview_net_worth_to_the_cent(tmp_path: Path) -> None:
    """Overview and Accounts must agree to the cent, rounding once from the exact sum."""
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    # 1112 CHF at 0.94 CHF/EUR is 1182.978723... EUR -> 1182.98 EUR rounded once.
    # Rounding per (owner, asset) line gives 1182.97; rounding per account gives 1182.99.
    close(
        conn,
        account("migros", "Migros", "CHF", "1004"),
        account("revolut", "Revolut CHF", "CHF", "4"),
        account("swisscard", "Swisscard", "CHF", "-4"),
    )
    close(
        conn,
        account("migros", "Migros", "CHF", "104"),
        account("revolut", "Revolut CHF", "CHF", "4"),
        owner=PARTNER,
    )
    rate(conn, "EUR", day(1), "0.94")
    conn.close()
    frozen = (networth.FrozenAccount("Migros", OWNER),)
    client = TestClient(webapp.create_app(path, currency="EUR", frozen=frozen), base_url=SITE)
    overview_total = client.get("/api/overview").json()["overview"]["total"]
    statements = client.get("/api/statements").json()
    assert overview_total == statements["total"] == "1182.98"
    for cut in ("asset", "owner", "liquidity", "total"):
        series = client.get("/api/networth", params={"by": cut}).json()["series"]
        assert series["total"][-1] == "1182.98"
    months = client.get("/api/networth/months", params={"currency": "EUR"}).json()["months"]
    assert months[0]["values"]["EUR"]["total"] == "1182.98"
    # Single-owner views also match between Overview and Accounts.
    for person in (OWNER, PARTNER):
        ov = client.get("/api/overview", params={"owner": person}).json()["overview"]["total"]
        st = client.get("/api/statements", params={"owner": person}).json()["total"]
        nw = client.get("/api/networth", params={"owner": person}).json()["series"]["total"][-1]
        assert ov == st == nw


def test_broker_currencies_and_staged_only_accounts_round_total_once(tmp_path: Path) -> None:
    """A broker's currency accounts combine unrounded before rounding, and an uncommitted
    account does not hide or distort the total of the committed ones."""
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    # 4 CHF + 4 USD (= 4 CHF) at 0.94 CHF/EUR: each sub-account is 4.2553... (4.26),
    # combined unrounded is 8.5106... -> 8.51.
    broker = statement(
        "ibkr",
        (),
        [
            CashBalance("IBKR CHF", "CHF", Decimal(4), Decimal(4)),
            CashBalance("IBKR USD", "USD", Decimal(4), Decimal(4)),
        ],
        filename="IBKR 2026-09.csv",
    )
    close(conn, broker)
    staging.stage(
        conn, batch(account("migros", "Migros", "CHF", "100", month=OCTOBER), month=OCTOBER), NOW
    )
    rate(conn, "USD", day(1), "1")
    rate(conn, "EUR", day(1), "0.94")
    conn.close()
    client = TestClient(webapp.create_app(path, currency="EUR"), base_url=SITE)
    sent = client.get("/api/statements").json()
    by_name = {a["account"]: a for a in sent["accounts"]}
    assert by_name["IBKR"]["balance"] == "8.51"
    assert by_name["Migros"]["balance"] is None
    assert sent["total"] == "8.51"
    assert client.get("/api/overview").json()["overview"]["total"] == "8.51"
