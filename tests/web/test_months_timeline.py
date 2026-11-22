"""The months page leads with a timeline: each account's statements along the months."""

import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from scrooge import staging
from scrooge.storage import db
from scrooge.web import app as webapp
from tests.storage.helpers import OWNER, PARTNER, certificate, certified, txn
from tests.web.builders import (
    ANNA_SEPTEMBER,
    AUGUST_DAYS,
    BUILT,
    LUKAS_AUGUST,
    LUKAS_SEPTEMBER,
    SEPTEMBER_DAYS,
    close,
    stage_rows,
)

SITE = "http://127.0.0.1:8765"
TODAY = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """Lukas: his bank committed for August and staged for September, another account
    committed for June only; Anna: her bank staged for September."""
    path = tmp_path / "f.db"
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    close(conn, OWNER, AUGUST_DAYS, ("migros", LUKAS_AUGUST))
    june = (date(2026, 6, 1), date(2026, 6, 30))
    row = txn("-9", source_ref="9", account="Revolut CHF", date=june[0])
    close(conn, OWNER, june, ("revolut", [row]))
    stage_rows(conn, OWNER, SEPTEMBER_DAYS, ("migros", LUKAS_SEPTEMBER), opening="4954.80")
    stage_rows(conn, PARTNER, SEPTEMBER_DAYS, ("migros", ANNA_SEPTEMBER))
    conn.close()
    return TestClient(webapp.create_app(path, lambda: TODAY), base_url=SITE)


def rects(html: str) -> list[tuple[str, str, str]]:
    """The class, x and width of every run drawn."""
    found = re.findall(
        r'<rect class="(committed|staged)" x="([\d.]+)" y="3" width="([\d.]+)"', html
    )
    return [(kind, x, width) for kind, x, width in found]


def test_each_account_is_a_row_under_its_person_with_its_runs_of_days(client: TestClient) -> None:
    page = client.get("/months").text
    assert re.findall(r"<h3>(\w+)</h3>", page) == ["Anna", "Lukas"]
    labels = re.findall(r'aria-label="([^"]*: (?:committed|staged)[^"]*)"', page)
    assert labels == [
        "Anna, Migros: staged 2026-09-01 to 2026-09-30",
        "Lukas, Migros: staged 2026-09-01 to 2026-09-30; committed 2026-08-01 to 2026-08-31",
        "Lukas, Revolut CHF: committed 2026-06-01 to 2026-06-30",
    ]
    assert [kind for kind, _, _ in rects(page)] == [
        "staged",
        "staged",
        "committed",
        "committed",
    ]


def test_the_axis_runs_from_the_first_statement_to_the_end_of_this_month(
    client: TestClient,
) -> None:
    page = client.get("/months").text
    # 2026-06-01 to 2026-11-30 is 183 days; June is the first tick, at the left edge.
    names = re.findall(r'<text class="month" x="[\d.]+" y="16">(\w+)</text>', page)
    assert names[:6] == ["Jun", "Jul", "Aug", "Sep", "Oct", "Nov"]
    assert 'viewBox="0 0 1000 22"' in page
    assert 'preserveAspectRatio="none"' in page


def test_an_account_far_behind_is_marked_on_its_row_and_in_the_tiles(client: TestClient) -> None:
    page = client.get("/months", params={"owner": "Lukas"}).text
    assert page.count('<div class="row behind">') == 1
    assert "Revolut CHF <small>" in page.partition('<div class="row behind">')[2]
    assert "1 behind: a statement is probably missing" in page
    assert re.findall(r"<h3>(\w+)</h3>", page) == ["Lukas"]


def test_the_tiles_say_how_far_it_is_committed_and_what_waits(client: TestClient) -> None:
    page = client.get("/months").text
    assert re.search(r"<h2>Committed up to</h2>\s*<p class=\"value\">2026-08-31</p>", page)
    assert re.search(r"<h2>Accounts</h2>\s*<p class=\"value\">3</p>", page)
    assert re.search(r"<h2>Staged</h2>\s*<p class=\"value\">2</p>", page)
    assert "waiting to be <a" in page


def test_the_tables_stay_under_the_timeline_folded(client: TestClient) -> None:
    page = client.get("/months").text
    assert page.index("<svg") < page.index("<details>") < page.index("<table")
    assert "Statements, month by month" in page


def test_nothing_staged_says_so_and_a_copy_without_statements_has_no_timeline(
    tmp_path: Path,
) -> None:
    path = tmp_path / "f.db"
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    close(conn, OWNER, AUGUST_DAYS, ("migros", LUKAS_AUGUST))
    conn.close()
    page = TestClient(webapp.create_app(path, lambda: TODAY), base_url=SITE).get("/months").text
    assert "nothing waits" in page
    assert "none behind" in page
    empty = tmp_path / "empty.db"
    conn = db.connect(empty)
    db.migrate(conn, BUILT)
    conn.close()
    nothing = TestClient(webapp.create_app(empty, lambda: TODAY), base_url=SITE).get("/months").text
    assert "Statements, month by month" not in nothing
    assert "<details>" not in nothing


def test_a_certificate_after_the_statements_makes_no_account_behind(tmp_path: Path) -> None:
    path = tmp_path / "f.db"
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    close(conn, OWNER, AUGUST_DAYS, ("migros", LUKAS_AUGUST))
    staging.stage(conn, certified(certificate(date(2026, 10, 15), "36000")), BUILT)
    staging.commit(conn, owner=OWNER, now=BUILT)
    conn.close()
    page = TestClient(webapp.create_app(path, lambda: TODAY), base_url=SITE).get("/months").text
    assert "none behind" in page
    assert re.search(r"<h2>Committed up to</h2>\s*<p class=\"value\">2026-08-31</p>", page)
    assert "Pillar 2 AXA" in page  # the timeline still shows the day of the certificate
    assert "committed 2026-10-15 to 2026-10-15" in page
