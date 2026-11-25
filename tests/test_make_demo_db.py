"""`make demo`: two years of invented history, imported and committed as real statements are."""

import io
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scrooge import networth, overview
from scrooge.storage import db
from tests import make_demo_db, make_dev_db

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def nothing_real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test here can see the user's data (as in test_make_dev_db), nor needs Tesseract."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(make_demo_db, "SETUPS", tmp_path / "no setups")
    monkeypatch.setattr(make_demo_db, "can_read_scans", lambda: False)


def build(folder: Path, tmp_path: Path) -> tuple[make_dev_db.Built, str]:
    out = io.StringIO()
    real = (tmp_path / "real",)
    built = make_demo_db.build(folder, out, now=NOW, real=real, config=tmp_path / "none.yaml")
    return built, out.getvalue()


def test_two_years_for_both_people_all_committed(tmp_path: Path) -> None:
    built, said = build(tmp_path / "demo", tmp_path)
    with db.reading(built.database) as conn:
        shown = overview.run(conn, currency="EUR")
        files = conn.execute(
            "SELECT min(period_start), max(period_end), count(*), count(committed_at) "
            "FROM statement_files"
        ).fetchone()
        currencies = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT i.currency FROM positions p "
                "JOIN instruments i ON i.id = p.instrument_id"
            )
        }
        uncategorized = conn.execute("SELECT 1 FROM transactions WHERE category_id IS NULL")
        assert not uncategorized.fetchall()
    assert files[:2] == ("2024-10-01", "2026-09-30")
    assert files[2] == files[3]
    assert {"EUR", "USD"} <= currencies
    assert {part.name for part in shown.people} == {"Lukas", "Anna"}
    assert {part.name for part in shown.assets} == {
        networth.CASH,
        networth.PENSION,
        networth.ETFS,
        networth.STOCKS,
    }
    assert f"net worth {shown.total:,.2f} EUR on {shown.day}" in said


def test_the_same_on_every_run(tmp_path: Path) -> None:
    _, first = build(tmp_path / "demo", tmp_path)
    _, again = build(tmp_path / "demo", tmp_path)
    assert first == again


def test_a_folder_it_did_not_make_is_left_alone(tmp_path: Path) -> None:
    folder = tmp_path / "mine"
    folder.mkdir()
    (folder / "notes.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(make_dev_db.RefusedError, match="not made by make demo"):
        build(folder, tmp_path)
    assert (folder / "notes.txt").read_text(encoding="utf-8") == "keep"
