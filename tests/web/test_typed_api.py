"""Figures typed on the accounts page, through `/api/accounts/typed`: what an account held at
the end of a day where no statement says it, as `scrooge balance` and `scrooge position` take
them."""

import sqlite3
from datetime import date
from typing import Any

import pytest

from scrooge import staging
from scrooge.models import Kind
from scrooge.storage import db
from tests.storage.helpers import batch, certificate, certified, statement, txn
from tests.test_networth import NOW, rate
from tests.web.builders import Site


def _send(site: Site, **sent: object) -> tuple[int, dict[str, Any]]:
    response = site.client.post("/api/accounts/typed", json=sent, headers={"X-Token": site.token})
    found: dict[str, Any] = response.json()
    return response.status_code, found


def _rows(site: Site, sql: str) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(site.database)
    try:
        return [tuple(row) for row in conn.execute(sql)]
    finally:
        conn.close()


def test_a_balance_is_typed_and_removed(site: Site) -> None:
    where = {"owner": "Lukas", "account": "Kasse", "day": "2026-01-31"}
    sent = _send(site, **where, what="balance", amount="5000.10", currency="eur")
    assert sent == (200, {"symbol": None, "errors": []})
    held = (
        "SELECT a.name, a.currency, b.amount_nano FROM balances b "
        "JOIN accounts a ON a.id = b.account_id"
    )
    assert _rows(site, held) == [("Kasse", "EUR", 5_000_100_000_000)]
    assert _send(site, **where, what="balance", remove=True) == (
        200,
        {"symbol": None, "errors": []},
    )
    assert _rows(site, held) == []
    status, again = _send(site, **where, what="balance", remove=True)
    assert status == 409
    assert again["errors"] == ["Nothing was typed for Kasse of Lukas on 2026-01-31."]


def test_a_position_is_typed_under_the_symbol_the_instruments_know_and_removed(
    site: Site,
) -> None:
    where = {"owner": "Lukas", "account": "Shares", "day": "2026-02-27"}
    sent = _send(
        site, **where, what="position", symbol="widg", quantity="12", currency="usd", tab="ETFs"
    )
    assert sent == (200, {"symbol": "WIDG", "errors": []})
    held = (
        "SELECT i.symbol, i.currency, p.quantity FROM positions p "
        "JOIN instruments i ON i.id = p.instrument_id WHERE p.source = 'manual'"
    )
    assert _rows(site, held) == [("WIDG", "USD", "12")]
    gone = _send(site, **where, what="position", symbol="WIDG", remove=True)
    assert gone == (200, {"symbol": "WIDG", "errors": []})
    assert _rows(site, held) == []


@pytest.mark.parametrize(
    ("sent", "why"),
    [
        ({"owner": "Zoe"}, "Zoe is nobody the config names."),
        ({"account": " "}, "Say which account."),
        ({"day": "31.01.2026"}, "'31.01.2026' is not a day like 2026-10-31."),
        ({"day": "2026-10-13"}, "2026-10-13 is after today (2026-10-12): type what was held"),
        ({"amount": "1,5"}, "'1,5' is not an amount like 1234.56."),
        ({"currency": "euro"}, "'euro' is not a currency code like CHF or USD."),
    ],
)
def test_a_balance_that_cannot_be_typed_says_why(
    site: Site, sent: dict[str, str], why: str
) -> None:
    good = {"owner": "Lukas", "account": "Kasse", "day": "2026-01-31", "amount": "1"}
    status, answer = _send(site, **(good | {"currency": "CHF"} | sent), what="balance")
    assert status == 409
    assert answer["errors"][0].startswith(why)
    assert site.count("balances") == 0


@pytest.mark.parametrize(
    ("sent", "why"),
    [
        ({"quantity": "-1"}, "'-1' is no number of shares held: 0 or more."),
        ({"tab": "Bonds"}, "'Bonds' is no kind of instrument to type: Stocks or ETFs."),
    ],
)
def test_a_position_that_cannot_be_typed_says_why(
    site: Site, sent: dict[str, str], why: str
) -> None:
    good = {"owner": "Lukas", "account": "Shares", "day": "2026-01-31", "symbol": "ACME"}
    status, answer = _send(
        site, **(good | {"quantity": "1", "currency": "USD"} | sent), what="position"
    )
    assert (status, answer["errors"]) == (409, [why])


def test_the_session_says_whose_figures_can_be_typed(site: Site) -> None:
    assert site.client.get("/api/session").json()["typing"] == ["Lukas", "Anna"]
    assert site.app().get("/api/session").json()["typing"] == []


