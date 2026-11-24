import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from scrooge import categories
from scrooge.models import Kind
from scrooge.storage import db, store
from scrooge.web import app as webapp
from tests import test_savings as year
from tests.storage import helpers
from tests.storage.helpers import OWNER, PARTNER, txn
from tests.web.builders import BUILT, close
from tests.web.test_app import text

SALARY = {"kind": Kind.INCOME, "category": categories.SALARY}
SHARES = {"kind": Kind.SECURITY_TRADE, "category": categories.INVESTMENTS}
GIFT = "Anna Meier"  # the other person: recognised whatever the case
SITE = "http://127.0.0.1:8765"
EUR = {"account": "Revolut EUR", "currency": "EUR", "source_file": "revolut.tsv"}
LUKAS = [
    txn("-45.20", date=date(2026, 7, 3), source_ref="1"),
    txn("-1850", date=date(2026, 7, 20), source_ref="2", category=categories.RENT),
    txn("6000", date=date(2026, 7, 25), source_ref="3", **SALARY),
    txn("-500", date=date(2026, 8, 10), source_ref="4", **SHARES),
    txn("6100", date=date(2026, 8, 25), source_ref="5", **SALARY),
    txn(
        "-200", date=date(2026, 8, 26), source_ref="6", category=categories.GIFTS, counterparty=GIFT
    ),
]
LUKAS_EUR = [
    txn("-30", date=date(2026, 8, 5), source_ref="1", category=categories.EATING_OUT, **EUR)
]
ANNA = [
    txn("-60", date=date(2026, 8, 12), source_ref="1"),
    txn(
        "200",
        date=date(2026, 8, 26),
        source_ref="2",
        kind=Kind.INCOME,
        category=categories.REFUNDS,
        counterparty="LUKAS MEIER",
    ),
]


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    """July and August 2026, committed for both."""
    path: Path = tmp_path_factory.mktemp("analysis-cashflow") / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    span = (date(2026, 7, 1), date(2026, 8, 31))
    close(conn, OWNER, span, ("migros", LUKAS), ("revolut", LUKAS_EUR))
    close(conn, PARTNER, span, ("migros", ANNA))
    store.set_rate(conn, date(2026, 7, 1), "EUR", Decimal("0.95"), source="fetched:yahoo")
    conn.close()
    return TestClient(webapp.create_app(path), base_url="http://127.0.0.1:8765")


def tables(html: str) -> list[list[list[str]]]:
    """The rows of every table body and foot on the page, in order, as their cells' text."""
    found = []
    for body in re.findall(r"<t(?:body|foot)>(.*?)</t(?:body|foot)>", html, re.DOTALL):
        cell = r"<t[dh][^>]*>(.*?)</t[dh]>"
        rows = [re.findall(cell, row, re.DOTALL) for row in body.split("</tr>")]
        found.append([[text(each) for each in row] for row in rows if row])
    return found


def flow(page: str) -> str:
    """The folded cash flow of the analysis: the part of the page after its summary."""
    return page.partition("Income and expenses in each currency")[2]


def get(client: TestClient, **params: str) -> str:
    return client.get("/analysis", params={"currency": "CHF", **params}).text


def test_each_currency_has_its_periods_newest_first_and_their_sum(client: TestClient) -> None:
    response = client.get("/analysis", params={"currency": "CHF"})
    assert response.status_code == 200
    page = flow(response.text)
    assert re.findall(r"<h3>(.*?)</h3>", page) == ["CHF", "EUR"]
    chf, chf_sum, income, expenses, investing, eur, eur_sum, eur_expenses = tables(page)
    assert chf == [
        # Without what they paid each other.
        ["2026-08", "6,100.00", "-60.00", "", "6,040.00", "-500.00"],
        ["2026-07", "6,000.00", "-1,895.20", "", "4,104.80", "0.00"],
    ]
    assert chf_sum == [["All 2", "12,100.00", "-1,955.20", "", "10,144.80", "-500.00"]]
    assert income == [["Salary", "12,100.00", "2"]]
    assert expenses == [["Home", "-1,850.00", "1"], ["Food", "-105.20", "2"]]
    assert investing == [["Investments", "-500.00", "1"]]
    assert eur == [["2026-08", "0.00", "-30.00", "", "-30.00", "0.00"]]
    assert eur_sum == [["All 1", "0.00", "-30.00", "", "-30.00", "0.00"]]
    assert eur_expenses == [["Eating out", "-30.00", "1"]]
    assert "what the two of you paid each other" in text(page)


def test_the_bars_are_drawn_to_the_largest_amount_of_the_currency(client: TestClient) -> None:
    page = flow(get(client))
    bars = re.findall(r'<meter class="(\w+)" min="0" max="([\d.]+)" value="([\d.]+)"', page)
    assert bars == [
        ("income", "6100", "6100"),
        ("expenses", "6100", "60"),
        ("income", "6100", "6000"),
        ("expenses", "6100", "1895.2"),
        ("income", "30", "0"),
        ("expenses", "30", "30"),
    ]
    assert 'title="Expenses -1,895.20"' in page
    assert "style=" not in get(client)  # the page's own policy would refuse it


def test_one_person_sees_what_they_paid_the_other(client: TestClient) -> None:
    his, _, _, expenses, *_ = tables(flow(get(client, owner="Lukas")))
    assert his[0] == ["2026-08", "6,100.00", "-200.00", "", "5,900.00", "-500.00"]
    assert ["Gifts", "-200.00", "1"] in expenses
    hers = flow(get(client, owner="Anna"))
    assert tables(hers)[0] == [["2026-08", "200.00", "-60.00", "", "140.00", "0.00"]]
    assert "what the two of you paid each other" not in text(hers)


