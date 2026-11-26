from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from scrooge import categories
from scrooge.models import Kind
from scrooge.storage import db, store
from scrooge.web import app as webapp
from tests.storage.helpers import OWNER, PARTNER, txn
from tests.web.builders import BUILT, SITE, close

SPAN = (date(2025, 7, 1), date(2026, 8, 31))  # fourteen months, the last one over
SHOP = "KONSUM-1 ZUERICH"
USD = {"account": "IBKR USD", "currency": "USD", "source_file": "ibkr.csv"}


def month(n: int, day: int) -> date:
    """The `day` of the `n`th month of SPAN."""
    return date(2025 + (6 + n) // 12, (6 + n) % 12 + 1, day)


LUKAS = [
    *(txn("-100", date=month(n, 5), source_ref=f"f{n}", counterparty=SHOP) for n in range(14)),
    *(
        txn(
            "6000",
            date=month(n, 25),
            source_ref=f"p{n}",
            kind=Kind.INCOME,
            category=categories.SALARY,
        )
        for n in range(14)
    ),
    txn("-30", date=date(2026, 8, 9), source_ref="1", counterparty="Coop-3 Bern"),
]
LUKAS_USD = [
    txn(
        "-10",
        date=date(2026, 3, 2),
        source_ref="1",
        kind=Kind.COMMISSION,
        category=categories.FEES,
        **USD,
    ),
    txn(
        "-10",
        date=date(2026, 3, 2),
        source_ref="2",
        kind=Kind.TRANSFER,
        category=categories.OWN_TRANSFER,
        **USD,
    ),
]
ANNA = [txn("-60", date=date(2026, 8, 12), source_ref="1", counterparty=SHOP)]


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    path: Path = tmp_path_factory.mktemp("transactions-context") / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    close(conn, OWNER, SPAN, ("migros", LUKAS), ("ibkr", LUKAS_USD))
    close(conn, PARTNER, (date(2026, 8, 1), SPAN[1]), ("migros", ANNA))
    store.set_rate(conn, SPAN[0], "USD", Decimal("0.80"), source="fetched:yahoo")
    store.set_rate(conn, SPAN[0], "EUR", Decimal("0.95"), source="fetched:yahoo")
    conn.close()
    return TestClient(webapp.create_app(path), base_url=SITE)


def asked(client: TestClient, **params: str) -> dict[str, object]:
    response = client.get("/api/transactions/context", params={"currency": "CHF", **params})
    assert response.status_code == 200, response.text
    sent: dict[str, object] = response.json()
    return sent


def test_a_category_is_its_last_two_years_its_usual_month_and_its_merchants(
    client: TestClient,
) -> None:
    sent = asked(client, owner=OWNER, category=categories.GROUP_FOOD)
    months = sent.pop("months")
    assert isinstance(months, list)
    assert len(months) == 24
    assert months[0] == {"month": "2024-09", "amount": "0.00"}
    assert months[10] == {"month": "2025-07", "amount": "100.00"}
    assert months[-1] == {"month": "2026-08", "amount": "130.00"}
    assert sent == {
        "kind": "category",
        "name": "Food",
        "currency": "CHF",
        "earned": False,
        "net": False,
        "usual": "100.00",
        "this_month": "130.00",
        "through": "2026-08-31",
        "partial": False,
        "merchants": [
            {"name": "KONSUM ZUERICH", "amount": "1400.00", "count": 14},
            {"name": "Coop Bern", "amount": "30.00", "count": 1},
        ],
        "balance": None,
        "errors": [],
    }


def test_an_income_category_is_money_earned(client: TestClient) -> None:
    sent = asked(client, owner=OWNER, category=categories.SALARY)
    assert (sent["earned"], sent["usual"], sent["this_month"]) == (True, "6000.00", "6000.00")


def test_a_merchant_is_named_as_the_cash_flow_names_it(client: TestClient) -> None:
    sent = asked(client, owner=OWNER, merchant="Konsum Zuerich", text="ignored")
    assert (sent["kind"], sent["name"], sent["this_month"]) == (
        "merchant",
        "KONSUM ZUERICH",
        "100.00",
    )
    assert (sent["merchants"], sent["balance"]) == (None, None)
    # Both people together: Anna's too, and no usual month before twelve of hers.
    both = asked(client, merchant="Konsum Zuerich")
    assert (both["this_month"], both["usual"]) == ("160.00", None)


def test_an_account_is_its_net_flow_and_what_it_holds(client: TestClient) -> None:
    sent = asked(client, owner=OWNER, account="IBKR USD")
    months = sent["months"]
    assert isinstance(months, list)
    # 10 dollars moved out at 0.80; the fee is what the money cost, not money taken out.
    assert months[18] == {"month": "2026-03", "amount": "-8.00"}
    assert (sent["net"], sent["earned"]) == (True, False)
    assert (sent["balance"], sent["merchants"], sent["usual"]) == ("-16.00", None, "0.00")
    # The salary less the shops: money put aside every month.
    shop = asked(client, owner=OWNER, account="Migros")
    assert (shop["usual"], shop["this_month"]) == ("5900.00", "5870.00")


def test_a_missing_rate_leaves_the_figures_out_and_says_why(client: TestClient) -> None:
    sent = asked(client, owner=OWNER, account="IBKR USD", currency="GBP")
    assert (sent["name"], sent["months"], sent["usual"], sent["balance"]) == (
        "IBKR USD",
        [],
        None,
        None,
    )
    errors = sent["errors"]
    assert isinstance(errors, list)
    assert errors
    assert all("GBP" in error for error in errors)


@pytest.mark.parametrize(
    ("params", "error"),
    [
        ({}, "Ask for exactly one of category, merchant and account."),
        (
            {"category": categories.GROUP_HOME, "merchant": "Coop"},
            "Ask for exactly one of category, merchant and account.",
        ),
        ({"category": "Alimentar"}, "No category named 'Alimentar'."),
        ({"account": "IBKR USD", "owner": PARTNER}, "No account named 'IBKR USD' of Anna."),
        ({"merchant": "Coop", "owner": "Nobody"}, "No owner named 'Nobody'."),
        ({"merchant": "Coop", "currency": "XYZ"}, "'XYZ' is not one of EUR, CHF, USD, GBP, JPY."),
    ],
)
def test_what_cannot_be_read_is_a_400_with_a_sentence(
    client: TestClient, params: dict[str, str], error: str
) -> None:
    response = client.get("/api/transactions/context", params={"currency": "CHF", **params})
    assert (response.status_code, response.json()) == (400, {"errors": [error]})


def test_the_transactions_can_be_cut_to_a_merchant(client: TestClient) -> None:
    sent = client.get("/api/transactions", params={"merchant": "coop bern"}).json()
    assert sent["count"] == 1
    assert sent["rows"][0]["counterparty"] == "Coop-3 Bern"


def test_a_merchant_named_after_its_account_lists_only_what_was_spent(client: TestClient) -> None:
    # The salary names nobody, so it is Migros's, but nothing was spent there.
    salary = client.get("/api/transactions", params={"merchant": "migros", "owner": OWNER})
    assert (salary.json()["count"], salary.json()["totals"]) == (0, [])
    # A fee is the account's, as on the cash flow.
    fee = client.get("/api/transactions", params={"merchant": "ibkr usd"}).json()
    assert fee["totals"] == [{"currency": "USD", "count": 1, "total": "-10"}]


def test_transaction_rows_send_canonical_merchant_and_category_context(
    client: TestClient,
) -> None:
    rows = client.get("/api/transactions", params={"owner": OWNER}).json()["rows"]
    coop = next(r for r in rows if r["counterparty"] == "Coop-3 Bern")
    assert (coop["merchant"], coop["category_context"]) == ("Coop Bern", True)

    fee = next(r for r in rows if r["kind"] == "commission")
    assert (fee["merchant"], fee["category_context"]) == ("IBKR USD", True)

    salary = next(r for r in rows if r["kind"] == "income")
    assert (salary["merchant"], salary["category_context"]) == (None, True)

    transfer = next(r for r in rows if r["kind"] == "transfer")
    assert (transfer["merchant"], transfer["category_context"]) == (None, False)


def test_transaction_row_context_excludes_inter_person_household_and_investment_principal(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    lukas_migros = [
        txn("-50", date=date(2026, 8, 5), source_ref="1", counterparty="ANNA MEIER"),
        txn(
            "-100",
            date=date(2026, 8, 8),
            source_ref="4",
            kind=Kind.INTERNAL,
            category=None,
        ),
        txn(
            "-20",
            date=date(2026, 8, 9),
            source_ref="5",
            kind=Kind.EXPENSE,
            category=categories.OWN_TRANSFER,
        ),
    ]
    lukas_ibkr = [
        txn(
            "-500",
            date=date(2026, 8, 6),
            source_ref="2",
            kind=Kind.SECURITY_TRADE,
            category=categories.INVESTMENTS,
            **USD,
        ),
        txn(
            "40",
            date=date(2026, 8, 7),
            source_ref="3",
            kind=Kind.TRADING,
            category=categories.TRADING,
            **USD,
        ),
    ]
    close(
        conn,
        OWNER,
        (date(2026, 8, 1), date(2026, 8, 31)),
        ("migros", lukas_migros),
        ("ibkr", lukas_ibkr),
    )
    close(
        conn,
        PARTNER,
        (date(2026, 8, 1), date(2026, 8, 31)),
        ("migros", [txn("50", date=date(2026, 8, 5), source_ref="1", counterparty="LUKAS MEIER")]),
    )
    conn.close()
    app_client = TestClient(webapp.create_app(path), base_url=SITE)

    # Household view drops inter-person spending from merchant context; single-owner view keeps it.
    household_rows = app_client.get("/api/transactions").json()["rows"]
    to_partner_household = next(
        r for r in household_rows if r["owner"] == OWNER and r["counterparty"] == "ANNA MEIER"
    )
    assert to_partner_household["merchant"] is None
    lukas_only = app_client.get("/api/transactions", params={"owner": OWNER}).json()["rows"]
    to_partner_single = next(r for r in lukas_only if r["counterparty"] == "ANNA MEIER")
    assert to_partner_single["merchant"] == "ANNA MEIER"

    # Investment principal, internal, and transfer-side rows have no category context; Trading does.
    by_kind = {r["kind"]: r for r in lukas_only if r["counterparty"] != "ANNA MEIER"}
    assert by_kind["security_trade"]["category_context"] is False
    assert by_kind["trading"]["category_context"] is True
    assert by_kind["internal"]["category_context"] is False
    assert by_kind["expense"]["category_context"] is False  # categorized as own_transfer