def test_a_figure_needs_json_the_token_and_the_import_page(site: Site) -> None:
    assert _send(site, owner="Lukas")[0] == 400
    sent = {"owner": "Lukas", "account": "Kasse", "day": "2026-01-31", "what": "balance"}
    assert site.client.post("/api/accounts/typed", json=sent).status_code == 403
    copy = site.app(imports=site.setup, read_only=True)
    headers = {"X-Token": site.token}
    assert copy.post("/api/accounts/typed", json=sent, headers=headers).status_code == 403
    bare = site.app()
    assert bare.post("/api/accounts/typed", json=sent, headers=headers).status_code == 404


def test_typed_only_accounts_appear_in_statements_and_disappear_when_removed(site: Site) -> None:
    assert (
        _send(
            site,
            owner="Lukas",
            account="Kasse",
            day="2026-01-31",
            what="balance",
            amount="1200.50",
            currency="CHF",
        )[0]
        == 200
    )
    assert (
        _send(
            site,
            owner="Lukas",
            account="Kasse",
            day="2026-02-28",
            what="position",
            symbol="ACME",
            quantity="5",
            currency="CHF",
            tab="Stocks",
        )[0]
        == 200
    )
    assert (
        _send(
            site,
            owner="Anna",
            account="Cash Box",
            day="2026-01-31",
            what="balance",
            amount="50.00",
            currency="CHF",
        )[0]
        == 200
    )

    # Also add a pension certificate (balance-only typable) and a Schwab export (both typable).
    conn = db.connect(site.database)
    rate(conn, "USD", date(2026, 1, 1), "0.9")
    staging.stage(conn, certified(certificate(date(2026, 1, 1), "10000", issuer="AXA")), now=NOW)
    schwab_row = txn(
        "10",
        date=date(2026, 2, 10),
        account="Schwab USD",
        currency="USD",
        description="Dividend",
        kind=Kind.DIVIDEND,
        source_file="schwab.csv",
    )
    staging.stage(
        conn,
        batch(
            statement("schwab", [schwab_row], filename="schwab.csv", month=date(2026, 2, 1)),
            month=date(2026, 2, 1),
        ),
        now=NOW,
    )
    staging.commit(conn, owner="Lukas", now=NOW)
    conn.close()

    assert (
        _send(
            site,
            owner="Lukas",
            account="Pillar 2 AXA",
            day="2026-02-28",
            what="balance",
            amount="10500",
            currency="CHF",
        )[0]
        == 200
    )
    assert (
        _send(
            site,
            owner="Lukas",
            account="Schwab USD",
            day="2026-01-31",
            what="balance",
            amount="100",
            currency="USD",
        )[0]
        == 200
    )

    sent = site.client.get("/api/statements", params={"owner": "Lukas"}).json()
    accounts = {a["account"]: a for a in sent["accounts"]}
    assert "Cash Box" not in accounts  # Anna's account excluded by owner filter

    kasse = accounts["Kasse"]
    assert kasse["source"] is None
    assert kasse["typable"] == "both"
    assert kasse["parts"] == ["Kasse"]
    assert kasse["statements"] == []
    assert kasse["spans"] == []
    assert kasse["typed"] == [
        {
            "account": "Kasse",
            "day": "2026-02-28",
            "what": "position",
            "currency": "CHF",
            "amount": None,
            "symbol": "ACME",
            "quantity": "5",
        },
        {
            "account": "Kasse",
            "day": "2026-01-31",
            "what": "balance",
            "currency": "CHF",
            "amount": "1200.5",
            "symbol": None,
            "quantity": None,
        },
    ]

    assert accounts["Pillar 2 AXA"]["typable"] == "balance"
    assert [f["day"] for f in accounts["Pillar 2 AXA"]["typed"]] == ["2026-02-28"]
    assert accounts["Schwab USD"]["typable"] == "both"
    assert [f["account"] for f in accounts["Schwab USD"]["typed"]] == ["Schwab USD"]

    # Removing every typed figure from Kasse drops the typed-only account from /api/statements.
    assert (
        _send(
            site,
            owner="Lukas",
            account="Kasse",
            day="2026-01-31",
            what="balance",
            remove=True,
        )[0]
        == 200
    )
    assert (
        _send(
            site,
            owner="Lukas",
            account="Kasse",
            day="2026-02-28",
            what="position",
            symbol="ACME",
            remove=True,
        )[0]
        == 200
    )
    after = site.client.get("/api/statements", params={"owner": "Lukas"}).json()
    assert "Kasse" not in {a["account"] for a in after["accounts"]}