def test_periods_and_categories_link_to_their_transactions(client: TestClient) -> None:
    page = flow(get(client, owner="Lukas", **{"from": "2026-08"}))
    page = page.replace(f"{SITE}/transactions?from=", "?").replace("&amp;", "&")
    assert '<a href="?2026-08-01&to=2026-08-31&owner=Lukas">2026-08</a>' in page
    # A category is listed over the range shown, which the page spells out.
    assert '<a href="?2026-08-01&to=2026-08-31&owner=Lukas&category=Gifts">Gifts</a>' in page
    followed = client.get("/transactions?from=2026-08-01&to=2026-08-31&owner=Lukas&category=Gifts")
    assert "1 transaction · -200.00 CHF" in text(followed.text)


def test_the_range_and_the_grouping_come_from_the_form(client: TestClient) -> None:
    year = get(client, by="year", to="2026-07")
    assert tables(flow(year))[0] == [["2026", "6,000.00", "-1,895.20", "", "4,104.80", "0.00"]]
    weeks = get(client, by="week", owner="Anna")
    assert [row[0] for row in tables(flow(weeks))[0]] == ["2026-W35", "2026-W33"]
    quarter = get(client, by="quarter")
    assert "transactions?from=2026-07-01&amp;to=2026-09-30" in flow(quarter)


def test_without_a_range_it_is_the_twelve_months_the_rest_of_the_page_shows(
    client: TestClient,
) -> None:
    page = get(client)
    assert "From 2025-09-01 to 2026-08-31" in text(page)
    assert tables(flow(get(client, **{"from": "2026-08"})))[0][0][0] == "2026-08"
    assert len(tables(flow(get(client, **{"from": "2026-08"})))[0]) == 1


def test_an_empty_range_has_no_cash_flow(client: TestClient) -> None:
    page = get(client, **{"from": "2030"})
    assert "Income and expenses in each currency" not in page
    assert "Nothing is committed in this range." in page


def test_the_cash_flow_page_is_gone(client: TestClient) -> None:
    assert client.get("/cashflow").status_code == 404
    assert "Cash flow" not in client.get("/").text


def test_what_was_saved_is_sent_as_a_part_of_income_and_of_salary(client: TestClient) -> None:
    def report(**params: str) -> dict[str, Any]:
        sent: dict[str, Any] = client.get("/api/cashflow", params=params).json()["report"]
        return sent

    his = report(owner=OWNER, currency="CHF", **{"from": "2026-07"})
    # All he earned was salary: both rates are the same.
    assert (his["totals"]["work"], his["totals"]["rate"], his["totals"]["rate_salary"]) == (
        "12100.00",
        "82.400",
        "82.400",
    )
    assert his["periods"][-1]["rate_salary"] == "96.300"
    # Two months of history: no twelve months to measure either rate over.
    assert (his["periods"][-1]["rate_12"], his["periods"][-1]["rate_salary_12"]) == (None, None)
    # She was paid no salary: there is nothing to measure what she kept against.
    hers = report(owner=PARTNER, currency="CHF", **{"from": "2026-07"})["totals"]
    assert (hers["work"], hers["rate"], hers["rate_salary"]) == ("0.00", None, None)


@pytest.fixture(scope="module")
def years(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    """The twenty months of the savings tests: enough for the twelve before July 2026."""
    path: Path = tmp_path_factory.mktemp("analysis-years") / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, helpers.CLOSED_AT)
    helpers.close(conn, OWNER, (year.START, year.END), year.END, ("migros", year.LUKAS))
    helpers.close(conn, PARTNER, (date(2026, 1, 1), year.END), year.END, ("migros", year.ANNA))
    conn.close()
    return TestClient(webapp.create_app(path), base_url=SITE)


def test_the_twelve_months_are_measured_against_income_and_salary(years: TestClient) -> None:
    params = {"owner": OWNER, "currency": "CHF"}
    sent = years.get("/api/cashflow", params={**params, "from": "2026-07", "to": "2026-07"})
    july = sent.json()["report"]["periods"][0]
    # August 2025 to July 2026: 39400 kept of the 60300 that came in, and of 60000 of salary.
    assert (july["name"], july["rate_12"], july["rate_salary_12"]) == (
        "2026-07",
        "65.300",
        "65.700",
    )
    month = years.get("/api/month", params={**params, "month": "2026-07"}).json()["month"]
    assert (month["rate_year"], month["rate_salary_year"]) == ("65.300", "65.700")


def test_the_household_is_sent_who_paid(client: TestClient) -> None:
    # What the two paid each other stays in the household; his 30 EUR count at 0.95.
    params = {"from": "2026-07", "currency": "CHF"}
    sent: dict[str, Any] = client.get("/api/cashflow", params=params).json()
    paid = sent["report"]["paid_by"]
    assert paid["spent"] == sent["report"]["totals"]["spent"] == "1983.70"
    assert paid["people"] == [
        {
            "name": "Anna",
            "spent": "60.00",
            "share": "3.000",
            "settled": "-140.00",
            "settled_share": "-7.100",
        },
        {
            "name": "Lukas",
            "spent": "1923.70",
            "share": "97.000",
            "settled": "2123.70",
            "settled_share": "107.100",
        },
    ]
    # His gift to her, as his statements have it.
    assert paid["moves"] == [{"sender": "Lukas", "receiver": "Anna", "amount": "200.00"}]
    assert paid["categories"][0] == {
        "name": "Home",
        "spent": "1850.00",
        "by": {"Anna": "0.00", "Lukas": "1850.00"},
    }
    alone = client.get("/api/cashflow", params={**params, "owner": OWNER}).json()
    assert alone["report"]["paid_by"] is None
