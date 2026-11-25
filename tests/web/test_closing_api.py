"""Closing an account and opening it again, through `/api/accounts/close` and `.../reopen`."""

from datetime import date
from decimal import Decimal

import pytest
from httpx2 import Response

from scrooge import months
from scrooge.models import CashBalance
from scrooge.storage import db
from tests.storage.helpers import AUGUST, OCTOBER, OWNER, SEPTEMBER, statement
from tests.test_networth import account, close, rate, spent
from tests.web.builders import Site

D = Decimal
END = "2026-09-30"


def _bank(site: Site, opening: str, *amounts: str) -> None:
    """Migros, committed for September: it opens with `opening` and books `amounts` on the 10th."""
    rows = [spent(amount, date(2026, 9, 10)) for amount in amounts]
    conn = db.connect(site.database)
    close(conn, account("migros", "Migros", "CHF", opening, *rows))
    conn.close()


def _broker(site: Site, chf: str, usd: str) -> None:
    """ACME, an invented broker, lists cash in francs and dollars in one statement."""
    balances = [
        CashBalance("ACME CHF", "CHF", D(chf), D(chf)),
        CashBalance("ACME USD", "USD", D(usd), D(usd)),
    ]
    made = statement("ibkr", (), balances, filename="ACME 2026-09.csv", month=SEPTEMBER)
    conn = db.connect(site.database)
    rate(conn, "USD", date(2026, 9, 1), "0.9")
    close(conn, made)
    conn.close()


def _closed(site: Site) -> list[tuple[str, date | None]]:
    conn = db.connect(site.database)
    try:
        return [(line.account, line.closed) for line in months.coverage(conn, OWNER)]
    finally:
        conn.close()


def _post(site: Site, what: str, **sent: object) -> Response:
    return site.client.post(f"/api/accounts/{what}", json=sent, headers={"X-Token": site.token})


def test_an_account_that_held_nothing_is_closed_and_opened_again(site: Site) -> None:
    _bank(site, "100", "-100")
    sent = {"owner": OWNER, "account": "Migros"}
    closed = _post(site, "close", **sent, day=END)
    assert (closed.status_code, closed.json()) == (200, {**sent, "errors": []})
    assert _closed(site) == [("Migros", date(2026, 9, 30))]
    assert site.client.get("/api/statements").json()["accounts"][0]["closed"] == END
    assert _post(site, "reopen", **sent).json() == {**sent, "errors": []}
    assert _closed(site) == [("Migros", None)]


def test_an_account_that_held_money_is_refused_with_the_reason(site: Site) -> None:
    _bank(site, "100", "-1")
    refused = _post(site, "close", owner=OWNER, account="Migros", day=END)
    assert refused.status_code == 409
    assert refused.json() == {
        "errors": [
            "Migros of Lukas held 99.00 CHF at the end of 2026-09-30: "
            "close it on a day it held nothing."
        ]
    }
    assert _closed(site) == [("Migros", None)]
    again = _post(site, "reopen", owner=OWNER, account="Migros")
    assert (again.status_code, again.json()["errors"]) == (409, ["Migros of Lukas is not closed."])
    unknown = _post(site, "close", owner=OWNER, account="Nowhere", day=END)
    assert unknown.json()["errors"] == ["Lukas has no account 'Nowhere'."]


def test_an_account_that_is_behind_is_closed_all_the_same(site: Site) -> None:
    conn = db.connect(site.database)
    rows = [spent("-100", date(2026, 8, 10))]
    close(conn, account("migros", "Migros", "CHF", "100", *rows, month=AUGUST), month=AUGUST)
    later = account("revolut", "Revolut", "CHF", "5", month=OCTOBER)
    close(conn, later, month=OCTOBER)  # two months on: Migros is behind
    conn.close()
    shown = {
        a["account"]: a["behind"] for a in site.client.get("/api/statements").json()["accounts"]
    }
    assert shown == {"Migros": True, "Revolut": False}
    assert _post(site, "close", owner=OWNER, account="Migros", day="2026-08-31").status_code == 200
    shown = {
        a["account"]: a["behind"] for a in site.client.get("/api/statements").json()["accounts"]
    }
    assert shown == {"Migros": False, "Revolut": False}


def test_a_broker_is_closed_in_every_currency_or_in_none(site: Site) -> None:
    _broker(site, "0", "5")
    refused = _post(site, "close", owner=OWNER, account="ACME", day=END)
    assert "ACME USD of Lukas held" in refused.json()["errors"][0]
    assert _closed(site) == [("ACME CHF", None), ("ACME USD", None)]


def test_a_broker_is_closed_and_opened_again_in_every_currency(site: Site) -> None:
    _broker(site, "0", "0")
    assert _post(site, "close", owner=OWNER, account="ACME", day=END).status_code == 200
    closed = date(2026, 9, 30)
    assert _closed(site) == [("ACME CHF", closed), ("ACME USD", closed)]
    assert site.client.get("/api/statements").json()["accounts"][0]["closed"] == END
    assert _post(site, "reopen", owner=OWNER, account="ACME").status_code == 200
    assert _closed(site) == [("ACME CHF", None), ("ACME USD", None)]
    # one currency closed alone: the broker opens again whole all the same
    assert _post(site, "close", owner=OWNER, account="ACME USD", day=END).status_code == 200
    assert _post(site, "reopen", owner=OWNER, account="ACME").status_code == 200
    assert _closed(site) == [("ACME CHF", None), ("ACME USD", None)]


@pytest.mark.parametrize(
    "sent",
    [
        {"owner": OWNER, "account": "Migros"},
        {"owner": OWNER, "account": "Migros", "day": "30/09/2026"},
        {"owner": OWNER, "account": "Migros", "day": 5},
        {"owner": 1, "account": "Migros", "day": END},
        {"account": "Migros", "day": END},
    ],
)
def test_a_close_without_an_owner_an_account_and_a_day_is_refused(
    site: Site, sent: dict[str, object]
) -> None:
    refused = _post(site, "close", **sent)
    assert refused.status_code == 400
    assert "Send the owner, the account and the day" in refused.json()["errors"][0]


def test_a_reopen_without_an_owner_and_an_account_is_refused(site: Site) -> None:
    assert _post(site, "reopen", owner=OWNER).status_code == 400
    headers = {"X-Token": site.token}
    assert (
        site.client.post("/api/accounts/reopen", content="[1]", headers=headers).status_code == 400
    )
    assert site.client.post("/api/accounts/close", content="{", headers=headers).status_code == 400


def test_only_these_pages_may_change_an_account(site: Site) -> None:
    _bank(site, "100", "-100")
    sent = {"owner": OWNER, "account": "Migros", "day": END}
    copy = site.app(read_only=True)
    for what in ("close", "reopen"):
        path = f"/api/accounts/{what}"
        assert site.client.post(path, json=sent).status_code == 403
        assert site.client.post(path, json=sent, headers={"X-Token": "other"}).status_code == 403
        foreign = {"X-Token": site.token, "Origin": "https://elsewhere.example"}
        assert site.client.post(path, json=sent, headers=foreign).status_code == 403
        assert site.client.get(path).status_code == 405
        assert copy.post(path, json=sent, headers={"X-Token": site.token}).status_code == 403
    assert _closed(site) == [("Migros", None)]
