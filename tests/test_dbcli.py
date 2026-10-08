import io
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import uvicorn
from starlette.applications import Starlette

from finanze import categories, cli, dbcli, prices
from finanze.storage import db
from tests.conftest import INSTRUMENTS_YAML, write_rules
from tests.fakes import FakeSource

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
CONFIG = """database: {database}
owner: Lukas
owners:
  Lukas: {{match: ['LUKAS MEIER']}}
  Anna: {{match: ['ANNA MEIER']}}
"""


MATURED = "  912797YY2: {maturity: 2026-09-16}\n"
AUGUST_PURCHASE = (
    "Date,Type,Sub Type,Action,Symbol,Instrument Type,Description,Value,Quantity,Average Price,"
    "Commissions,Fees,Multiplier,Root Symbol,Underlying Symbol,Expiration Date,Strike Price,"
    "Call or Put,Order #,Total,Currency\n"
    "2026-08-20T15:58:53+0200,Trade,Buy to Open,BUY_TO_OPEN,912797YY2,Fixed Income Security,"
    'Bought 10 912797YY2 @ 992.00,"-9,920.00",10,-992.00,0.00,0.00,,,,,,,900000001,'
    '"-9,920.00",USD\n'
)


class Run:
    """Runs a database command on the fixture folder and records what it printed."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.database = folder / "data" / "finanze.db"
        self.config = folder / "finanze.yaml"
        self.config.write_text(CONFIG.format(database=self.database), encoding="utf-8")
        self.rules = write_rules(folder)
        self.instruments = folder / "instruments.yaml"
        self.instruments.write_text(INSTRUMENTS_YAML + MATURED, encoding="utf-8")
        self.output = ""
        self.source: FakeSource | None = None  # what `prices` fetches from
        self.answers: list[str] = []
        self.questions: list[str] = []

    def __call__(self, command: str, *args: str) -> int:
        argv = [command, *args, "--config", str(self.config)]
        out = io.StringIO()
        code = dbcli.main(argv, out, ask=self._ask, now=lambda: NOW, source=self.source)
        self.output = out.getvalue()
        return code

    def _ask(self, question: str) -> str:
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else ""

    def load(self, *args: str, folder: Path | None = None) -> int:
        paths = ["--rules", str(self.rules), "--instruments", str(self.instruments)]
        return self("import", str(folder or self.folder), *paths, *args)

    def load_august(self) -> int:
        """The month before the fixtures: just the purchase of the bill that matures in them."""
        august = self.folder / "old" / "august"
        august.mkdir(parents=True, exist_ok=True)
        (august / "tastytrade.csv").write_text(AUGUST_PURCHASE, encoding="utf-8")
        return self.load(folder=august)

    def pending(self) -> int:
        conn = db.connect(self.database)
        try:
            query = "SELECT count(*) FROM transactions WHERE category_id IS NULL AND kind <> ?"
            return int(conn.execute(query, ("internal",)).fetchone()[0])
        finally:
            conn.close()

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
    assert run.load_august() == dbcli.OK
    run.load()
    staged = run.count("transactions") - 1  # all but the August purchase
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
    assert "transactions need a category: import again with --review" in run.output
    assert run("commit", "2026-09") == dbcli.FAILED
    assert "still need a category" in run.output
    assert not list((run.database.parent / "backups").iterdir())


def test_a_missing_statement_is_shown_on_import_and_blocks_the_commit(run: Run) -> None:
    run.load()
    conn = db.connect(run.database)
    # As if July had been imported and August had not: Migros closed July on another balance.
    conn.execute(
        "INSERT INTO statement_files (owner_id, source, filename, sha256, period_start, "
        "period_end, reconciled, imported_at) "
        "VALUES (1, 'migros', 'july.csv', 'x', '2026-07-01', '2026-07-31', 'yes', 'x')"
    )
    conn.execute(
        "INSERT INTO balances SELECT account_id, '2026-07-31', amount_nano + 1, 'statement', "
        "last_insert_rowid() FROM balances b JOIN accounts a ON a.id = b.account_id "
        "WHERE a.name = 'Migros' AND b.date = '2026-08-31'"
    )
    conn.close()
    assert run.load() == dbcli.OK
    assert "  missing: Migros (CHF): no statement covers the days after 2026-07-31" in run.output
    assert run("commit", "2026-09") == dbcli.FAILED
    assert "error: " in run.output
    assert "no statement covers" in run.output


def test_a_bill_without_its_purchase_is_shown_on_import_and_blocks_the_commit(run: Run) -> None:
    assert run.load() == dbcli.OK
    assert "  missing: Tastytrade: 912797YY2 matured or was sold on 2026-09-16" in run.output
    assert run("commit", "2026-09") == dbcli.FAILED
    assert "its purchases are not all stored" in run.output
    assert run.load_august() == dbcli.OK
    assert run("commit", "2026-09") == dbcli.OK


def test_review_asks_saves_rules_and_stages_the_categorized_month(run: Run) -> None:
    run.rules.write_text("rules:\n  - {match: Muster, category: Affitto}\n", encoding="utf-8")
    assert run.load() == dbcli.NEEDS_REVIEW
    before = run.pending()
    run.answers = ["Spesa", "q"]
    assert run.load("--review") == dbcli.NEEDS_REVIEW
    assert len(run.questions) == 2
    assert f"saved to {run.rules}" in run.output
    assert 'category: "Spesa"' in run.rules.read_text(encoding="utf-8")
    assert 0 < run.pending() < before

    run.questions.clear()
    assert run.load("--review") == dbcli.NEEDS_REVIEW  # nothing answered: still pending
    assert run.questions


def test_the_rules_of_the_other_person_are_not_applied(run: Run) -> None:
    run.rules.write_text("rules: []\n", encoding="utf-8")
    rule = "rules:\n  - {match: Muster, category: Affitto}\n"
    run.rules.with_name("rules.anna.yaml").write_text(rule, encoding="utf-8")
    run.load()
    without = run.pending()
    run.rules.with_name("rules.lukas.yaml").write_text(rule, encoding="utf-8")
    run.load()
    assert run.pending() == without - 1


def test_review_can_add_a_category_which_reaches_the_database(run: Run) -> None:
    run.rules.write_text("rules:\n  - {match: Muster, category: Affitto}\n", encoding="utf-8")
    run.answers = ["+Prestiti", "q"]
    assert run.load("--review") == dbcli.NEEDS_REVIEW
    added = run.rules.with_name("categories.yaml")
    assert f"new category {len(categories.ORDER) + 1}=Prestiti" in run.output
    assert "Prestiti" in added.read_text(encoding="utf-8")
    conn = db.connect(run.database)
    query = (
        "SELECT count(*) FROM transactions t JOIN categories c ON c.id = t.category_id "
        "WHERE c.name = 'Prestiti'"
    )
    assert conn.execute(query).fetchone()[0] > 0
    conn.close()
    assert run.load() == dbcli.NEEDS_REVIEW  # the saved rule and category are read again


def test_review_asks_nothing_when_every_row_has_a_category(run: Run) -> None:
    assert run.load("--review") == dbcli.OK
    assert run.questions == []


PENSION = Path(__file__).parent / "fixtures" / "finpension" / "transactions.tsv"


def load_pension(run: Run) -> int:
    folder = run.folder / "old" / "pension"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pillar 3a.tsv").write_bytes(PENSION.read_bytes())
    return run.load(folder=folder)


def test_a_statement_of_several_months_is_committed_as_a_range(run: Run) -> None:
    assert load_pension(run) == dbcli.OK
    assert "July 2026 to September 2026 for Lukas: 1 statements reconciled, 9 trans" in run.output
    assert run.output.endswith("Next: finanze commit 2026-07 2026-09\n")

    assert run("commit", "2026-07", "2026-09") == dbcli.OK
    assert "July 2026 to September 2026 committed for Lukas. Copy from before: " in run.output
    assert run.count("committed_transactions") == 9
    assert run("commit", "2026-07", "2026-09") == dbcli.OK
    assert run.output == "July 2026 to September 2026 was already committed for Lukas.\n"

    assert run("reopen", "2026-08", "2026-09") == dbcli.OK
    assert run.output == "August 2026 to September 2026 is open again for Lukas.\n"
    assert run.count("committed_transactions") == 4  # July: deposit, two buys, the fee


def test_a_range_stops_at_the_first_month_that_cannot_be_committed(run: Run) -> None:
    load_pension(run)
    assert run("commit", "2026-07", "2026-10") == dbcli.FAILED
    assert run.output == (
        "July 2026 to September 2026 committed for Lukas, then:\n"
        "error: nothing is staged for Lukas in 2026-10\n"
    )
    assert len(list((run.database.parent / "backups").iterdir())) == 1


def test_files_and_folders_can_be_given_together(run: Run) -> None:
    run.load_august()
    pension = run.folder / "old" / "pension"
    pension.mkdir(parents=True)
    report = pension / "pillar 3a.tsv"
    report.write_bytes(PENSION.read_bytes())
    paths = ["--rules", str(run.rules), "--instruments", str(run.instruments)]
    assert run("import", str(run.folder), str(report), *paths) == dbcli.OK
    assert "July 2026 to September 2026 for Lukas: 7 statements reconciled" in run.output
    assert run("commit", "2026-07", "2026-09") == dbcli.OK


def test_a_second_broker_account_is_named_by_its_number(run: Run) -> None:
    run.load_august()
    with run.config.open("a", encoding="utf-8") as config:
        config.write("account_labels: {U12345678: IBKR 2}\n")
    assert run.load() == dbcli.OK
    conn = db.connect(run.database)
    names = [row[0] for row in conn.execute("SELECT name FROM accounts WHERE source = 'ibkr'")]
    conn.close()
    assert sorted(names) == ["IBKR 2 CHF", "IBKR 2 EUR", "IBKR 2 USD"]

    run.config.write_text(f"database: {run.database}\nowner: L\naccount_labels: [x]\n")
    assert run.load() == dbcli.FAILED
    assert "'account_labels' must map account numbers to names" in run.output


def test_a_balance_can_be_typed_for_an_account_without_statements(
    run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("balance", "Kasse", "2023-01-31", "5000.10", "eur") == dbcli.OK
    assert run.output == "Kasse of Lukas: 5000.10 EUR on 2023-01-31.\n"
    assert run.count("balances") == 1
    assert run("balance", "Kasse", "2023-02-28", "1", "CHF") == dbcli.FAILED
    assert "'Kasse' is in EUR, not CHF" in run.output
    with pytest.raises(SystemExit):
        run("balance", "Kasse", "2023-02-28", "1,5", "EUR")
    assert "'1,5' is not an amount like 1234.56" in capsys.readouterr().err


def test_a_path_that_does_not_exist_is_reported(run: Run) -> None:
    assert run("import", str(run.folder / "nowhere")) == dbcli.FAILED
    assert run.output == f"error: {run.folder / 'nowhere'}: no such file or folder\n"


def test_a_range_must_run_forwards(run: Run) -> None:
    assert run("commit", "2026-09", "2026-07") == dbcli.FAILED
    assert run.output == "error: 2026-07 is before 2026-09\n"


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


@pytest.fixture
def closed(run: Run) -> Run:
    """August and September 2026, committed."""
    run.load_august()
    run.load()
    assert run("commit", "2026-08", "2026-09") == dbcli.OK
    return run


def test_query_lists_the_matching_rows_and_their_total(closed: Run) -> None:
    assert closed("query", "--category", "Spesa") == dbcli.OK
    assert closed.output == (
        "2026-08-07  Lukas  Swisscard    -41.85  CHF  Spesa  FRISCHMARKT ZENTRUM\n"
        "2026-08-30  Lukas  Migros       -25.40  CHF  Spesa  KONSUM-1234 ZUERICH\n"
        "2026-08-31  Lukas  Revolut CHF  -20.00  CHF  Spesa  Konsum\n"
        "3 transactions\n"
        "Total:\n"
        "  -87.25 CHF  (3)\n"
    )


def test_query_can_print_the_totals_alone_with_a_line_per_period(closed: Run) -> None:
    assert closed("query", "--kind", "income", "--summary") == dbcli.OK
    assert closed.output == (
        "9 transactions\n"
        "Total:\n"
        "   7,430.70 CHF  (6)\n"
        "  14,999.60 USD  (3)\n"
        "By month:\n"
        "  2026-08       2.40 CHF  (1)\n"
        "  2026-09   7,428.30 CHF  (5)\n"
        "  2026-09  14,999.60 USD  (3)\n"
    )


def test_query_takes_a_day_a_month_or_a_year_as_either_end(closed: Run) -> None:
    def count(*args: str) -> str:
        assert closed("query", "--summary", "--category", "Spesa", *args) == dbcli.OK
        return closed.output.partition("\n")[0]

    assert count("--from", "2026-08-30") == "2 transactions"
    assert count("--to", "2026-08-30") == "2 transactions"
    assert count("--from", "2026-08", "--to", "2026-08") == "3 transactions"
    assert count("--from", "2026-09") == "0 transactions"
    assert count("--from", "2026", "--to", "2026") == "3 transactions"
    assert count("--to", "2025") == "0 transactions"


def test_query_by_booking_date_shows_that_date(closed: Run) -> None:
    assert closed("query", "--text", "frischmarkt", "--booking", "--from", "2026-09") == dbcli.OK
    assert closed.output == (
        "2026-09-04  Lukas  Swisscard  -41.85  CHF  Spesa  FRISCHMARKT ZENTRUM\n"
        "1 transaction\n"
        "Total:\n"
        "  -41.85 CHF  (1)\n"
    )


def test_query_combines_account_amount_and_owner(closed: Run) -> None:
    migros = ("query", "--account", "Migros", "--min", "1000", "--max", "2000")
    assert closed(*migros, "--owner", "Lukas") == dbcli.OK
    assert closed.output.startswith(
        "2026-09-01  Lukas  Migros  -1,850.00  CHF  Affitto  Hans Muster\n"
        "2026-09-08  Lukas  Migros  -1,288.18  CHF           Swisscard AECS GmbH\n"
        "2 transactions\n"
    )
    assert closed("query", "--owner", "Anna") == dbcli.OK
    assert closed.output == "0 transactions\n"


def test_query_reads_both_people_without_an_owner_in_the_config(closed: Run) -> None:
    closed.config.write_text(f"database: {closed.database}\n", encoding="utf-8")
    assert closed("query", "--summary") == dbcli.OK
    assert closed.output.startswith("110 transactions\n")


def test_query_refuses_a_name_it_does_not_know(closed: Run) -> None:
    assert closed("query", "--category", "Spese") == dbcli.FAILED
    assert closed.output == "error: no category named 'Spese'\n"


@pytest.mark.parametrize("text", ["May", "20266", "2026-13", "2026-02-30"])
def test_query_dates_must_look_like_dates(
    closed: Run, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    with pytest.raises(SystemExit):
        closed("query", "--from", text)
    assert f"{text!r} is not a day, a month or a year" in capsys.readouterr().err


def test_serve_brings_the_schema_up_to_date_and_listens_on_this_machine_only(
    run: Run, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    served: list[tuple[object, dict[str, object]]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **how: served.append((app, how)))
    run.config.write_text(f"database: {run.database}\n", encoding="utf-8")  # no owner needed
    assert run("serve", "--port", "9000") == dbcli.OK
    assert run.output == f"{run.database} is at http://127.0.0.1:9000 (Ctrl-C to stop)\n"
    ((app, how),) = served
    assert (how["host"], how["port"]) == ("127.0.0.1", 9000)
    assert isinstance(app, Starlette)
    assert app.state.database == run.database
    assert app.state.rules.shared == Path("rules.local.yaml")
    assert app.state.rules.people == {}
    assert run.count("schema_version") == len(db.migrations())

    with pytest.raises(SystemExit):
        run("serve", "--host", "0.0.0.0")  # noqa: S104  # the address that must be refused
    assert "unrecognized arguments: --host" in capsys.readouterr().err


# ---------------------------------------------------------------------------------- prices


def test_prices_fetches_what_is_held_and_says_what_it_stored(closed: Run) -> None:
    closes = [(date(2026, 9, 15), "112.5"), (date(2026, 9, 30), "113.1")]
    rates = [(date(2026, 9, 30), "0.8")]
    closed.source = FakeSource(
        {"ACME": closes, "GOOG": closes, "WIDG.SW": closes[:1]}
        | {f"{currency}CHF=X": rates for currency in ("EUR", "USD", "GBP", "JPY")},
    )
    closed.instruments.write_text(
        INSTRUMENTS_YAML + MATURED + "  WIDG: {price_symbol: WIDG.SW}\n", encoding="utf-8"
    )
    paths = ["--instruments", str(closed.instruments)]
    assert closed("prices", "--through", "2026-09-30", *paths) == dbcli.OK
    assert closed.output == "4 closes, 4 exchange rates and 0 splits stored.\n"
    # Nothing is fetched twice, and no owner is needed: prices are everyone's.
    closed.config.write_text(f"database: {closed.database}\n", encoding="utf-8")
    assert closed("prices", "--through", "2026-09-30", *paths) == dbcli.OK
    assert closed.output == "0 closes, 0 exchange rates and 0 splits stored.\n"


def test_prices_lists_what_it_could_not_fetch_and_fetches_up_to_today(closed: Run) -> None:
    closed.source = FakeSource({"USDCHF=X": [(date(2026, 10, 5), "0.8")]})
    assert closed("prices", "--instruments", str(closed.instruments)) == dbcli.NEEDS_REVIEW
    lines = closed.output.splitlines()
    assert lines[0] == "0 closes, 1 exchange rates and 0 splits stored."
    assert "  missing: ACME: no prices (ACME: No data found)" in lines
    assert "  missing: EUR: no rates (EURCHF=X: No data found)" in lines
    assert all(asked[2] <= NOW.date() for asked in closed.source.asked)
    assert ("USDCHF=X", NOW.date()) in {(asked[0], asked[2]) for asked in closed.source.asked}


def test_prices_uses_the_real_source_unless_given_another(
    closed: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    made: list[FakeSource] = []

    def yahoo() -> FakeSource:
        made.append(FakeSource())
        return made[-1]

    monkeypatch.setattr(prices, "Yahoo", yahoo)
    assert closed("prices", "--instruments", str(closed.instruments)) == dbcli.NEEDS_REVIEW
    assert len(made) == 1
    assert made[0].asked
