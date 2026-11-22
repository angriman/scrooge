import getpass
import hashlib
import io
import shutil
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import uvicorn
from starlette.applications import Starlette

from finanze import categories, cli, dbcli, networth, prices
from finanze.extractors import ocr
from finanze.storage import db, holdings, store
from finanze.web import auth
from tests.conftest import FILES, FIXTURES, INSTRUMENTS_YAML, write_rules
from tests.fakes import FakeSource

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
Typed = Callable[..., tuple[int, str, list[str]]]
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
        self.now = NOW
        self.source: FakeSource | None = None  # what `prices` fetches from
        self.answers: list[str] = []
        self.questions: list[str] = []

    def __call__(self, command: str, *args: str) -> int:
        argv = [command, *args, "--config", str(self.config)]
        out = io.StringIO()
        code = dbcli.main(argv, out, ask=self._ask, now=lambda: self.now, source=self.source)
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


def test_import_says_what_became_of_each_file_and_what_is_next(run: Run) -> None:
    assert run.load_august() == dbcli.OK
    assert run.output == (
        "  staged tastytrade.csv (Tastytrade transactions)\n"
        "1 transactions staged for Lukas.\n"
        "Next: finanze commit\n"
    )
    assert run.load() == dbcli.OK
    assert "  staged Migros.csv (Migros Bank account statement)\n" in run.output
    assert "  staged swisscard.pdf (Swisscard invoice)\n" in run.output
    assert "  skipped ibkr.pdf (not a statement)\n" in run.output
    assert "  note: Revolut EUR includes rows with no balance to check against\n" in run.output
    assert "109 transactions staged for Lukas.\n" in run.output
    assert run.output.endswith("Next: finanze commit\n")
    assert run.count("transactions") > 0
    assert run.count("committed_transactions") == 0


def test_commit_saves_a_copy_first_and_uncommit_undoes_it(run: Run) -> None:
    assert run.load_august() == dbcli.OK
    run.load()
    staged = run.count("transactions") - 1  # all but the August purchase
    assert run("commit") == dbcli.OK
    copy = run.database.parent / "backups" / "finanze-20261006T120000.db"
    assert "  committed tastytrade.csv (2026-08-20 to 2026-08-20)\n" in run.output
    assert f"7 statements committed for Lukas. Copy from before: {copy}\n" in run.output
    assert copy.exists()
    assert run.count("committed_transactions") == staged + 1

    copy.unlink()
    assert run("commit") == dbcli.OK
    assert run.output == "Nothing is staged for Lukas.\n"
    assert not copy.exists()

    # What is committed is left as it is: the same statements again find their rows there.
    assert run.load() == dbcli.OK
    assert "  Migros.csv (Migros Bank account statement): committed already, nothing new\n" in (
        run.output
    )
    assert "0 transactions staged" in run.output
    # One row less than is committed: the interest split off the bill is in no statement.
    assert f"  {staged - 1} rows are committed already: left as they are" in run.output
    assert "differs" not in run.output
    assert "Next:" not in run.output
    assert run.count("committed_transactions") == staged + 1
    assert run("uncommit", "3") == dbcli.OK
    assert run.output == "Migros.csv is staged again for Lukas.\n"
    assert run.count("committed_transactions") < staged + 1
    assert run.load() == dbcli.OK  # the statement it took back is found in the folder again
    assert "  staged Migros.csv (Migros Bank account statement)\n" in run.output
    assert run("commit") == dbcli.OK


def test_statements_are_listed_staged_first_with_the_numbers_to_act_on(run: Run) -> None:
    assert run("statements") == dbcli.OK
    assert run.output == "No statements for Lukas.\n"
    run.load_august()
    run.load()
    assert run("statements") == dbcli.OK
    lines = run.output.splitlines()
    assert (
        lines[0] == "    6  staged     2026-08-05 to 2026-09-04  Swisscard invoice: swisscard.pdf"
    )
    assert len(lines) == 7
    assert {line.split()[1] for line in lines} == {"staged"}
    run("commit")
    run("uncommit", "2")
    assert run("statements") == dbcli.OK
    first, *rest = run.output.splitlines()
    assert first == "    2  staged     2026-09-01 to 2026-09-30  IBKR activity statement: IBKR.csv"
    assert {line.split()[1] for line in rest} == {"committed"}


def test_discard_removes_a_staged_statement_and_not_a_committed_one(run: Run) -> None:
    run.load_august()
    assert run("discard", "1") == dbcli.OK
    assert run.output == "tastytrade.csv and what it staged are removed; its original is kept.\n"
    assert run.count("statement_files") == 0
    assert run.count("transactions") == 0
    assert kept(run) != []  # the original stays
    run.load_august()
    run("commit")
    assert run("discard", "1") == dbcli.FAILED
    assert run.output == "error: tastytrade.csv is committed: take it back first\n"
    assert run("uncommit", "1") == dbcli.OK
    assert run("discard", "1") == dbcli.OK


def test_what_is_booked_today_or_later_waits_for_the_next_import(run: Run) -> None:
    only = run.folder / "old" / "only"
    only.mkdir()
    shutil.copy(run.folder / "Migros.csv", only / "Migros.csv")
    run.now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)  # 9 September is the last day over
    assert run.load(folder=only) == dbcli.OK
    later = run.output.splitlines()[2]
    assert later.endswith(" booked today or later wait for the next import")
    run.now = NOW
    assert run.load(folder=only) == dbcli.OK
    assert "booked today or later" not in run.output


def test_a_statement_that_is_not_stored_or_not_committed_is_refused(run: Run) -> None:
    run.load_august()
    assert run("uncommit", "99") == dbcli.FAILED
    assert run.output == "error: Lukas has no statement 99\n"
    assert run("uncommit", "1") == dbcli.FAILED
    assert run.output == "error: tastytrade.csv is not committed\n"


def test_uncategorized_rows_are_reported_and_block_the_commit(run: Run) -> None:
    run.rules.write_text("rules: []\n", encoding="utf-8")
    assert run.load() == dbcli.NEEDS_REVIEW
    assert "transactions need a category: import again with --review" in run.output
    assert run("commit") == dbcli.FAILED
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
    assert run("commit") == dbcli.FAILED
    assert "error: " in run.output
    assert "no statement covers" in run.output


def test_a_bill_without_its_purchase_is_shown_on_import_and_blocks_the_commit(run: Run) -> None:
    assert run.load() == dbcli.OK
    assert "  missing: Tastytrade: 912797YY2 matured or was sold on 2026-09-16" in run.output
    assert "Next:" not in run.output  # something is missing first
    assert run("commit") == dbcli.FAILED
    assert "its purchases are not all stored" in run.output
    assert run.load_august() == dbcli.OK
    assert run("commit") == dbcli.OK


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


def test_a_statement_of_several_months_is_one_statement(run: Run) -> None:
    assert load_pension(run) == dbcli.OK
    assert "  staged pillar 3a.tsv (finpension pillar 3a transactions)\n" in run.output
    assert run.output.endswith("9 transactions staged for Lukas.\nNext: finanze commit\n")

    assert run("commit") == dbcli.OK
    assert "  committed pillar 3a.tsv (2026-07-06 to 2026-09-16)\n" in run.output
    assert run.count("committed_transactions") == 9
    assert run("commit") == dbcli.OK
    assert run.output == "Nothing is staged for Lukas.\n"

    assert run("uncommit", "1") == dbcli.OK
    assert run.count("committed_transactions") == 0


