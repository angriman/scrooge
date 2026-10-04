from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from finanze import categories
from finanze.classification import (
    ConfigError,
    Instrument,
    UnknownInstrumentError,
    append_rule,
    classify,
    load_instruments,
    load_rules,
    resolve_instruments,
    uncategorized,
)
from finanze.models import Kind, SecurityTrade, Transaction


def _txn(
    description: str,
    amount: str = "-10",
    *,
    kind: Kind = Kind.EXPENSE,
    category: str = "",
    account: str = "Migros",
    counterparty: str = "",
) -> Transaction:
    return Transaction(
        date=date(2026, 9, 1),
        account=account,
        amount=Decimal(amount),
        currency="CHF",
        description=description,
        kind=kind,
        source_file="f",
        source_ref="1",
        counterparty=counterparty,
        category=category,
    )


def _trade(ticker: str, asset_tab: str = "") -> SecurityTrade:
    return SecurityTrade(
        date=date(2026, 9, 1),
        account="IBKR USD",
        amount=Decimal(-100),
        currency="USD",
        description="Buy",
        kind=Kind.SECURITY_TRADE,
        source_file="f",
        source_ref="1",
        category="Investimenti",
        ticker=ticker,
        quantity=Decimal(1),
        price=Decimal(100),
        cost=Decimal(100),
        asset_tab=asset_tab,
    )


