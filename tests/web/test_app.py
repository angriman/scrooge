import csv
import io
import re
import struct
from datetime import date
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from finanze.models import Kind
from finanze.storage import db
from finanze.web import app as webapp
from tests.storage.helpers import OWNER, PARTNER, txn
from tests.web.builders import BUILT, close, stage_rows

WEB = Path(webapp.__file__).parent
LUKAS = [
    txn("-45.20", date=date(2026, 7, 3), source_ref="1"),
    txn("6000", date=date(2026, 8, 25), source_ref="2", kind=Kind.INCOME, category="Stipendio"),
    txn("-12", date=date(2026, 9, 10), source_ref="3"),
    # Bought on the last day of July, booked in August, at a shop with a name to escape.
    txn(
        "-80",
        date=date(2026, 7, 31),
        booking_date=date(2026, 8, 2),
        source_ref="4",
        category="Ristoranti",
        counterparty="<b>Bäckerei</b>",
        description="CARD 1234",
    ),
]
BAKERY = "&lt;b&gt;Bäckerei&lt;/b&gt; CARD 1234"  # as the page must write it
ANNA = [txn("-60", date=date(2026, 7, 12), source_ref="1")]


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Lukas with July and August committed and September staged; Anna with July committed."""
    path = tmp_path_factory.mktemp("web") / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    july = date(2026, 7, 1)
    close(conn, OWNER, (july, date(2026, 8, 31)), ("migros", [LUKAS[0], LUKAS[1], LUKAS[3]]))
    stage_rows(
        conn,
        OWNER,
        (date(2026, 9, 1), date(2026, 9, 30)),
        ("migros", [LUKAS[2]]),
        opening="5874.80",
    )
    close(conn, PARTNER, (july, date(2026, 7, 31)), ("migros", ANNA))
    conn.close()
    return path


@pytest.fixture
def client(database: Path) -> TestClient:
    return TestClient(webapp.create_app(database), base_url="http://127.0.0.1:8765")


def rows(html: str) -> list[list[str]]:
    """The cells of every row of the page's last table, without their tags."""
    body = html.rpartition("<tbody>")[2]
    found = []
    for row in re.findall(r"<tr>(.*?)</tr>", body, re.DOTALL):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        found.append([text(cell) for cell in cells])
    return found