def test_files_and_folders_can_be_given_together(run: Run) -> None:
    run.load_august()
    pension = run.folder / "old" / "pension"
    pension.mkdir(parents=True)
    report = pension / "pillar 3a.tsv"
    report.write_bytes(PENSION.read_bytes())
    paths = ["--rules", str(run.rules), "--instruments", str(run.instruments)]
    assert run("import", str(run.folder), str(report), *paths) == dbcli.OK
    assert "  staged pillar 3a.tsv (finpension pillar 3a transactions)\n" in run.output
    assert "  staged tastytrade.csv (Tastytrade transactions)\n" in run.output
    assert run("commit") == dbcli.OK


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


AXA_READ = (
    "Pillar 2 AXA of Lukas: 35,802.00 CHF on 2026-03-01, read off the axa certificate "
    "Scan 0001.pdf (mandatory part 12,345.65).\n"
)
SERVISA_READ = (
    "Pillar 2 Servisa of Lukas: 3,579.40 CHF on 2026-01-01, read off the servisa certificate "
    "Dokument.pdf (mandatory part 2,468.10).\n"
    "  note: nothing on this certificate adds up to that figure: compare it with the paper\n"
)


def test_pension_certificates_are_imported_and_what_was_read_is_shown(
    run: Run, pillar2: Path
) -> None:
    (pillar2 / "cover letter.txt").write_text("Dear member", encoding="utf-8")
    assert run.load(folder=pillar2) == dbcli.OK
    assert run.output == (
        "  staged Dokument.pdf (Servisa pension certificate)\n"
        "  staged Scan 0001.pdf (AXA pension certificate)\n"
        "  skipped cover letter.txt (not a statement)\n"
        + SERVISA_READ
        + AXA_READ
        + "0 transactions staged for Lukas.\nNext: finanze commit\n"
    )
    assert (run.count("statement_files"), run.count("balances")) == (2, 2)
    assert run.count("transactions") == 0
    assert run("commit") == dbcli.OK

    conn = db.connect(run.database)
    result = networth.run(conn, currency="CHF")
    conn.close()
    worth = dict(zip(result.days, result.total, strict=True))
    assert result.days[0] == date(2026, 1, 1)
    assert worth[date(2026, 2, 28)] == Decimal("3579.40")
    assert worth[date(2026, 3, 1)] == Decimal("3579.40") + Decimal("35802.00")
    assert result.days[-1] == date(2026, 3, 1)  # net worth ends on the last day committed


