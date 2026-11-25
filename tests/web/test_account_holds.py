"""What each account of `/api/statements` holds by part, and its balance month by month."""

from pathlib import Path

from starlette.testclient import TestClient

from scrooge.storage import db
from scrooge.web import app as webapp
from tests.storage.helpers import OWNER, PARTNER
from tests.test_holds import two_months
from tests.test_networth import NOW, account, close
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