def text(html: str) -> str:
    """What a reader sees: no tags, single spaces."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def test_the_months_page_says_how_far_each_account_is_committed(client: TestClient) -> None:
    from tests.web.test_cashflow_page import tables  # noqa: PLC0415  # it imports this module

    response = client.get("/months")
    assert response.status_code == 200
    coverage, people, listed = tables(response.text)
    assert coverage == [
        ["Anna", "Migros", "Migros Bank account statement", "2026-07-31", ""],
        ["Lukas", "Migros", "Migros Bank account statement", "2026-08-31", "staged to 2026-09-30"],
    ]
    assert people == [
        ["Anna", "2026-07-01 to 2026-07-31 (1 statement)", "1", "0"],
        ["Lukas", "2026-07-01 to 2026-08-31 (1 statement)", "3", "1"],
    ]
    assert [row[:2] for row in listed] == [
        ["2026-08", "Lukas"],
        ["2026-07", "Anna"],
        ["2026-07", "Lukas"],
    ]
    assert "Only committed statements are shown" in response.text
    only = tables(client.get("/months", params={"owner": "Anna"}).text)[1]
    assert [row[0] for row in only] == ["Anna"]


def test_the_view_is_everyone_or_one_person(client: TestClient) -> None:
    everyone = client.get("/", params={"currency": "CHF"})
    assert everyone.status_code == 200
    said = text(everyone.text)
    assert "CHF net worth on 2026-08-31" in said
    assert "Statements up to 2026-07-31: what was held then is valued at the latest" in said
    assert '<a href="http://127.0.0.1:8765/?currency=CHF" aria-current="true">Everyone</a>' in (
        everyone.text
    )
    response = client.get("/", params={"owner": "Anna"})
    page = response.text.replace("http://127.0.0.1:8765", "")
    assert '<a href="/?owner=Anna" aria-current="true">Anna</a>' in page
    assert '<a href="/">Everyone</a>' in page
    assert '<a href="/?owner=Anna" aria-current="page">Overview</a>' in page
    assert '<a href="/transactions?owner=Anna">Transactions</a>' in page


def test_a_person_the_database_does_not_have_is_not_found(client: TestClient) -> None:
    response = client.get("/", params={"owner": "<b>Zoe"})
    assert response.status_code == 404
    assert response.text == "no owner named '<b>Zoe'\n"
    assert response.headers["content-type"].startswith("text/plain")


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil.example", "192.168.1.20"])
def test_a_request_under_another_host_name_is_refused(client: TestClient, host: str) -> None:
    assert client.get("/", headers={"host": host}).status_code == 400


def test_localhost_is_this_machine_too(client: TestClient) -> None:
    assert client.get("/", headers={"host": "localhost:8765"}).status_code == 200


@pytest.mark.parametrize("path", ["/", "/static/app.css", "/nothing-here"])
def test_every_response_forbids_loading_from_elsewhere(client: TestClient, path: str) -> None:
    headers = client.get(path).headers
    assert headers["content-security-policy"] == "default-src 'self'; frame-ancestors 'none'"
    assert headers["x-content-type-options"] == "nosniff"


def test_the_stylesheet_is_served_from_the_package(client: TestClient) -> None:
    response = client.get("/static/app.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


def test_no_page_or_asset_names_another_site() -> None:
    files = [*WEB.glob("templates/*"), *(f for f in WEB.glob("static/*") if f.is_file())]
    assert files
    for file in files:
        assert not re.search(r"https?:|//\w", file.read_text(encoding="utf-8")), file.name


def test_every_page_names_its_icon(client: TestClient) -> None:
    page = client.get("/").text
    icons = "http://127.0.0.1:8765/static/icons"
    assert f'<link rel="icon" href="{icons}/icon-32.png" sizes="32x32" type="image/png">' in page
    assert f'<link rel="icon" href="{icons}/icon.svg" type="image/svg+xml">' in page
    assert f'<link rel="apple-touch-icon" href="{icons}/apple-touch-icon.png">' in page
    svg = client.get("/static/icons/icon.svg")
    assert svg.headers["content-type"] == "image/svg+xml"
    for name in ["icon-32.png", "apple-touch-icon.png"]:
        assert client.get(f"/static/icons/{name}").headers["content-type"] == "image/png"


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def test_the_icons_are_drawn_here_and_name_no_other_site() -> None:
    # One drawing, the net worth chart's bands under its line; `make icons` draws the PNGs.
    folder = WEB / "static" / "icons"
    assert sorted(f.name for f in folder.iterdir()) == [
        "apple-touch-icon.png",
        "icon-32.png",
        "icon.svg",
    ]
    drawing = (folder / "icon.svg").read_text(encoding="utf-8")
    # The namespace is a name, not an address anything is fetched from.
    drawing = drawing.replace('xmlns="http://www.w3.org/2000/svg"', "", 1)
    assert not re.search(r"https?:|//\w|href|<image|<script|<style|url\(", drawing)
    assert png_size(folder / "icon-32.png") == (32, 32)
    assert png_size(folder / "apple-touch-icon.png") == (180, 180)


def test_the_vendored_chart_library_is_whole_and_asks_nothing_of_the_network() -> None:
    # uPlot 1.6.32 (MIT), copied in: the page loads it from this machine (D18).
    script = (WEB / "static" / "vendor" / "uPlot.iife.min.js").read_text(encoding="utf-8")
    banner, _, code = script.partition("\n")
    assert banner == "/*! https://github.com/leeoniya/uPlot (v1.6.32) */"
    assert not re.search(r"https?:|fetch\(|XMLHttpRequest|WebSocket|import\(", code)
    style = (WEB / "static" / "vendor" / "uPlot.min.css").read_text(encoding="utf-8")
    assert not re.search(r"url\(|@import|https?:", style)
    assert "MIT License" in (WEB / "static" / "vendor" / "uPlot.LICENSE").read_text("utf-8")


def test_transactions_are_listed_newest_first_with_their_totals(client: TestClient) -> None:
    response = client.get("/transactions")
    assert response.status_code == 200
    assert rows(response.text) == [
        ["2026-08-25", "Lukas", "Migros file", "Stipendio income", "KONSUM", "6,000.00 CHF"],
        [
            "2026-07-31 booked 2026-08-02",
            "Lukas",
            "Migros file",
            "Ristoranti expense",
            BAKERY,
            "-80.00 CHF",
        ],
        ["2026-07-12", "Anna", "Migros file", "Spesa expense", "KONSUM", "-60.00 CHF"],
        ["2026-07-03", "Lukas", "Migros file", "Spesa expense", "KONSUM", "-45.20 CHF"],
    ]
    assert "4 transactions · 5,814.80 CHF · CSV" in text(response.text)
    assert "By month 2026-07 -185.20 CHF 3 2026-08 6,000.00 CHF 1" in text(response.text)
    assert "By year" not in response.text
    assert '<a href="http://127.0.0.1:8765/transactions" aria-current="page">' in response.text


def test_the_filter_narrows_the_list_and_stays_in_the_form(client: TestClient) -> None:
    asked = {"category": "Ristoranti e bar", "from": "2026-08", "booking": "1", "owner": "Lukas"}
    response = client.get("/transactions", params=asked)
    assert rows(response.text) == [
        ["2026-08-02 real 2026-07-31", "Migros file", "Ristoranti expense", BAKERY, "-80.00 CHF"]
    ]
    assert "1 transaction · -80.00 CHF" in text(response.text)
    assert "By month" not in response.text
    assert "<option selected>Ristoranti e bar</option>" in response.text
    assert 'name="from" value="2026-08"' in response.text
    assert 'name="booking" value="1" checked' in response.text
    assert '<input type="hidden" name="owner" value="Lukas">' in response.text

    by_kind = client.get("/transactions", params={"kind": "income", "min": "5000", "text": "kon"})
    assert [row[-1] for row in rows(by_kind.text)] == ["6,000.00 CHF"]
    assert '<option value="income" selected>income</option>' in by_kind.text


def test_a_field_that_cannot_be_read_is_said_and_the_rest_still_applies(
    client: TestClient,
) -> None:
    response = client.get("/transactions", params={"from": "May", "owner": "Anna"})
    assert response.status_code == 200
    assert "&#39;May&#39; is not a day, a month or a year" in response.text
    assert len(rows(response.text)) == 1


def test_an_account_the_person_does_not_have_gives_an_empty_list(client: TestClient) -> None:
    response = client.get("/transactions", params={"owner": "Anna", "account": "Tastytrade"})
    assert response.status_code == 200
    assert "No account named &#39;Tastytrade&#39; of Anna." in response.text
    assert "0 transactions" in text(response.text)
    assert "CSV" not in response.text
    assert client.get("/transactions", params={"owner": "Zoe"}).status_code == 404


def test_a_long_list_comes_in_pages(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webapp, "_PAGE", 3)
    first = client.get("/transactions", params={"owner": "Lukas"})
    assert len(rows(first.text)) == 3
    assert "Page 1 of 1" not in first.text
    everyone = client.get("/transactions")
    assert len(rows(everyone.text)) == 3
    assert "Page 1 of 2" in everyone.text
    assert 'href="http://127.0.0.1:8765/transactions?page=2" rel="next">Older</a>' in everyone.text
    assert "Newer" not in everyone.text
    for page in ("2", "99"):
        last = client.get("/transactions", params={"page": page})
        assert [row[0] for row in rows(last.text)] == ["2026-07-03"]
        assert "Page 2 of 2" in last.text
        assert "Older" not in last.text
        assert "Newer" in last.text
    for page in ("0", "-1", "two"):
        assert "Page 1 of 2" in client.get("/transactions", params={"page": page}).text


def test_the_result_can_be_saved_as_csv_with_exact_amounts(client: TestClient) -> None:
    response = client.get("/transactions.csv", params={"owner": "Lukas", "to": "2026-07"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="transactions.csv"'
    header, *lines = csv.reader(io.StringIO(response.text))
    assert header[:6] == ["date", "booking_date", "owner", "account", "amount", "currency"]
    assert [line[:6] for line in lines] == [
        ["2026-07-03", "2026-07-03", "Lukas", "Migros", "-45.2", "CHF"],
        ["2026-07-31", "2026-08-02", "Lukas", "Migros", "-80", "CHF"],
    ]
    named = dict(zip(header, lines[1], strict=True))
    assert named["counterparty"] == "<b>Bäckerei</b>"
    assert (named["kind"], named["category"], named["category_parent"]) == (
        "expense",
        "Ristoranti",
        "Ristoranti e bar",
    )


def test_the_csv_refuses_a_filter_it_cannot_read(client: TestClient) -> None:
    response = client.get("/transactions.csv", params={"from": "May"})
    assert response.status_code == 400
    assert response.text.startswith("'May' is not a day")
    assert client.get("/transactions.csv", params={"category": "Nope"}).status_code == 404