def test_the_balance_a_yearly_certificate_opens_with_is_shown_too(
    run: Run, pillar2: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = (FIXTURES / "axa_yearly" / "certificate.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(ocr, "read_text", lambda path: text)
    assert run.load(folder=pillar2 / "Scan 0001.pdf") == dbcli.OK
    assert run.output.startswith("  staged Scan 0001.pdf (AXA pension certificate)\n")
    assert run.output.removeprefix("  staged Scan 0001.pdf (AXA pension certificate)\n") == (
        "Pillar 2 AXA of Lukas: 37,026.00 CHF on 2026-01-01, read off the axa certificate "
        "Scan 0001.pdf (mandatory part 12,345.65).\n"
        "  and 30,000.75 CHF on 2025-01-01, where its year began\n"
        "0 transactions staged for Lukas.\n"
        "Next: finanze commit\n"
    )
    assert (run.count("statement_files"), run.count("balances")) == (1, 2)


def test_a_pension_fund_figure_typed_between_certificates_is_pillar_2(
    run: Run, pillar2: Path
) -> None:
    assert run.load(folder=pillar2) == dbcli.OK
    assert run("commit") == dbcli.OK
    assert run("balance", "Pillar 2 AXA", "2026-06-30", "36500", "CHF") == dbcli.OK
    assert run.output == "Pillar 2 AXA of Lukas: 36500 CHF on 2026-06-30.\n"
    assert run("balance", "Pillar 2 AXA", "2026-03-01", "1", "CHF") == dbcli.FAILED
    assert "has a certificate for 2026-03-01, which says 35,802.00 CHF" in run.output

    conn = db.connect(run.database)
    try:
        valued = networth.value(conn, "Lukas")
        axa = conn.execute("SELECT id FROM accounts WHERE name = 'Pillar 2 AXA'").fetchone()[0]
    finally:
        conn.close()
    assert valued is not None
    [series] = [s for s in valued.series if s.account_id == axa]
    assert series.asset == networth.PILLAR2
    on = dict(zip(valued.days, series.values, strict=True))
    assert on[date(2026, 6, 29)] == Decimal("35802.00")
    assert on[date(2026, 6, 30)] == Decimal(36500)  # after the last statement day too

    assert any(
        note.startswith("Pillar 2 AXA of Lukas is carried at what its certificates state, or was")
        for note in valued.notes
    )


def test_what_was_typed_is_removed_by_remove(run: Run, pillar2: Path) -> None:
    assert run.load(folder=pillar2) == dbcli.OK
    assert run("commit") == dbcli.OK
    assert run("balance", "Pillar 2 AXA", "2026-06-30", "36500", "CHF") == dbcli.OK
    assert run("balance", "--remove", "Pillar 2 AXA", "2026-06-30") == dbcli.OK
    assert run.output == "Removed what was typed of Pillar 2 AXA of Lukas for 2026-06-30.\n"
    assert run("balance", "--remove", "Pillar 2 AXA", "2026-06-30") == dbcli.FAILED
    assert "nothing was typed for Pillar 2 AXA of Lukas on 2026-06-30" in run.output
    assert run("balance", "--remove", "Pillar 2 AXA", "2026-06-30", "1") == dbcli.FAILED
    assert "`balance --remove` takes no figure" in run.output
    assert run("balance", "Pillar 2 AXA", "2026-06-30") == dbcli.FAILED
    assert "`balance` needs the amount and the currency" in run.output

    assert run("position", "Broker", "2026-06-30", "ABCD", "3", "USD") == dbcli.OK
    assert run("position", "--remove", "Broker", "2026-06-30", "abcd") == dbcli.OK
    assert run.output == "Removed what was typed of ABCD of Broker of Lukas for 2026-06-30.\n"
    assert run("position", "Broker", "2026-06-30", "ABCD") == dbcli.FAILED
    assert "`position` needs the quantity and the currency" in run.output


def test_a_certificate_read_again_after_its_commit_changes_nothing(run: Run, pillar2: Path) -> None:
    assert run.load(folder=pillar2) == dbcli.OK
    assert run("commit") == dbcli.OK
    assert run.load(folder=pillar2) == dbcli.OK
    assert "  Scan 0001.pdf (AXA pension certificate): committed already, nothing new\n" in (
        run.output
    )
    assert "Next:" not in run.output
    assert (run.count("statement_files"), run.count("balances")) == (2, 2)


def test_another_scan_of_a_committed_certificate_is_refused_until_it_is_taken_back(
    run: Run, pillar2: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run.load(folder=pillar2) == dbcli.OK
    assert run("commit") == dbcli.OK
    again = run.folder / "again" / "Scan 0001.pdf"
    again.parent.mkdir()
    again.write_bytes((pillar2 / "Scan 0001.pdf").read_bytes() + b"\n% scanned twice")
    text = (FIXTURES / "axa" / "certificate.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(ocr, "read_text", lambda path: text)
    assert run.load(folder=again.parent) == dbcli.FAILED
    assert run.output.startswith(
        "  refused Scan 0001.pdf (AXA pension certificate): a certificate of Pillar 2 AXA"
    )
    assert "is committed: take it back before Scan 0001.pdf replaces it" in run.output
    assert run("uncommit", "2") == dbcli.OK
    assert run.load(folder=again.parent) == dbcli.OK
    assert (run.count("statement_files"), run.count("balances")) == (2, 2)


def test_certificates_come_in_with_the_statements_of_other_months(
    run: Run, pillar2: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run.load_august()
    paths = ["--rules", str(run.rules), "--instruments", str(run.instruments)]
    assert run("import", str(run.folder), str(pillar2), *paths) == dbcli.OK
    assert "  staged Migros.csv (Migros Bank account statement)\n" in run.output
    assert "  staged Scan 0001.pdf (AXA pension certificate)\n" in run.output
    assert "  staged Dokument.pdf (Servisa pension certificate)\n" in run.output
    assert SERVISA_READ + AXA_READ in run.output
    assert run.output.endswith("Next: finanze commit\n")
    assert run("commit") == dbcli.OK

    # A certificate dated inside the days the statements cover is a statement like the others.
    text = (FIXTURES / "axa" / "certificate.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(ocr, "read_text", lambda path: text.replace("01.03.2026", "15.09.2026"))
    scan = pillar2 / "Scan 0001.pdf"
    assert run("import", str(run.folder), str(scan), *paths) == dbcli.OK
    assert "Pillar 2 AXA of Lukas: 35,802.00 CHF on 2026-09-15, " in run.output
    assert run.output.count("Next: ") == 1
    assert run("commit") == dbcli.OK


def test_a_certificate_that_cannot_be_read_is_refused_and_stores_nothing(
    run: Run, pillar2: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = (FIXTURES / "servisa" / "certificate.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(ocr, "read_text", lambda path: text.replace("3'579.40", "3'579.4O"))
    paths = ["--rules", str(run.rules), "--instruments", str(run.instruments)]
    assert run("import", str(run.folder), str(pillar2), *paths) == dbcli.FAILED
    assert "  refused Dokument.pdf (not read): cannot read amount" in run.output
    assert run.count("balances") > 0  # the statements of the folder were staged all the same
    conn = db.connect(run.database)
    scans = conn.execute("SELECT count(*) FROM statement_files WHERE source IN ('axa', 'servisa')")
    assert scans.fetchone()[0] == 0
    conn.close()


def test_a_path_that_does_not_exist_is_reported(run: Run) -> None:
    assert run("import", str(run.folder / "nowhere")) == dbcli.FAILED
    assert run.output == f"error: {run.folder / 'nowhere'}: no such file or folder\n"


def test_nothing_staged_is_said_by_commit(run: Run) -> None:
    assert run("commit") == dbcli.OK
    assert run.output == "Nothing is staged for Lukas.\n"
    assert not (run.database.parent / "backups").exists() or not list(
        (run.database.parent / "backups").iterdir()
    )


def test_a_file_that_does_not_reconcile_is_refused_and_the_others_are_staged(run: Run) -> None:
    statement = run.folder / "Migros.csv"
    text = statement.read_text(encoding="utf-8")
    statement.write_text(text.replace("-1288.18", "-1288.19"), encoding="utf-8")
    assert run.load_august() == dbcli.OK
    assert run.load() == dbcli.FAILED
    assert run.output.count("  refused ") >= 1
    assert "  refused Migros.csv (not read): " in run.output
    assert "  staged IBKR.csv (IBKR activity statement)\n" in run.output
    assert "Next:" not in run.output  # an exit code of failure, and nothing to say about next
    conn = db.connect(run.database)
    migros = conn.execute("SELECT count(*) FROM statement_files WHERE source = 'migros'")
    assert migros.fetchone()[0] == 0
    conn.close()
    assert run.count("transactions") > 1


def test_a_file_that_needs_a_category_ends_the_import_with_a_request_to_review(
    run: Run,
) -> None:
    run.rules.write_text("rules: []\n", encoding="utf-8")
    assert run.load() == dbcli.NEEDS_REVIEW
    assert "  staged Migros.csv (Migros Bank account statement)\n" in run.output


def test_a_refusal_comes_before_a_request_for_categories(run: Run) -> None:
    statement = run.folder / "Migros.csv"
    statement.write_text(
        statement.read_text(encoding="utf-8").replace("-1288.18", "-1288.19"), encoding="utf-8"
    )
    run.rules.write_text("rules: []\n", encoding="utf-8")
    assert run.load() == dbcli.NEEDS_REVIEW
    assert "  refused Migros.csv" in run.output


def test_owner_and_database_can_come_from_the_command_line(run: Run, tmp_path: Path) -> None:
    run.config.unlink()
    other = tmp_path / "other.db"
    assert run.load("--owner", "Anna", "--db", str(other)) == dbcli.OK
    assert "  staged Migros.csv (Migros Bank account statement)\n" in run.output
    assert "109 transactions staged for Anna.\n" in run.output
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
    assert run("commit") == dbcli.FAILED
    assert message in run.output


def test_a_person_without_patterns_is_fine(run: Run) -> None:
    run.config.write_text(
        f"database: {run.database}\nowner: Lukas\nowners: {{Lukas: , Anna: {{}}}}\n",
        encoding="utf-8",
    )
    assert run.load() == dbcli.OK


def test_a_statement_is_named_by_its_number(run: Run, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        run("uncommit", "September")
    assert "invalid int value: 'September'" in capsys.readouterr().err


def test_the_main_command_hands_database_commands_over(run: Run) -> None:
    out = io.StringIO()
    argv = ["uncommit", "1", "--config", str(run.config)]
    assert cli.main(argv, out=out) == dbcli.FAILED
    assert out.getvalue() == "error: Lukas has no statement 1\n"


@pytest.fixture
def closed(run: Run) -> Run:
    """August and September 2026, committed."""
    run.load_august()
    run.load()
    assert run("commit") == dbcli.OK
    return run


def test_a_position_typed_for_an_account_without_statements_is_valued_at_its_closes(
    closed: Run,
) -> None:
    """Shares at a broker no statement is read of: typed now and then, valued every day."""
    assert closed("position", "Schwab", "2026-09-10", "abcd", "10", "usd") == dbcli.OK
    assert closed.output == "Schwab of Lukas: 10 ABCD on 2026-09-10.\n"
    # Bought more, with no trade to show it: the new quantity stands, it is no mismatch.
    assert closed("position", "Schwab", "2026-09-20", "ABCD", "12", "USD") == dbcli.OK
    assert closed("position", "Schwab", "2026-09-20", "WIDG", "3", "USD", "--tab", "ETFs") == 0
    closes = [(date(2026, 9, 10), "100"), (date(2026, 9, 25), "110")]
    closed.source = FakeSource({"ABCD": closes, "WIDG": closes})
    closed("prices", "--through", "2026-09-30", "--instruments", str(closed.instruments))
    assert "ABCD" not in closed.output  # fetched: it is held

    conn = db.connect(closed.database)
    try:
        valued = networth.value(conn, "Lukas")
        assert valued is not None
        symbols = dict(conn.execute("SELECT id, symbol FROM instruments").fetchall())
        owner = store.owner_id(conn, "Lukas")
        assert owner is not None
        assert holdings.check(conn, owner) == []
    finally:
        conn.close()
    typed = {symbols[s.instrument_id]: s for s in valued.series if s.instrument_id in symbols}
    shares, fund = typed["ABCD"], typed["WIDG"]
    assert (shares.asset, shares.currency, shares.quantity) == (networth.STOCKS, "USD", Decimal(12))
    assert fund.asset == networth.ETFS
    on = dict(zip(valued.days, shares.values, strict=True))
    assert not on[date(2026, 9, 9)]  # nothing before the first day typed
    assert on[date(2026, 9, 15)] == Decimal(1000)
    assert on[date(2026, 9, 25)] == Decimal(1320)


def test_what_is_typed_after_the_last_statement_counts_from_its_day(closed: Run) -> None:
    """The statements end on 30 September; a position and a balance typed later still count."""
    assert closed("position", "Schwab", "2026-10-02", "ABCD", "10", "USD") == dbcli.OK
    assert closed("balance", "Kasse", "2026-10-02", "500", "EUR") == dbcli.OK
    closed.source = FakeSource({"ABCD": [(date(2026, 10, 1), "100"), (date(2026, 10, 3), "110")]})
    closed("prices", "--through", "2026-10-04", "--instruments", str(closed.instruments))
    conn = db.connect(closed.database)
    try:
        valued = networth.value(conn, "Lukas")
        accounts = dict(conn.execute("SELECT name, id FROM accounts").fetchall())
    finally:
        conn.close()
    assert valued is not None
    assert valued.days[-1] == date(2026, 10, 3)
    last = {s.account_id: s.values[-1] for s in valued.series}
    assert last[accounts["Schwab"]] == Decimal(1100)
    assert last[accounts["Kasse"]] == Decimal(500)


def test_a_position_is_refused_for_an_account_with_statements(closed: Run) -> None:
    assert closed("position", "Migros", "2026-09-10", "ABCD", "1", "CHF") == dbcli.FAILED
    assert "account 'Migros' cannot have both statements and typed balances" in closed.output


def test_a_typed_account_holds_instruments_in_several_currencies(closed: Run) -> None:
    assert closed("position", "Broker", "2026-09-10", "ABCD", "1", "USD") == dbcli.OK
    assert closed("position", "Broker", "2026-09-11", "NESN.SW", "2", "CHF") == dbcli.OK
    conn = db.connect(closed.database)
    try:
        held = conn.execute(
            "SELECT i.symbol, i.currency FROM positions p "
            "JOIN instruments i ON i.id = p.instrument_id WHERE p.source = 'manual' ORDER BY 1"
        ).fetchall()
    finally:
        conn.close()
    assert [tuple(row) for row in held] == [("ABCD", "USD"), ("NESN.SW", "CHF")]


def _steps(run: Run) -> dict[str, list[tuple[date, Decimal]]]:
    conn = db.connect(run.database)
    try:
        symbols = dict(conn.execute("SELECT id, symbol FROM instruments").fetchall())
        return {
            symbols[h.instrument_id]: [(s.day, s.quantity) for s in h.steps]
            for h in holdings.held(conn)
        }
    finally:
        conn.close()


def test_a_position_typed_leaves_the_account_s_other_instruments_as_they_were(
    closed: Run,
) -> None:
    assert closed("position", "Broker", "2026-09-10", "ABCD", "10", "USD") == dbcli.OK
    assert closed("position", "Broker", "2026-09-20", "EFGH", "3", "USD") == dbcli.OK
    steps = _steps(closed)
    assert steps["ABCD"] == [(date(2026, 9, 10), Decimal(10))]  # not 0 on 20 September
    assert steps["EFGH"] == [(date(2026, 9, 20), Decimal(3))]


def test_a_position_typed_never_changes_what_an_instrument_is(closed: Run) -> None:
    conn = db.connect(closed.database)
    try:
        before = conn.execute("SELECT symbol, type, currency FROM instruments").fetchall()
    finally:
        conn.close()
    known = {row[0]: (row[1], row[2]) for row in before}
    assert known["WIDG"] == ("etf", "USD")  # bought at Tastytrade
    assert closed("position", "Broker", "2026-09-10", "WIDG", "1", "USD") == dbcli.OK
    assert closed("position", "Broker", "2026-09-11", "NEW", "1", "USD") == dbcli.OK
    assert closed("position", "Broker", "2026-09-11", "FUND", "1", "USD", "--tab", "ETFs") == 0
    conn = db.connect(closed.database)
    try:
        after = {row[0]: row[1] for row in conn.execute("SELECT symbol, type FROM instruments")}
    finally:
        conn.close()
    assert (after["WIDG"], after["NEW"], after["FUND"]) == ("etf", "stock", "etf")


def test_a_position_typed_under_its_price_symbol_is_the_instrument_a_statement_books(
    closed: Run,
) -> None:
    closed.instruments.write_text(
        INSTRUMENTS_YAML + MATURED + "  NESN: {tab: Stocks, price_symbol: NESN.SW}\n"
        "  OLDW: {ticker: WIDG}\n",
        encoding="utf-8",
    )
    instruments = ("--instruments", str(closed.instruments))
    assert closed("position", "Broker", "2026-09-10", "nesn.sw", "2", "CHF", *instruments) == 0
    assert closed.output == "Broker of Lukas: 2 NESN on 2026-09-10.\n"
    assert closed("position", "Broker", "2026-09-10", "OLDW", "1", "USD", *instruments) == 0
    assert closed.output == "Broker of Lukas: 1 WIDG on 2026-09-10.\n"


@pytest.mark.parametrize("quantity", ["nan", "Infinity", "-1", "-0.5", "ten"])
def test_a_quantity_that_is_no_number_of_shares_is_refused(closed: Run, quantity: str) -> None:
    with pytest.raises(SystemExit):
        closed("position", "Broker", "2026-09-10", "ABCD", quantity, "USD")


@pytest.mark.parametrize("amount", ["nan", "Infinity", "-inf"])
def test_a_balance_that_is_no_amount_is_refused(closed: Run, amount: str) -> None:
    with pytest.raises(SystemExit):
        closed("balance", "Kasse", "2026-09-10", amount, "EUR")


def test_a_day_after_today_cannot_be_typed(closed: Run) -> None:
    # Today is 6 October 2026 (NOW): 2062 was meant to be 2026.
    for command in (
        ("balance", "Kasse", "2062-10-02", "500", "EUR"),
        ("position", "Broker", "2026-10-07", "ABCD", "1", "USD"),
    ):
        assert closed(*command) == dbcli.FAILED
        assert "is after today (2026-10-06)" in closed.output
    assert closed("balance", "Kasse", "2026-10-06", "500", "EUR") == dbcli.OK


def test_a_figure_typed_today_counts_today_before_any_close_of_today(closed: Run) -> None:
    closed.source = FakeSource({"ABCD": [(date(2026, 10, 1), "100")]})
    assert closed("position", "Broker", "2026-10-01", "ABCD", "10", "USD") == dbcli.OK
    closed("prices", "--through", "2026-10-02", "--instruments", str(closed.instruments))
    assert closed("balance", "Kasse", "2026-10-05", "500", "EUR") == dbcli.OK
    conn = db.connect(closed.database)
    try:
        valued = networth.value(conn, "Lukas")
    finally:
        conn.close()
    assert valued is not None
    assert valued.days[-1] == date(2026, 10, 5)


def test_a_person_with_typed_accounts_alone_counts(closed: Run) -> None:
    assert closed("balance", "Kasse", "2026-09-10", "500", "EUR", "--owner", "Anna") == 0
    conn = db.connect(closed.database)
    try:
        valued = networth.value(conn, "Anna")
    finally:
        conn.close()
    assert valued is not None
    assert valued.series[0].values[-1] == Decimal(500)


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
    assert closed.output.startswith("111 transactions\n")


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
    assert app.state.rules.shared == Path("rules.yaml")
    assert app.state.rules.people == {}
    assert app.state.imports.rules == [Path("rules.yaml")]
    assert app.state.imports.instruments == Path("instruments.yaml")
    assert app.state.currency == "EUR"  # without 'currency' in the config
    assert run.count("schema_version") == len(db.migrations())

    with pytest.raises(SystemExit):
        run("serve", "--host", "0.0.0.0")  # noqa: S104  # the address that must be refused
    assert "unrecognized arguments: --host" in capsys.readouterr().err


def test_the_config_says_where_the_rules_and_the_instruments_are(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """As on a machine where the personal files and the committed ones are in two folders."""
    data, image = run.folder / "data", run.folder / "image"
    image.mkdir()
    generic, instruments = image / "rules.yaml", image / "instruments.yaml"
    shutil.move(run.rules, generic)
    shutil.move(run.instruments, instruments)
    keys = f"rules: [{data / 'rules.local.yaml'}, {generic}]\ninstruments: {instruments}\n"
    run.config.write_text(CONFIG.format(database=run.database) + keys, encoding="utf-8")
    august = run.folder / "old" / "august"
    august.mkdir(parents=True)
    (august / "tastytrade.csv").write_text(AUGUST_PURCHASE, encoding="utf-8")
    assert run("import", str(august)) == dbcli.OK  # the bill's maturity is in `instruments`
    assert run("import", str(run.folder)) == dbcli.OK
    assert "109 transactions staged for Lukas.\n" in run.output  # every row has a category

    served: list[Starlette] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **_: served.append(app))
    assert run("serve") == dbcli.OK
    assert served[-1].state.imports.rules == [data / "rules.local.yaml", generic]
    assert served[-1].state.imports.instruments == instruments
    assert served[-1].state.rules.shared == data / "rules.local.yaml"
    # A flag still wins, and names every file read: none is looked for next to it.
    assert run("serve", "--rules", "mine.yaml", "--instruments", "other.yaml") == dbcli.OK
    assert served[-1].state.imports.rules == [Path("mine.yaml")]
    assert served[-1].state.imports.instruments == Path("other.yaml")


@pytest.mark.parametrize(
    ("keys", "why"),
    [
        ("rules: rules.local.yaml", "'rules' must list the rules files, the one both people"),
        ("rules: []", "'rules' must list the rules files"),
        ("rules: [{a: 1}]", "'rules' must list the rules files"),
        ("instruments: [a.yaml]", "'instruments' must be the path of the instruments file"),
        ("instruments: ''", "'instruments' must be the path of the instruments file"),
    ],
)
def test_rules_and_instruments_in_the_config_must_be_paths(
    run: Run, monkeypatch: pytest.MonkeyPatch, keys: str, why: str
) -> None:
    monkeypatch.setattr(uvicorn, "run", lambda app, **how: None)
    run.config.write_text(f"database: {run.database}\n{keys}\n", encoding="utf-8")
    assert run("serve") == dbcli.FAILED
    assert run.output.startswith(f"error: {run.config}: {why}")


WORDS = "correct horse battery"
STORED = auth.digest(WORDS, salt=bytes(16))


def serve_with(run: Run, monkeypatch: pytest.MonkeyPatch, serve: str) -> list[dict[str, object]]:
    """Run `serve` with that `serve:` in the config; how uvicorn was started, if it was."""
    served: list[dict[str, object]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **how: served.append({"app": app} | how))
    run.config.write_text(f"database: {run.database}\nserve: {serve}\n", encoding="utf-8")
    run("serve")
    return served


@pytest.mark.parametrize(
    ("host", "names", "allowed"),
    [
        ("192.168.1.20", "[finanze.home]", ("192.168.1.20", "finanze.home")),
        ("0.0.0.0", "[finanze.home]", ("finanze.home",)),  # noqa: S104  # asked for in the config
        ("'::'", "[finanze.home]", ("finanze.home",)),
        ("127.0.0.1", "[finanze.home]", ("finanze.home",)),  # behind a proxy
        ("192.168.1.20", "", ("192.168.1.20",)),
    ],
)
def test_serve_listens_where_the_config_says_once_it_has_a_password(
    run: Run,
    monkeypatch: pytest.MonkeyPatch,
    host: str,
    names: str,
    allowed: tuple[str, ...],
) -> None:
    config = f"{{host: {host}, names: {names or '[]'}, password: {STORED}}}"
    (how,) = serve_with(run, monkeypatch, config)
    address = host.strip("'")
    assert how["host"] == address
    printed = f"[{address}]" if ":" in address else address
    app = how["app"]
    assert isinstance(app, Starlette)
    assert app.state.access is not None
    assert app.state.hosts == ("127.0.0.1", "localhost", *allowed)
    assert run.output == (
        f"{run.database} is at http://{printed}:8765 (Ctrl-C to stop)\n"
        f"  for other devices, with the password: {', '.join(allowed)}\n"
    )


def test_a_password_alone_keeps_the_pages_on_this_machine_behind_it(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    (how,) = serve_with(run, monkeypatch, f"{{password: {STORED}}}")
    assert how["host"] == "127.0.0.1"
    app = how["app"]
    assert isinstance(app, Starlette)
    assert app.state.access is not None
    assert run.output == f"{run.database} is at http://127.0.0.1:8765 (Ctrl-C to stop)\n"


@pytest.mark.parametrize(
    ("serve", "why"),
    [
        ("{host: 192.168.1.20}", "not served beyond this machine without a password"),
        ("{host: 0.0.0.0, names: [a.home]}", "not served beyond this machine without a password"),
        ("{names: [a.home]}", "not served beyond this machine without a password"),
        ("{host: 192.168.1.20, password: hunter2}", "password: not a digest written by"),
        (f"{{host: 0.0.0.0, password: {STORED}}}", "names: say what the devices call"),
        (f"{{host: finanze.home, password: {STORED}}}", "'finanze.home' is not an address"),
        (f"{{host: 192.168.1.20, names: a.home, password: {STORED}}}", "names: expected a list"),
        (f"{{port: 80, password: {STORED}}}", "'serve' takes 'host', 'names', 'password' and"),
        ("[192.168.1.20]", "'serve' takes 'host', 'names', 'password' and 'read_only'"),
        ("{read_only: 1}", "serve: read_only: expected true or false, not 1"),
    ],
)
def test_serve_refuses_a_config_that_would_show_the_pages_unguarded_or_makes_no_sense(
    run: Run, monkeypatch: pytest.MonkeyPatch, serve: str, why: str
) -> None:
    assert serve_with(run, monkeypatch, serve) == []
    assert run.output.startswith(f"error: {run.config}: ")
    assert why in run.output


def test_serve_shows_a_copy_as_it_is_without_writing_anywhere(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    run.database.parent.mkdir()
    conn = db.connect(run.database)
    db.migrate(conn, NOW)
    conn.execute("DELETE FROM schema_version WHERE version > 1")  # as older code left it
    conn.close()
    assert serve_with(run, monkeypatch, "{read_only: true}") == []
    assert run.output == (
        f"error: {run.database}: schema version 1, where this code reads version "
        f"{db.latest()}: publish it again, with the same version of finanze on both machines\n"
    )
    assert run.count("schema_version") == 1  # not brought up to date either

    conn = db.connect(run.database)
    conn.execute("INSERT INTO schema_version VALUES (?, 'x')", (db.latest(),))
    conn.close()
    before = run.database.read_bytes()
    run.database.chmod(0o444)
    run.database.parent.chmod(0o555)  # where nothing can be written, like a share mounted so
    try:
        (how,) = serve_with(run, monkeypatch, "{read_only: true}")
    finally:
        run.database.parent.chmod(0o755)
        run.database.chmod(0o644)
    app = how["app"]
    assert isinstance(app, Starlette)
    assert (app.state.read_only, app.state.database) == (True, run.database)
    assert run.output == (
        f"{run.database} is at http://127.0.0.1:8765 (Ctrl-C to stop)\n"
        "  a copy, shown as 'finanze publish' left it: nothing here changes it\n"
    )
    assert run.database.read_bytes() == before
    assert [file.name for file in run.database.parent.iterdir()] == ["finanze.db"]

    (how,) = serve_with(run, monkeypatch, "{read_only: false}")
    assert isinstance(how["app"], Starlette)
    assert how["app"].state.read_only is False


def test_a_database_newer_than_this_code_is_refused_by_every_command(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    run.database.parent.mkdir()
    conn = db.connect(run.database)
    db.migrate(conn, NOW)
    conn.execute("INSERT INTO schema_version VALUES (99, 'x')")
    conn.close()
    newer = f"error: the database is at schema version 99, this code knows up to {db.latest()}\n"
    assert run("query") == dbcli.FAILED
    assert run.output == newer
    assert serve_with(run, monkeypatch, "{}") == []
    assert run.output == newer
    assert serve_with(run, monkeypatch, "{read_only: true}") == []
    assert f"{run.database}: schema version 99, where this code reads" in run.output


def older(run: Run, monkeypatch: pytest.MonkeyPatch) -> int:
    """The database as code one migration older left it, with an owner in it; its version."""
    every = db.migrations()
    run.database.parent.mkdir()
    conn = db.connect(run.database)
    monkeypatch.setattr(db, "migrations", lambda: every[:-1])
    db.migrate(conn, NOW)
    monkeypatch.undo()
    conn.execute("INSERT INTO owners (name) VALUES ('Lukas')")
    conn.close()
    return every[-2][0]


def schema_of(path: Path) -> int:
    with db.reading(path) as conn:
        return db.version(conn)


def test_serve_copies_a_database_at_an_older_schema_before_bringing_it_up_to_date(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    was = older(run, monkeypatch)
    (how,) = serve_with(run, monkeypatch, "{}")
    assert isinstance(how["app"], Starlette)
    copy = run.database.parent / "backups" / "finanze-20261006T120000.db"
    assert run.output == (
        f"{run.database}: schema version {was} brought up to {db.latest()}. "
        f"Copy from before: {copy}\n"
        f"{run.database} is at http://127.0.0.1:8765 (Ctrl-C to stop)\n"
    )
    assert (schema_of(copy), schema_of(run.database)) == (was, db.latest())
    assert run.count("owners") == 1

    (how,) = serve_with(run, monkeypatch, "{}")  # up to date: no copy, nothing said
    assert run.output == f"{run.database} is at http://127.0.0.1:8765 (Ctrl-C to stop)\n"
    assert [path.name for path in copy.parent.iterdir()] == [copy.name]


def test_serve_does_not_start_on_a_database_a_migration_fails_on(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    was = older(run, monkeypatch)
    every = db.migrations()
    broken = (every[-1][0] + 1, "CREATE TABLE owners (x INTEGER) STRICT;")
    monkeypatch.setattr(db, "migrations", lambda: [*every, broken])
    assert serve_with(run, monkeypatch, "{}") == []  # never listens
    copy = run.database.parent / "backups" / "finanze-20261006T120000.db"
    assert run.output == (
        f"error: {run.database}: schema version {broken[0]} could not be reached (table "
        f"owners already exists): the database is at version {every[-1][0]}; copy from "
        f"before: {copy}\n"
    )
    assert (schema_of(copy), schema_of(run.database)) == (was, every[-1][0])

    # Started again, as a container is: it fails the same way, and no copy is added for a
    # database that did not change.
    assert serve_with(run, monkeypatch, "{}") == []
    assert run.output.endswith(f"the database is at version {every[-1][0]}\n")
    assert [path.name for path in copy.parent.iterdir()] == [copy.name]


def test_serve_refuses_a_copy_that_is_missing_or_is_no_database(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert serve_with(run, monkeypatch, "{read_only: true}") == []
    assert run.output == (
        f"error: {run.database}: no copy of the database to show: run 'finanze publish' "
        "where the data is\n"
    )
    assert not run.database.parent.exists()  # an unmounted share is not filled in

    run.database.parent.mkdir()
    run.database.write_text("half a file, or another one\n" * 40, encoding="utf-8")
    assert serve_with(run, monkeypatch, "{read_only: true}") == []
    assert run.output.startswith(f"error: {run.database}: not a database (file is not a database)")
    assert run.output.endswith(
        ": publish it again, with the same version of finanze on both machines\n"
    )

    run.database.write_bytes(b"")  # what SQLite takes for a database with nothing in it
    assert serve_with(run, monkeypatch, "{read_only: true}") == []
    assert f"{run.database}: schema version 0, where this code reads" in run.output
    assert run.database.read_bytes() == b""


@pytest.fixture
def typed(monkeypatch: pytest.MonkeyPatch) -> Typed:
    """Runs `finanze password` with those answers: its code, what it printed, what it asked."""

    def run(*answers: str) -> tuple[int, str, list[str]]:
        out, left, asked = io.StringIO(), list(answers), []

        def secret(question: str) -> str:
            asked.append(question)
            return left.pop(0)

        monkeypatch.setattr(getpass, "getpass", secret)
        return dbcli.main(["password"], out), out.getvalue(), asked

    return run


def test_password_prints_the_line_for_the_config_and_never_the_password(typed: Typed) -> None:
    code, printed, asked = typed(WORDS, WORDS)
    assert code == dbcli.OK
    assert asked == ["Password: ", "Once more: "]
    first, line = printed.splitlines()
    assert first == "Put this under 'serve' in finanze.yaml:"
    assert line.startswith("  password: scrypt$")
    assert auth.matches(WORDS, line.removeprefix("  password: "))
    assert WORDS not in printed


def test_password_wants_twelve_characters_typed_the_same_twice(typed: Typed) -> None:
    code, printed, asked = typed("only eleven")
    assert (code, printed) == (dbcli.FAILED, "error: a password has at least 12 characters\n")
    assert asked == ["Password: "]
    code, printed, _ = typed(WORDS, WORDS + "!")
    assert (code, printed) == (dbcli.FAILED, "error: the two do not match\n")


# --------------------------------------------------------------------------------- publish


@pytest.fixture
def share(tmp_path: Path) -> Path:
    """The other machine's folder, as it is mounted on this one."""
    folder = tmp_path / "share"
    folder.mkdir()
    return folder


def test_publish_copies_the_database_to_the_folder_and_says_what_went(
    closed: Run, share: Path
) -> None:
    closed.config.write_text(f"database: {closed.database}\n", encoding="utf-8")  # no owner
    assert closed("publish", str(share)) == dbcli.OK
    copy = share / "finanze.db"
    assert closed.output == (
        f"Published {copy}: {copy.stat().st_size:,} bytes, 111 committed transactions, "
        f"schema version {db.latest()}.\n"
    )
    # Only the database: not the config, the rules, the statements or the copies kept here.
    assert [file.name for file in share.iterdir()] == ["finanze.db"]
    assert (closed.database.parent / "backups").is_dir()
    with db.reading(copy) as conn:
        assert conn.execute("SELECT count(*) FROM committed_transactions").fetchone()[0] == 111


def test_publish_says_what_the_copy_holds_of_statements_that_are_not_committed(
    run: Run, share: Path
) -> None:
    run.load_august()
    assert run("publish", str(share)) == dbcli.OK
    first, second = run.output.splitlines()
    assert first.endswith(f" bytes, 0 committed transactions, schema version {db.latest()}.")
    assert second == "  with it: 1 transactions of 1 staged statement, which the pages do not show"
    run.load()
    assert run("publish", str(share)) == dbcli.OK
    assert " of 7 staged statements, which the pages do not show\n" in run.output


def test_publish_takes_the_folder_from_the_config_unless_one_is_named(
    closed: Run, share: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert closed("publish") == dbcli.FAILED
    assert closed.output == (
        f"error: no folder to publish to: name one, or set 'publish' in {closed.config}\n"
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    with closed.config.open("a", encoding="utf-8") as config:
        config.write("publish: ~/share\n")
    assert closed("publish") == dbcli.OK
    assert closed.output.startswith(f"Published {share / 'finanze.db'}: ")
    other = tmp_path / "other"
    other.mkdir()
    assert closed("publish", str(other)) == dbcli.OK
    assert (other / "finanze.db").exists()

    closed.config.write_text(f"database: {closed.database}\npublish: [a, b]\n", encoding="utf-8")
    assert closed("publish") == dbcli.FAILED
    assert closed.output == (
        f"error: {closed.config}: 'publish' must be the folder the copy goes to\n"
    )


def test_publish_refuses_a_folder_that_is_missing_or_holds_the_database(
    closed: Run, share: Path
) -> None:
    unmounted = share / "nas"
    assert closed("publish", str(unmounted)) == dbcli.FAILED
    assert closed.output == (
        f"error: {unmounted}: no such folder: nothing is published to a share that is not mounted\n"
    )
    assert not unmounted.exists()
    assert closed("publish", str(closed.database.parent)) == dbcli.FAILED
    assert "the database itself is kept there: publish its copy somewhere else" in closed.output


def test_publish_does_not_make_a_database_where_there_is_none(run: Run, share: Path) -> None:
    assert run("publish", str(share)) == dbcli.FAILED
    assert run.output == f"error: {run.database}: no database to publish\n"
    assert not run.database.parent.exists()
    assert list(share.iterdir()) == []


def test_what_publish_leaves_is_what_a_read_only_serve_shows(
    closed: Run, share: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert closed("publish", str(share)) == dbcli.OK
    shown = Run(share)  # the other machine: its own config, the copy and nothing else
    shown.database = share / "finanze.db"
    (how,) = serve_with(shown, monkeypatch, "{read_only: true}")
    app = how["app"]
    assert isinstance(app, Starlette)
    assert app.state.database == share / "finanze.db"
    assert shown.output.endswith("nothing here changes it\n")


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
    # The config may name the instruments, as the flag does.
    closed.config.write_text(
        CONFIG.format(database=closed.database) + f"instruments: {closed.instruments}\n",
        encoding="utf-8",
    )
    assert closed("prices", "--through", "2026-09-30") == dbcli.OK
    assert closed.output == "4 closes, 4 exchange rates and 0 splits stored.\n"
    # Nothing is fetched twice, and no owner is needed: prices are everyone's.
    closed.config.write_text(f"database: {closed.database}\n", encoding="utf-8")
    paths = ["--instruments", str(closed.instruments)]
    assert closed("prices", "--through", "2026-09-30", *paths) == dbcli.OK
    assert closed.output == "0 closes, 0 exchange rates and 0 splits stored.\n"


def test_prices_lists_what_it_could_not_fetch_and_fetches_up_to_yesterday(closed: Run) -> None:
    # Today's close is not final while a market is open, and it would never be fetched again.
    yesterday = NOW.date() - timedelta(days=1)
    closed.source = FakeSource({"USDCHF=X": [(yesterday, "0.8"), (NOW.date(), "0.9")]})
    assert closed("prices", "--instruments", str(closed.instruments)) == dbcli.NEEDS_REVIEW
    lines = closed.output.splitlines()
    assert lines[0] == "0 closes, 1 exchange rates and 0 splits stored."
    assert "  missing: ACME: no prices (ACME: No data found)" in lines
    assert "  missing: EUR: no rates (EURCHF=X: No data found)" in lines
    assert {asked[2] for asked in closed.source.asked} == {yesterday}
    closed.source.asked.clear()
    closed("prices", "--through", "2026-10-20", "--instruments", str(closed.instruments))
    assert max(asked[2] for asked in closed.source.asked) == yesterday  # nor a day to come


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


def kept(run: Run) -> list[str]:
    folder = run.database.parent / "originals"
    return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []


def test_import_keeps_the_file_of_every_statement_under_its_digest(run: Run) -> None:
    assert run.load() == dbcli.OK
    digests = {
        hashlib.sha256((run.folder / name).read_bytes()).hexdigest() + Path(name).suffix
        for name in FILES
    }
    assert set(kept(run)) == digests  # the statements, and none of the files skipped
    assert run.load() == dbcli.OK  # the same files again: nothing more to keep
    assert set(kept(run)) == digests


def test_an_import_that_stores_nothing_keeps_nothing(run: Run) -> None:
    only = run.folder / "old" / "only"
    only.mkdir()
    migros = only / "Migros.csv"
    text = (run.folder / "Migros.csv").read_text(encoding="utf-8")
    migros.write_text(text.replace("-1288.18", "-1288.19"), encoding="utf-8")
    assert run.load(folder=only) == dbcli.FAILED
    assert kept(run) == []


def test_keep_takes_in_the_files_of_statements_imported_earlier(run: Run) -> None:
    assert run.load() == dbcli.OK
    shutil.rmtree(run.database.parent / "originals")  # as after an import of before
    assert run("keep", str(run.folder / "Migros.csv"), str(run.folder / "notes.txt")) == dbcli.OK
    assert f"1 of 2 files kept in {run.database.parent / 'originals'}." in run.output
    assert "  no original: IBKR.csv\n" in run.output
    assert "no original: Migros.csv" not in run.output
    assert len(kept(run)) == 1
    assert run("keep", str(run.folder)) == dbcli.OK
    assert run.output.startswith("5 of ")  # the sixth is kept already
    assert "no original" not in run.output
    assert len(kept(run)) == len(FILES)


def test_logo_keeps_an_image_for_an_instrument_and_drops_it(run: Run) -> None:
    assert run.load() == dbcli.OK
    image = run.folder / "widg.svg"
    image.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    assert run("logo", "WIDG", str(image)) == dbcli.OK
    assert run.output == "WIDG: image/svg+xml logo kept (41 bytes).\n"
    conn = db.connect(run.database)
    where = "SELECT source FROM logos WHERE symbol = 'WIDG'"
    assert conn.execute(where).fetchone()[0] == "widg.svg"
    assert run("logo", "WIDG", str(image), "--source", "the issuer's site") == dbcli.OK
    assert conn.execute(where).fetchone()[0] == "the issuer's site"
    conn.close()
    assert run("logo", "WIDG", "--remove") == dbcli.OK
    assert run.output == "WIDG: logo removed.\n"


def test_logo_refuses_what_it_cannot_keep(run: Run) -> None:
    assert run.load() == dbcli.OK
    image = run.folder / "notes.txt"
    assert run("logo", "WIDG", str(image)) == dbcli.FAILED
    assert "neither a PNG nor an SVG" in run.output
    assert run("logo", "WIDG", str(run.folder / "missing.svg")) == dbcli.FAILED
    assert "missing.svg" in run.output
    assert run("logo", "WIDG") == dbcli.FAILED
    assert "name the file, or --remove" in run.output
    assert run("logo", "WIDG", "--remove") == dbcli.FAILED
    assert "no logo to remove" in run.output


def test_an_export_that_disagrees_with_a_committed_month_says_where(run: Run) -> None:
    savings = run.folder / "old" / "savings" / "Revolut Savings.tsv"
    savings.parent.mkdir(parents=True)
    lines = (run.folder / "Revolut Savings.tsv").read_text(encoding="utf-8").splitlines(True)
    savings.write_text("".join(lines), encoding="utf-8")
    assert run.load(folder=savings.parent) == dbcli.OK
    assert run("commit") == dbcli.OK
    short = [line for line in lines if not line.startswith("Sep 29, 2026, 2:09")]
    savings.write_text("".join(short), encoding="utf-8")
    assert run.load(folder=savings.parent) == dbcli.OK
    assert "  differs: Revolut EUR 2026-09-29: " in run.output
    assert "is committed and is not in Revolut Savings.tsv\n" in run.output
    assert "nothing committed was changed: take the statement back" in run.output


def test_keys_of_an_older_config_are_ignored(run: Run) -> None:
    run.config.write_text(
        run.config.read_text(encoding="utf-8") + "locale: it\naccounts:\n  Tastytrade: Tasty\n",
        encoding="utf-8",
    )
    assert run.load() == dbcli.OK


# ------------------------------------------------------------------ which setup


def _setup(root: Path, name: str) -> Path:
    """users/<name>/ with a config naming its database in it, as `finanze init` writes it."""
    home = root / "users" / name
    (home / "statements").mkdir(parents=True)
    database = f"users/{name}/finanze.db"
    (home / "finanze.yaml").write_text(CONFIG.format(database=database), encoding="utf-8")
    return home


def _main(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    code = dbcli.main(list(argv), out, now=lambda: NOW)
    return code, out.getvalue()


def test_the_only_setup_in_users_is_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _setup(tmp_path, "lukas")
    write_rules(home)
    (home / "instruments.yaml").write_text(INSTRUMENTS_YAML + MATURED, encoding="utf-8")
    shutil.copy(FIXTURES / FILES["Migros.csv"], home / "statements" / "Migros.csv")
    config = home / "finanze.yaml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "rules: [users/lukas/rules.yaml]\ninstruments: users/lukas/instruments.yaml\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    code, output = _main("import", "users/lukas/statements")  # no --config
    assert code == dbcli.OK, output
    assert "  staged Migros.csv (Migros Bank account statement)\n" in output
    assert (home / "finanze.db").is_file()
    assert (home / "originals").is_dir()


def test_with_several_setups_one_is_chosen_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("anna", "Lukas"):
        _setup(tmp_path, name)
    monkeypatch.chdir(tmp_path)
    assert _main("statements") == (
        dbcli.FAILED,
        "error: users/ holds several setups (Lukas, anna): choose one with --user\n",
    )
    # A folder's own name, whatever its case, and what `finanze init` makes of a name.
    for named in ("Lukas", "lukas", "LUKAS"):
        assert _main("statements", "--user", named) == (dbcli.OK, "No statements for Lukas.\n")
    assert (tmp_path / "users" / "Lukas" / "finanze.db").is_file()
    monkeypatch.setenv(dbcli.USER, "anna")
    assert _main("statements")[0] == dbcli.OK
    assert (tmp_path / "users" / "anna" / "finanze.db").is_file()
    assert _main("statements", "--user", "zoe") == (
        dbcli.FAILED,
        "error: no setup 'zoe' in users/ (there are Lukas, anna): make one with 'finanze init'\n",
    )


def test_the_config_here_comes_before_a_setup_in_users(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "finanze.yaml").write_text(CONFIG.format(database="data/f.db"), encoding="utf-8")
    _setup(tmp_path, "anna")
    monkeypatch.chdir(tmp_path)
    assert _main("statements") == (dbcli.OK, "No statements for Lukas.\n")
    assert (tmp_path / "data" / "f.db").is_file()
    assert not (tmp_path / "users" / "anna" / "finanze.db").exists()
    assert _main("statements", "--user", "anna")[0] == dbcli.OK  # unless --user says otherwise
    assert (tmp_path / "users" / "anna" / "finanze.db").is_file()


def test_without_users_the_config_here_is_read_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "finanze.yaml").write_text(CONFIG.format(database="data/f.db"), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert _main("statements") == (dbcli.OK, "No statements for Lukas.\n")
    assert (tmp_path / "data" / "f.db").is_file()
    monkeypatch.setenv(dbcli.USER, "lukas")  # names a setup that is not there
    assert _main("statements")[1].startswith("error: no setup 'lukas' in users/ (there is none")


@pytest.mark.parametrize(("currency", "shown"), [("chf", "CHF"), ("USD", "USD")])
def test_the_pages_show_the_currency_of_the_config(
    run: Run, monkeypatch: pytest.MonkeyPatch, currency: str, shown: str
) -> None:
    run.config.write_text(
        run.config.read_text(encoding="utf-8") + f"currency: {currency}\n", encoding="utf-8"
    )
    served: list[Starlette] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **_: served.append(app))
    assert run("serve") == dbcli.OK
    assert served[-1].state.currency == shown


@pytest.mark.parametrize("currency", ["XAU", "[EUR]", "1"])
def test_a_currency_the_pages_do_not_offer_is_refused(run: Run, currency: str) -> None:
    run.config.write_text(
        run.config.read_text(encoding="utf-8") + f"currency: {currency}\n", encoding="utf-8"
    )
    assert run("serve") == dbcli.FAILED
    assert run.output.endswith(
        f"error: {run.config}: 'currency' must be one of EUR, CHF, USD, GBP, JPY\n"
    )


def test_unmatched_must_name_a_category(run: Run) -> None:
    run.config.write_text(
        run.config.read_text(encoding="utf-8") + "unmatched: [a]\n", encoding="utf-8"
    )
    assert run.load() == dbcli.FAILED
    assert run.output.endswith(f"error: {run.config}: 'unmatched' must be the name of a category\n")
