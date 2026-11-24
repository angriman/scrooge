"""What each account of `/api/statements` holds by part, and its balance month by month."""

from pathlib import Path

from starlette.testclient import TestClient

from scrooge.storage import db
from scrooge.web import app as webapp
from tests.storage.helpers import OWNER
from tests.test_holds import two_months
from tests.test_networth import NOW
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
    broker = accounts[OWNER, "IBKR USD"]
    assert (broker["balance"], sent["errors"]) == ("4080.00", [])
    assert broker["holds"] == {
        "cash": [{"currency": "USD", "amount": "4500.00", "converted": "3600.00"}],
        "securities": "480.00",
        "months": [
            {"day": "2026-07-31", "balance": "4000.00"},
            {"day": "2026-08-31", "balance": "4000.00"},
            {"day": "2026-09-30", "balance": "4080.00"},
        ],
    }
    bank = accounts[OWNER, "Migros"]["holds"]
    assert (bank["cash"], bank["securities"]) == (
        [{"currency": "CHF", "amount": "700.00", "converted": "700.00"}],
        None,
    )
    missing = client.get("/api/statements", params={"currency": "JPY"}).json()
    assert missing["errors"]
    assert [a["holds"] for a in missing["accounts"]] == [None] * len(missing["accounts"])