def _rules(tmp_path: Path, text: str, name: str = "rules.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_taxonomy_has_no_duplicates_and_keeps_sheet_order() -> None:
    assert len(categories.ORDER) == len(categories.ALL)
    assert categories.ORDER[:2] == ("Affitto", "Utenze")
    assert {"Trading", "Commissioni", "Cashback", "Topup IBKR"} <= categories.ALL


def test_first_matching_rule_wins_and_matching_ignores_case(tmp_path: Path) -> None:
    rules = load_rules(
        [
            _rules(
                tmp_path,
                "rules:\n"
                "  - {match: 'frischmarkt|konsum', category: Spesa}\n"
                "  - {match: 'FRISCHMARKT', category: Ristoranti}\n"
                "  - {match: 'acme', category: Stipendio, kind: income}\n",
            )
        ]
    )
    out = classify([_txn("FRISCHMARKT ZENTRUM"), _txn("x", counterparty="Konsum-12")], rules)
    assert [t.category for t in out] == ["Spesa", "Spesa"]
    salary = classify([_txn("ACME AG", "7200", kind=Kind.EXPENSE)], rules)[0]
    assert (salary.category, salary.kind) == ("Stipendio", Kind.INCOME)


def test_rules_never_touch_categorized_or_internal_rows(tmp_path: Path) -> None:
    rules = load_rules([_rules(tmp_path, "rules:\n  - {match: '.', category: Spesa}\n")])
    kept = _txn("x", category="Trading", kind=Kind.TRADING)
    internal = _txn("Swisscard AECS", kind=Kind.INTERNAL)
    assert classify([kept, internal], rules) == (kept, internal)


def test_rule_can_be_limited_to_an_account(tmp_path: Path) -> None:
    text = "rules:\n  - {match: 'x', category: Spesa, account: Revolut EUR}\n"
    rules = load_rules([_rules(tmp_path, text)])
    out = classify([_txn("x"), _txn("x", account="Revolut EUR")], rules)
    assert [t.category for t in out] == ["", "Spesa"]


def test_earlier_rule_files_take_precedence_and_missing_files_are_skipped(tmp_path: Path) -> None:
    local = _rules(tmp_path, "rules:\n  - {match: 'bar', category: Regali}\n", "local.yaml")
    shared = _rules(tmp_path, "rules:\n  - {match: 'bar', category: Ristoranti}\n")
    rules = load_rules([tmp_path / "missing.yaml", local, shared])
    assert classify([_txn("Bar Sport")], rules)[0].category == "Regali"


def test_uncategorized_lists_rows_that_block_the_write() -> None:
    rows = [_txn("a"), _txn("b", category="Spesa"), _txn("c", kind=Kind.INTERNAL)]
    assert [t.description for t in uncategorized(rows)] == ["a"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("- not a mapping\n", "expected a mapping"),
        ("rules: {a: 1}\n", "top-level 'rules' list"),
        ("rules:\n  - {match: 'x'}\n", "needs 'match' and 'category'"),
        ("rules:\n  - just text\n", "needs 'match' and 'category'"),
        ("rules:\n  - {match: 'x', category: Vacanze}\n", "unknown category 'Vacanze'"),
        ("rules:\n  - {match: '(', category: Spesa}\n", "rule 1"),
        ("rules:\n  - {match: 'x', category: Spesa, kind: gift}\n", "rule 1"),
    ],
)
def test_malformed_rules_raise(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_rules([_rules(tmp_path, text)])


def test_empty_rules_file_is_fine(tmp_path: Path) -> None:
    assert load_rules([_rules(tmp_path, "# nothing yet\n")]) == ()
    assert load_rules([_rules(tmp_path, "rules: []\n")]) == ()


def test_instruments_override_the_tab_and_the_sheet_ticker(tmp_path: Path) -> None:
    path = _rules(
        tmp_path,
        "instruments:\n  B26A: {tab: ETFs, ticker: CEBE}\n  VT:\n  GOOG: {tab: Stocks}\n",
        "instruments.yaml",
    )
    instruments = load_instruments(path)
    assert instruments == {
        "B26A": Instrument("ETFs", "CEBE"),
        "VT": Instrument(),
        "GOOG": Instrument("Stocks"),
    }
    plain = _txn("x", category="Spesa")
    out = resolve_instruments(
        [_trade("B26A", "Stocks"), _trade("VT", "ETFs"), _trade("GOOG"), plain], instruments
    )
    assert [(t.ticker, t.asset_tab) for t in out if isinstance(t, SecurityTrade)] == [
        ("CEBE", "ETFs"),
        ("VT", "ETFs"),
        ("GOOG", "Stocks"),
    ]
    assert out[3] is plain


def test_bill_maturity_comes_from_instruments_when_the_statement_lacks_it(
    tmp_path: Path,
) -> None:
    path = _rules(tmp_path, "instruments:\n  912797ZZ1: {maturity: 2026-12-24}\n", "i.yaml")
    instruments = load_instruments(path)
    out = resolve_instruments([_trade("912797ZZ1", "Bonds")], instruments)[0]
    assert isinstance(out, SecurityTrade)
    assert out.maturity == date(2026, 12, 24)
    with pytest.raises(UnknownInstrumentError, match="no maturity date"):
        resolve_instruments([_trade("912797YY2", "Bonds")], instruments)
    with pytest.raises(ConfigError, match="maturity must be a date"):
        load_instruments(_rules(tmp_path, "instruments:\n  X: {maturity: soon}\n", "bad.yaml"))


def test_trade_without_a_tab_blocks(tmp_path: Path) -> None:
    assert load_instruments(tmp_path / "missing.yaml") == {}
    with pytest.raises(UnknownInstrumentError, match="XYZ"):
        resolve_instruments([_trade("XYZ")], {})


def test_malformed_instruments_raise(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown tab 'Crypto'"):
        load_instruments(_rules(tmp_path, "instruments:\n  BTC: {tab: Crypto}\n"))
    with pytest.raises(ConfigError, match="top-level 'instruments' dict"):
        load_instruments(_rules(tmp_path, "instruments: [VT]\n"))


def test_rule_can_be_limited_to_a_direction(tmp_path: Path) -> None:
    text = "rules:\n  - {match: 'rossi', category: Rimborsi, direction: in}\n"
    rules = load_rules([_rules(tmp_path, text)])
    out = classify([_txn("TWINT Rossi", "65"), _txn("TWINT Rossi", "-20")], rules)
    assert [t.category for t in out] == ["Rimborsi", ""]
    with pytest.raises(ConfigError, match="direction must be 'in' or 'out'"):
        load_rules([_rules(tmp_path, "rules:\n  - {match: x, category: Spesa, direction: up}\n")])


@pytest.mark.parametrize(
    "existing",
    [None, "", "# mine\n", "rules: []\n", "rules:\n  - {match: a, category: Spesa}\n"],
)
def test_appended_rules_load_and_match_literally(tmp_path: Path, existing: str | None) -> None:
    path = tmp_path / "rules.local.yaml"
    if existing is not None:
        path.write_text(existing, encoding="utf-8")
    name = 'SUMUP *CAFÉ (1+1) "X"'
    append_rule(path, name, "out", "Attività/Svago")
    append_rule(path, "Rossi, Maria", "in", "Rimborsi")
    rules = load_rules([path])
    assert [(r.category, r.direction) for r in rules[-2:]] == [
        ("Attività/Svago", "out"),
        ("Rimborsi", "in"),
    ]
    assert classify([_txn(name)], rules[-2:])[0].category == "Attività/Svago"
    assert classify([_txn("SUMUP XCAFÉ 111 X")], rules[-2:])[0].category == ""
