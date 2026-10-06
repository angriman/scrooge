import csv
import io
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from finanze import cli, records
from finanze.models import Kind, Transaction
from tests.conftest import INSTRUMENTS_YAML, write_rules

CONFIG = "accounts:\n  Tastytrade: Tasty\n"


def write_instruments(folder: Path) -> Path:
    path = folder / "instruments.yaml"
    path.write_text(INSTRUMENTS_YAML, encoding="utf-8")
    return path


class Run:
    """Runs the CLI on a folder of statements and records what happened."""

    def __init__(self, folder: Path, answer: str = "y") -> None:
        self.folder = folder
        self.answer = answer
        self.saved = folder / "out" / "2026-09"
        self.questions: list[str] = []
        self.output = ""

    def __call__(self, *args: str, rules: bool = True, config: bool = True) -> int:
        argv = [str(self.folder), "--instruments", str(write_instruments(self.folder))]
        argv += ["--rules", str(write_rules(self.folder) if rules else self.folder / "no.yaml")]
        path = self.folder / "finanze.yaml"
        if config:
            path.write_text(CONFIG, encoding="utf-8")
        argv += ["--config", str(path), "--output", str(self.folder / "out"), *args]
        out = io.StringIO()
        code = cli.main(argv, out=out, ask=self._ask)
        self.output = out.getvalue()
        return code

    def _ask(self, question: str) -> str:
        self.questions.append(question)
        return self.answer

    def pasted(self, name: str) -> list[list[str]]:
        text = (self.saved / name).read_text(encoding="utf-8")
        return [line.split("\t") for line in text.splitlines()]


@pytest.fixture
def run(folder: Path) -> Run:
    return Run(folder)


def test_dry_run_prints_the_month_and_saves_nothing(run: Run) -> None:
    assert run() == cli.OK
    assert "September 2026: 6 statements, all reconciled" in run.output
    assert "skipped ibkr.pdf (not a statement)" in run.output
    assert "note: Revolut EUR includes rows with no balance" in run.output
    assert "Uscite\n" in run.output
    assert "Affitto                    Migros              1850.00" in run.output
    assert "Trading                    Tasty " in run.output  # renamed by the config
    assert "ETFs    2026-09-23  WIDG" in run.output
    assert "Dry run: nothing saved" in run.output
    assert not (run.folder / "out").exists()


def test_uncategorized_rows_are_listed_and_block(run: Run) -> None:
    assert run("--write", rules=False) == cli.NEEDS_REVIEW
    assert "transactions need a category" in run.output
    assert "Hans Muster, Beispielweg 8, 5000 Aarau | Affitto settembre" in run.output
    # the transactions are saved for inspection, but nothing that could be pasted
    assert [path.name for path in run.saved.iterdir()] == ["transactions.csv"]


def test_write_saves_the_files_to_paste_and_says_where_they_go(run: Run) -> None:
    assert run("--write") == cli.OK
    assert run.questions == []
    assert f"Saved in {run.saved} (tab-separated, no header, 'it' numbers):" in run.output
    assert "rows -> Budget Mensile!A5" in run.output
    assert "rows -> Budget Mensile!F5" in run.output
    assert "stocks.tsv             1 rows -> Stocks: below the log that starts at A13" in run.output
    assert "Set Budget Mensile!A1 to 2026-09-01" in run.output
    assert sorted(path.name for path in run.saved.iterdir()) == [
        "bonds.tsv",
        "budget_entrate.tsv",
        "budget_uscite.tsv",
        "etfs.tsv",
        "stocks.tsv",
        "transactions.csv",
    ]
    assert run.pasted("budget_uscite.tsv")[0] == ["Affitto", "Migros", "1850,00"]
    assert run.pasted("stocks.tsv") == [["ACME", "2026-09-15", "20", "USD"]]
    assert ["WIDG", "2026-09-23", "-10", "54,25", "-542,5", "USD"] in run.pasted("etfs.tsv")
    assert any(row[0] == "US-T Govt Bill Dec24'26" for row in run.pasted("bonds.tsv"))


def test_a_rerun_saves_the_same_files(run: Run) -> None:
    assert run("--write") == cli.OK
    first = {path.name: path.read_bytes() for path in run.saved.iterdir()}
    assert run("--write") == cli.OK
    assert {path.name: path.read_bytes() for path in run.saved.iterdir()} == first


def test_locale_from_the_config_sets_the_decimal_mark(run: Run) -> None:
    (run.folder / "finanze.yaml").write_text(CONFIG + "locale: en\n", encoding="utf-8")
    assert run("--write", config=False) == cli.OK
    assert "'en' numbers" in run.output
    assert run.pasted("budget_uscite.tsv")[0] == ["Affitto", "Migros", "1850.00"]


def test_unknown_locale_is_an_error(run: Run) -> None:
    (run.folder / "finanze.yaml").write_text("locale: de\n", encoding="utf-8")
    assert run("--write", config=False) == cli.FAILED
    assert "'locale' must be one of it, en" in run.output
    assert not (run.folder / "out").exists()


