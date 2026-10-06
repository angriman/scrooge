import io
from datetime import UTC, datetime
from pathlib import Path

import pytest

from finanze import cli, dbcli
from finanze.storage import db
from tests.conftest import INSTRUMENTS_YAML, write_rules

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
CONFIG = """database: {database}
owner: Lukas
owners:
  Lukas: {{match: ['LUKAS MEIER']}}
  Anna: {{match: ['ANNA MEIER']}}
"""


class Run:
    """Runs a database command on the fixture folder and records what it printed."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.database = folder / "data" / "finanze.db"
        self.config = folder / "finanze.yaml"
        self.config.write_text(CONFIG.format(database=self.database), encoding="utf-8")
        self.rules = write_rules(folder)
        self.instruments = folder / "instruments.yaml"
        self.instruments.write_text(INSTRUMENTS_YAML, encoding="utf-8")
        self.output = ""

    def __call__(self, command: str, *args: str) -> int:
        argv = [command, *args, "--config", str(self.config)]
        out = io.StringIO()
        code = dbcli.main(argv, out, now=lambda: NOW)
        self.output = out.getvalue()
        return code

    def load(self, *args: str) -> int:
        paths = ["--rules", str(self.rules), "--instruments", str(self.instruments)]
        return self("import", str(self.folder), *paths, *args)

    def count(self, table: str) -> int:
        conn = db.connect(self.database)
        try:
            return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])  # noqa: S608  # table names are literals in this file
        finally:
            conn.close()


@pytest.fixture
def run(folder: Path) -> Run:
    return Run(folder)


def test_import_stages_the_month_and_says_what_is_next(run: Run) -> None:
    assert run.load() == dbcli.OK
    assert "September 2026 for Lukas: 6 statements reconciled" in run.output
    assert "skipped ibkr.pdf (not a statement)" in run.output
    assert "note: Revolut EUR includes rows with no balance to check against" in run.output
    assert run.output.endswith("Next: finanze commit 2026-09\n")
    assert run.count("transactions") > 0
    assert run.count("committed_transactions") == 0


def test_commit_saves_a_copy_first_and_reopen_undoes_it(run: Run) -> None:
    run.load()
    staged = run.count("transactions")
    assert run("commit", "2026-09") == dbcli.OK
    copy = run.database.parent / "backups" / "finanze-20261006T120000.db"
    assert f"September 2026 committed for Lukas. Copy from before: {copy}" in run.output
    assert copy.exists()
    assert run.count("committed_transactions") == staged

    copy.unlink()
    assert run("commit", "2026-09") == dbcli.OK
    assert "already committed" in run.output
    assert not copy.exists()

    assert run.load() == dbcli.FAILED
    assert "error: 2026-09 is committed for Lukas: reopen it first" in run.output
    assert run("reopen", "2026-09") == dbcli.OK
    assert run.output == "September 2026 is open again for Lukas.\n"
    assert run.count("committed_transactions") == 0
    assert run.load() == dbcli.OK


def test_uncategorized_rows_are_reported_and_block_the_commit(run: Run) -> None:
    run.rules.write_text("rules: []\n", encoding="utf-8")
    assert run.load() == dbcli.NEEDS_REVIEW
    assert "transactions need a category: add rules" in run.output
    assert run("commit", "2026-09") == dbcli.FAILED
    assert "still need a category" in run.output
    assert not list((run.database.parent / "backups").iterdir())


def test_a_month_that_is_not_staged_cannot_be_committed(run: Run) -> None:
    assert run("commit", "2026-08") == dbcli.FAILED
    assert run.output == "error: nothing is staged for Lukas in 2026-08\n"


def test_a_reconciliation_failure_stores_nothing(run: Run) -> None:
    statement = run.folder / "Migros.csv"
    text = statement.read_text(encoding="utf-8")
    statement.write_text(text.replace("-1288.18", "-1288.19"), encoding="utf-8")
    assert run.load() == dbcli.FAILED
    assert run.output.startswith("error: ")
    assert run.count("transactions") == 0


def test_owner_and_database_can_come_from_the_command_line(run: Run, tmp_path: Path) -> None:
    run.config.unlink()
    other = tmp_path / "other.db"
    assert run.load("--owner", "Anna", "--db", str(other)) == dbcli.OK
    assert "September 2026 for Anna" in run.output
    assert other.exists()
    assert not run.database.exists()


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ("owner: Lukas\n", "no database: pass --db or set 'database'"),
        ("database: x.db\n", "no owner: pass --owner or set 'owner'"),
        ("- a list\n", "expected a mapping"),
        ("database: x.db\nowner: L\nowners: [L]\n", "'owners' must map each person"),
        ("database: x.db\nowner: L\nowners: {L: {match: LUKAS}}\n", "expected 'match' with a list"),
        ("database: x.db\nowner: L\nowners: {L: [LUKAS]}\n", "expected 'match' with a list"),
        ("database: x.db\nowner: L\nowners: {L: {match: ['(']}}\n", "owners: L: "),
    ],
)
def test_configuration_mistakes_are_reported(run: Run, config: str, message: str) -> None:
    run.config.write_text(config, encoding="utf-8")
    assert run("reopen", "2026-09") == dbcli.FAILED
    assert message in run.output


def test_a_person_without_patterns_is_fine(run: Run) -> None:
    run.config.write_text(
        f"database: {run.database}\nowner: Lukas\nowners: {{Lukas: , Anna: {{}}}}\n",
        encoding="utf-8",
    )
    assert run.load() == dbcli.OK


def test_the_month_must_look_like_a_month(run: Run, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        run("commit", "September")
    assert "'September' is not a month like 2026-09" in capsys.readouterr().err


def test_the_main_command_hands_database_commands_over(run: Run) -> None:
    out = io.StringIO()
    argv = ["reopen", "2026-09", "--config", str(run.config)]
    assert cli.main(argv, out=out) == dbcli.FAILED
    assert out.getvalue() == "error: nothing is staged for Lukas in 2026-09\n"
