"""`make dev-db`: the development database, built from the fixtures for two invented people."""

import io
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from scrooge import dbcli, networth, overview
from scrooge.extractors import ocr, swisscard
from scrooge.storage import db
from tests import make_dev_db
from tests.conftest import FIXTURES

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
# The certificates under the names the build gives them, and the text Tesseract makes of them.
SCANS = {name: folder for names in make_dev_db.SCANS.values() for name, folder in names.items()}


@pytest.fixture(autouse=True)
def nothing_real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No test here can see the user's data: `~` is an empty home inside tmp_path, and the
    repository's own config and setups (where the real database may be named) are not read."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(make_dev_db, "SETUPS", tmp_path / "no setups")
    defaults = make_dev_db.build.__kwdefaults__  # `config` is a default fixed at definition
    assert defaults is not None
    monkeypatch.setitem(defaults, "config", tmp_path / "no config.yaml")
    return home


@pytest.fixture
def scans(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tesseract stands in by the text stored next to each scan."""

    def stored_text(path: Path) -> str:
        return (FIXTURES / SCANS[path.name] / "certificate.txt").read_text(encoding="utf-8")

    monkeypatch.setattr(make_dev_db, "can_read_scans", lambda: True)
    monkeypatch.setattr(ocr, "read_text", stored_text)


def build(folder: Path, tmp_path: Path) -> tuple[make_dev_db.Built, str]:
    out = io.StringIO()
    real = (tmp_path / "real",)
    built = make_dev_db.build(folder, out, now=NOW, real=real, config=tmp_path / "none.yaml")
    return built, out.getvalue()


@pytest.mark.usefixtures("scans")
def test_both_people_every_kind_of_asset_and_a_chart(tmp_path: Path) -> None:
    built, said = build(tmp_path / "dev", tmp_path)
    assert built.database == (tmp_path / "dev" / "scrooge.db").resolve()
    assert built.certificates
    assert "  Lukas: 6 statements committed\n" in said
    assert "  Anna: 6 statements committed\n" in said
    with db.reading(built.database) as conn:
        shown = overview.run(conn, currency="CHF")
        staged = "SELECT 1 FROM statement_files WHERE committed_at IS NULL"
        assert not conn.execute(staged).fetchall()
    assert shown.total > Decimal(0)
    assert shown.day is not None
    assert len(shown.worth.days) > 30  # the chart has a history to draw
    assert {part.name for part in shown.people} == {"Lukas", "Anna"}
    assert {part.name for part in shown.assets} >= {
        networth.CASH,
        networth.BILLS,
        networth.PILLAR2,
        networth.PENSION,
        networth.ETFS,
        networth.STOCKS,
    }
    assert all(holding.value > 0 for holding in shown.top)
    assert f"net worth {shown.total:,.2f} CHF on {shown.day}" in said


@pytest.mark.usefixtures("scans")
def test_the_config_next_to_it_serves_and_queries_it(tmp_path: Path) -> None:
    built, _ = build(tmp_path / "dev", tmp_path)
    assert built.serve() == (
        f"uv run scrooge serve --config {built.config} --rules {built.rules} "
        f"--instruments {built.instruments}"
    )
    out = io.StringIO()
    assert dbcli.main(["statements", "--config", str(built.config)], out) == dbcli.OK
    assert "committed  2026-09-01 to 2026-09-30  Migros Bank account statement" in out.getvalue()
    out = io.StringIO()
    argv = ["query", "--owner", "Anna", "--summary", "--config", str(built.config)]
    assert dbcli.main(argv, out) == dbcli.OK
    assert "transactions\n" in out.getvalue()


@pytest.mark.usefixtures("scans")
def test_its_rules_are_one_file_from_the_fixtures_and_the_starter(tmp_path: Path) -> None:
    # The repository's own rules.yaml and categories.yaml are the user's: never read here.
    built, _ = build(tmp_path / "dev", tmp_path)
    assert built.rules == tmp_path / "dev" / "rules.yaml"
    assert not (tmp_path / "dev" / "rules.local.yaml").exists()
    rules = built.rules.read_text(encoding="utf-8")
    starter = Path(__file__).parents[1] / "src" / "scrooge" / "starter"
    assert "VELOSHARE" in rules  # an invented shop of the fixtures
    generic = starter.joinpath("rules.yaml").read_text(encoding="utf-8").partition("rules:\n")[2]
    assert generic.rstrip("\n") in rules
    categories = (tmp_path / "dev" / "categories.yaml").read_text(encoding="utf-8")
    assert categories == starter.joinpath("categories.yaml").read_text(encoding="utf-8")


def test_without_tesseract_the_certificates_are_left_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(make_dev_db, "can_read_scans", lambda: False)
    built, said = build(tmp_path / "dev", tmp_path)
    assert not built.certificates
    assert said.startswith("No `tesseract` on PATH: the pension certificates are left out")
    assert "  Lukas: 4 statements committed\n" in said
    with db.reading(built.database) as conn:
        shown = overview.run(conn, currency="CHF")
    assert networth.PILLAR2 not in {part.name for part in shown.assets}
    assert {part.name for part in shown.people} == {"Lukas", "Anna"}


@pytest.mark.usefixtures("scans")
def test_it_is_built_again_from_nothing(tmp_path: Path) -> None:
    folder = tmp_path / "dev"
    build(folder, tmp_path)
    (folder / "left over.txt").write_text("from a session in the browser", encoding="utf-8")
    built, said = build(folder, tmp_path)
    assert not (folder / "left over.txt").exists()
    assert "  Lukas: 6 statements committed\n" in said
    assert built.database.is_file()


def test_a_folder_it_did_not_make_is_never_emptied(tmp_path: Path) -> None:
    folder = tmp_path / "data"
    folder.mkdir()
    database = folder / "scrooge.db"
    database.write_bytes(b"a real database")
    with pytest.raises(make_dev_db.RefusedError, match="not made by make dev-db"):
        build(folder, tmp_path)
    assert database.read_bytes() == b"a real database"
    assert sorted(p.name for p in folder.iterdir()) == ["scrooge.db"]

    a_file = tmp_path / "scrooge.db"
    a_file.write_bytes(b"a file")
    with pytest.raises(make_dev_db.RefusedError, match="not made by make dev-db"):
        build(a_file, tmp_path)
    assert a_file.read_bytes() == b"a file"


@pytest.mark.parametrize("where", ["real", "real/dev", "."])
def test_the_real_data_is_never_touched(tmp_path: Path, where: str) -> None:
    """Neither the real folder, nor one inside it, nor one it is inside of."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "scrooge.db").write_bytes(b"the real one")
    with pytest.raises(make_dev_db.RefusedError, match="the real data is kept at"):
        build(tmp_path / where, tmp_path)
    assert sorted(p.name for p in real.iterdir()) == ["scrooge.db"]
    assert (real / "scrooge.db").read_bytes() == b"the real one"


def test_the_database_the_real_config_names_is_never_touched(tmp_path: Path) -> None:
    config = tmp_path / "scrooge.yaml"
    config.write_text(f"database: {tmp_path / 'nas' / 'scrooge.db'}\n", encoding="utf-8")
    with pytest.raises(make_dev_db.RefusedError, match="the real data is kept at"):
        make_dev_db.build(tmp_path / "nas", io.StringIO(), now=NOW, real=(), config=config)
    assert not (tmp_path / "nas").exists()


def test_the_setups_in_users_and_the_databases_they_name_are_never_touched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setups = tmp_path / "users"
    (setups / "anna").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere" / "scrooge.db"
    (setups / "anna" / "scrooge.yaml").write_text(f"database: {elsewhere}\n", encoding="utf-8")
    monkeypatch.setattr(make_dev_db, "SETUPS", setups)
    none = tmp_path / "none.yaml"
    with pytest.raises(make_dev_db.RefusedError, match="the real data is kept at"):
        make_dev_db.build(elsewhere.parent, io.StringIO(), now=NOW, real=(), config=none)
    assert make_dev_db.ROOT / "users" in make_dev_db.REAL


def test_the_real_database_s_default_folder_is_refused(tmp_path: Path, nothing_real: Path) -> None:
    # `~` is the empty home of `nothing_real`: this is a stand-in for the user's real folder.
    assert Path("~").expanduser() == nothing_real
    real = nothing_real / ".local" / "share" / "scrooge_db"
    real.mkdir(parents=True)
    (real / "scrooge.db").write_bytes(b"the real one")
    out = io.StringIO()
    assert make_dev_db.main([str(real)], out) == 1
    assert out.getvalue().startswith(f"error: {real.resolve()}: the real data is kept at")
    assert make_dev_db.REAL[0] == Path("~/.local/share/scrooge_db")
    # Nothing was emptied, written or made there or anywhere in the stand-in home.
    assert sorted(p.name for p in real.iterdir()) == ["scrooge.db"]
    assert (real / "scrooge.db").read_bytes() == b"the real one"
    assert sorted(p.relative_to(nothing_real).as_posix() for p in nothing_real.rglob("*")) == [
        ".local",
        ".local/share",
        ".local/share/scrooge_db",
        ".local/share/scrooge_db/scrooge.db",
    ]


def test_the_card_invoice_s_pdf_reads_back_as_its_text(tmp_path: Path) -> None:
    text = (FIXTURES / "swisscard" / "statement.txt").read_text(encoding="utf-8")
    pdf = tmp_path / "swisscard.pdf"
    pdf.write_bytes(make_dev_db.text_pdf(text.splitlines()))
    assert swisscard.read_text(pdf) == text.rstrip("\n")
    assert not ocr.is_scan(pdf)