def test_dump_saves_the_normalized_transactions(run: Run) -> None:
    assert run("--dump") == cli.OK
    target = run.saved / "transactions.csv"
    assert f"saved {target}" in run.output
    assert [path.name for path in run.saved.iterdir()] == ["transactions.csv"]
    with target.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert tuple(rows[0]) == records.FIELDS
    rent = next(row for row in rows if row["category"] == "Affitto")
    assert (rent["account"], rent["amount"], rent["kind"]) == ("Migros", "-1850.00", "expense")
    assert (rent["id"], rent["ticker"]) == (f"Migros.csv:{rent['source_ref']}", "")
    bill = next(row for row in rows if row["asset_tab"] == "Bonds" and row["account"] == "Tasty")
    assert (bill["ticker"], bill["maturity"]) == ("912797ZZ1", "2026-12-24")
    # older runs saved the dump next to the statements: it must not be taken for one
    target.rename(run.folder / "transactions_2026-09.csv")
    assert run() == cli.OK
    assert "skipped transactions_2026-09.csv (not a statement)" in run.output


def test_failed_dump_is_reported_and_keeps_the_previous_file(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run("--dump") == cli.OK
    target = run.saved / "transactions.csv"
    before = target.read_text(encoding="utf-8")

    def refuse(_: object) -> list[records.Record]:
        msg = "more than one transaction has the id 'Migros.csv:3'"
        raise records.DuplicateRecordError(msg)

    monkeypatch.setattr(records, "records", refuse)
    assert run("--dump") == cli.FAILED
    assert "error: more than one transaction has the id" in run.output
    assert target.read_text(encoding="utf-8") == before


def test_failures_are_reported_and_nothing_is_saved(run: Run) -> None:
    migros = run.folder / "Migros.csv"
    migros.write_text(migros.read_text(encoding="utf-8").replace('"-4.40"', '"-4.50"'))
    assert run("--write") == cli.FAILED
    assert "error: Migros.csv: Migros row 11: balance" in run.output
    assert not (run.folder / "out").exists()


def test_malformed_config_is_an_error(run: Run) -> None:
    (run.folder / "finanze.yaml").write_text("accounts: [a, b]\n", encoding="utf-8")
    assert run(config=False) == cli.FAILED
    assert "expected a mapping" in run.output
    (run.folder / "finanze.yaml").write_text("", encoding="utf-8")
    assert run(config=False) == cli.OK  # an empty config is fine


def test_review_saves_answers_as_rules_and_then_proceeds(run: Run) -> None:
    rules = write_rules(run.folder)
    catch_all = "  - {match: '.', category: Regali}\n"
    rules.write_text(rules.read_text(encoding="utf-8").replace(catch_all, ""), encoding="utf-8")
    answers = iter(["1", "nonsense", "rimborsi", "", "q"])
    asked: list[str] = []

    def ask(question: str) -> str:
        asked.append(question)
        return next(answers)

    out = io.StringIO()
    argv = [str(run.folder), "--rules", str(rules), "--review"]
    argv += ["--instruments", str(write_instruments(run.folder))]
    argv += ["--config", str(run.folder / "no")]
    assert cli.main(argv, out=out, ask=ask) == cli.NEEDS_REVIEW
    text = out.getvalue()
    assert "Categories: 1=Affitto  2=Utenze" in text
    assert "unknown category 'nonsense'" in asked[2]
    assert len(asked) == 5  # two answered (one retried), one skipped, then q
    saved = rules.read_text(encoding="utf-8").splitlines()[-2:]
    assert saved[0].endswith('category: "Affitto", direction: in}')
    assert saved[1].endswith('category: "Rimborsi", direction: in}')
    assert f"saved to {rules}" in text
    assert "transactions need a category" in text  # the skipped ones still block


def test_review_until_everything_has_a_category(run: Run) -> None:
    rules = run.folder / "fresh.yaml"
    out = io.StringIO()
    argv = [str(run.folder), "--rules", str(rules), "--review"]
    argv += ["--instruments", str(write_instruments(run.folder))]
    argv += ["--config", str(run.folder / "no")]
    assert cli.main(argv, out=out, ask=lambda _: "Regali") == cli.OK
    assert "Dry run: nothing saved" in out.getvalue()
    assert rules.read_text(encoding="utf-8").startswith("rules:\n  - {match: ")
    # the saved rules are enough on their own the next time
    out = io.StringIO()
    argv.remove("--review")
    assert cli.main(argv, out=out, ask=lambda _: "q") == cli.OK


def test_review_never_saves_a_rule_for_rows_without_text(tmp_path: Path) -> None:
    blank = Transaction(
        date=date(2026, 9, 1),
        account="Revolut CHF",
        amount=Decimal("-5.00"),
        currency="CHF",
        description="",
        kind=Kind.EXPENSE,
        source_file="Revolut CHF.tsv",
        source_ref="1",
    )
    rules = tmp_path / "rules.local.yaml"
    out = io.StringIO()
    asked: list[str] = []

    def ask(question: str) -> str:
        asked.append(question)
        return "Regali"

    cli._review([blank, replace(blank, description=" ")], rules, out, ask)
    assert asked == []
    assert not rules.exists()
    assert "2 rows have no text to match" in out.getvalue()


def test_unplaceable_instrument_is_an_error(run: Run) -> None:
    (run.folder / "instruments.yaml").write_text("instruments: {}\n", encoding="utf-8")
    argv = [str(run.folder), "--instruments", str(run.folder / "instruments.yaml")]
    out = io.StringIO()
    assert cli.main(argv, out=out, ask=run._ask) == cli.FAILED
    assert "to instruments.yaml" in out.getvalue()
