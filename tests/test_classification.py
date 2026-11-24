import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from scrooge import categories
from scrooge.classification import (
    Instrument,
    UnknownInstrumentError,
    UnknownMaturityError,
    add_maturities,
    append_rule,
    between_people,
    classify,
    load_instruments,
    load_rules,
    personal_file,
    resolve_instruments,
    uncategorized,
    unmatched,
)
from scrooge.errors import ConfigError
from scrooge.models import Kind, SecurityTrade, Transaction


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
        category="Investments",
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


def test_taxonomy_names_are_unique_where_they_must_be_and_keep_sheet_order() -> None:
    expense_leaves = [leaf for leaves in categories.USCITE.values() for leaf in leaves]
    # A leaf sits under one top-level category only, or its group would be ambiguous.
    assert len(expense_leaves) == len(set(expense_leaves))
    assert len(categories.ENTRATE) == len(set(categories.ENTRATE))
    assert len(categories.TRANSFERS) == len(set(categories.TRANSFERS))
    assert all(leaves for leaves in categories.USCITE.values())
    # Transfers are neither expenses nor income; a few leaves are both, and only those.
    assert not set(categories.TRANSFERS) & (set(expense_leaves) | set(categories.ENTRATE))
    both = set(expense_leaves) & set(categories.ENTRATE)
    assert both == {"Trading", "Fees", "Investments", categories.UNSORTED}
    # ORDER lists every leaf once: a leaf on both sides is written once, where it first appears.
    assert len(categories.ORDER) == len(set(categories.ORDER))
    assert set(categories.ORDER) == set(expense_leaves) | set(categories.ENTRATE) | set(
        categories.TRANSFERS
    )
    assert len(categories.ORDER) == len(expense_leaves) + len(categories.ENTRATE) + len(
        categories.TRANSFERS
    ) - len(both)
    assert categories.ORDER[:3] == ("Rent", "Utilities", "Household")
    assert categories.ORDER.index("Trading") < categories.ORDER.index("Salary")
    assert categories.ORDER[-1] == "Own transfer"
    assert frozenset(categories.ORDER) == categories.ALL
    assert {"Electronics", "Flights", "Donations", "Taxes", "Sport"} <= categories.ALL
    assert {"Trading", "Fees", "Cashback", "IBKR top-up"} <= categories.ALL


def test_first_matching_rule_wins_and_matching_ignores_case(tmp_path: Path) -> None:
    rules = load_rules(
        [
            _rules(
                tmp_path,
                "rules:\n"
                "  - {match: 'frischmarkt|konsum', category: Groceries}\n"
                "  - {match: 'FRISCHMARKT', category: Eating out}\n"
                "  - {match: 'acme', category: Salary, kind: income}\n",
            )
        ]
    )
    out = classify([_txn("FRISCHMARKT ZENTRUM"), _txn("x", counterparty="Konsum-12")], rules)
    assert [t.category for t in out] == ["Groceries", "Groceries"]
    salary = classify([_txn("ACME AG", "7200", kind=Kind.EXPENSE)], rules)[0]
    assert (salary.category, salary.kind) == ("Salary", Kind.INCOME)


def test_rules_never_touch_categorized_or_internal_rows(tmp_path: Path) -> None:
    rules = load_rules([_rules(tmp_path, "rules:\n  - {match: '.', category: Groceries}\n")])
    kept = _txn("x", category="Trading", kind=Kind.TRADING)
    internal = _txn("Swisscard AECS", kind=Kind.INTERNAL)
    assert classify([kept, internal], rules) == (kept, internal)


def test_rule_can_be_limited_to_an_account(tmp_path: Path) -> None:
    text = "rules:\n  - {match: 'x', category: Groceries, account: Revolut EUR}\n"
    rules = load_rules([_rules(tmp_path, text)])
    out = classify([_txn("x"), _txn("x", account="Revolut EUR")], rules)
    assert [t.category for t in out] == ["", "Groceries"]


def test_earlier_rule_files_take_precedence_and_missing_files_are_skipped(tmp_path: Path) -> None:
    local = _rules(tmp_path, "rules:\n  - {match: 'bar', category: Gifts}\n", "local.yaml")
    shared = _rules(tmp_path, "rules:\n  - {match: 'bar', category: Eating out}\n")
    rules = load_rules([tmp_path / "missing.yaml", local, shared])
    assert classify([_txn("Bar Sport")], rules)[0].category == "Gifts"


def test_uncategorized_lists_rows_that_block_the_write() -> None:
    rows = [_txn("a"), _txn("b", category="Groceries"), _txn("c", kind=Kind.INTERNAL)]
    assert [t.description for t in uncategorized(rows)] == ["a"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("- not a mapping\n", "expected a mapping"),
        ("rules: {a: 1}\n", "top-level 'rules' list"),
        ("rules:\n  - {match: 'x'}\n", "needs 'match' and 'category'"),
        ("rules:\n  - just text\n", "needs 'match' and 'category'"),
        ("rules:\n  - {match: 'x', category: Vacanze}\n", "unknown category 'Vacanze'"),
        ("rules:\n  - {match: '(', category: Groceries}\n", "rule 1"),
        ("rules:\n  - {match: 'x', category: Groceries, kind: gift}\n", "rule 1"),
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
    plain = _txn("x", category="Groceries")
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


def test_every_bill_without_a_maturity_is_named_at_once() -> None:
    trades = [
        _trade("912797YY2", "Bonds"),
        _trade("912797XX3", "Bonds"),
        _trade("912797YY2", "Bonds"),
    ]
    with pytest.raises(UnknownMaturityError, match="912797YY2, 912797XX3: no maturity") as error:
        resolve_instruments(trades, {})
    assert error.value.bills == ("912797YY2", "912797XX3")


COMMENTED = """\
# What is held.
instruments:
    BIL: {tab: ETFs}   # an ETF
    # A bill:
    912797ZZ1: {maturity: 2026-12-24}
"""


def test_maturities_are_written_first_under_instruments_and_the_rest_is_kept(
    tmp_path: Path,
) -> None:
    path = _rules(tmp_path, COMMENTED, "instruments.yaml")
    add_maturities(path, {"912797XX3": date(2026, 11, 27), "912797YY2": date(2027, 2, 9)})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[:4] == [
        "# What is held.",
        "instruments:",
        "    912797XX3: {maturity: 2026-11-27}",  # indented like the entries already there
        "    912797YY2: {maturity: 2027-02-09}",
    ]
    assert lines[4:] == COMMENTED.splitlines()[2:]
    instruments = load_instruments(path)
    assert instruments["912797YY2"].maturity == date(2027, 2, 9)
    assert instruments["BIL"].tab == "ETFs"


def test_maturities_start_a_missing_instruments_file(tmp_path: Path) -> None:
    path = tmp_path / "instruments.yaml"
    add_maturities(path, {"912797XX3": date(2026, 11, 27)})
    assert path.read_text(encoding="utf-8") == "instruments:\n  912797XX3: {maturity: 2026-11-27}\n"
    empty = _rules(tmp_path, "# nothing yet\ninstruments:\n", "empty.yaml")
    add_maturities(empty, {"912797XX3": date(2026, 11, 27)})
    assert load_instruments(empty)["912797XX3"].maturity == date(2026, 11, 27)


def test_maturities_are_not_written_where_they_would_not_be_read(tmp_path: Path) -> None:
    flow = _rules(tmp_path, "instruments: {BIL: {tab: ETFs}}\n", "flow.yaml")
    with pytest.raises(ConfigError, match="no line 'instruments:'"):
        add_maturities(flow, {"912797XX3": date(2026, 11, 27)})
    listed = _rules(tmp_path, "instruments:\n  912797XX3: {tab: Bonds}\n", "listed.yaml")
    with pytest.raises(ConfigError, match="912797XX3 listed without a maturity"):
        add_maturities(listed, {"912797XX3": date(2026, 11, 27)})
    assert listed.read_text(encoding="utf-8") == "instruments:\n  912797XX3: {tab: Bonds}\n"


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
    text = "rules:\n  - {match: 'rossi', category: Refunds, direction: in}\n"
    rules = load_rules([_rules(tmp_path, text)])
    out = classify([_txn("TWINT Rossi", "65"), _txn("TWINT Rossi", "-20")], rules)
    assert [t.category for t in out] == ["Refunds", ""]
    with pytest.raises(ConfigError, match="direction must be 'in' or 'out'"):
        load_rules(
            [_rules(tmp_path, "rules:\n  - {match: x, category: Groceries, direction: up}\n")]
        )


@pytest.mark.parametrize(
    "existing",
    [None, "", "# mine\n", "rules: []\n", "rules:\n  - {match: a, category: Groceries}\n"],
)
def test_appended_rules_load_and_match_literally(tmp_path: Path, existing: str | None) -> None:
    path = tmp_path / "rules.local.yaml"
    if existing is not None:
        path.write_text(existing, encoding="utf-8")
    name = 'SUMUP *CAFÉ (1+1) "X"'
    append_rule(path, name, "out", "Activities")
    append_rule(path, "Rossi, Maria", "in", "Refunds")
    rules = load_rules([path])
    assert [(r.category, r.direction) for r in rules[-2:]] == [
        ("Activities", "out"),
        ("Refunds", "in"),
    ]
    assert classify([_txn(name)], rules[-2:])[0].category == "Activities"
    assert classify([_txn("SUMUP XCAFÉ 111 X")], rules[-2:])[0].category == ""
    # the whole text must match: a reviewed name is not a substring rule
    assert classify([_txn(f"{name} ZURICH")], rules[-2:])[0].category == ""
    # ...but spaces the statement pads the text with do not count
    padded = _txn(f"{name}   ", counterparty=f" {name}  ")
    assert classify([padded], rules[-2:])[0].category == "Activities"


def test_a_transfer_category_makes_the_row_a_transfer(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text(
        "rules:\n"
        "  - {match: 'own account', category: Own transfer}\n"
        "  - {match: 'cash machine', category: Withdrawals, kind: expense}\n"
        "  - {match: 'shop', category: Groceries}\n",
        encoding="utf-8",
    )
    rules = load_rules([path])
    rows = [
        _txn("to my own account", "-500", kind=Kind.EXPENSE),
        _txn("from my own account", "500", kind=Kind.INCOME),
        _txn("own account, exchanged", "-3", kind=Kind.FX),  # only income and expense change
        _txn("cash machine", "-100", kind=Kind.EXPENSE),  # what the rule says wins
        _txn("shop", "-20", kind=Kind.EXPENSE),
    ]
    assert [(t.category, t.kind) for t in classify(rows, rules)] == [
        ("Own transfer", Kind.TRANSFER),
        ("Own transfer", Kind.TRANSFER),
        ("Own transfer", Kind.FX),
        ("Withdrawals", Kind.EXPENSE),
        ("Groceries", Kind.EXPENSE),
    ]


def test_an_appended_rule_loads_whatever_characters_the_name_holds(tmp_path: Path) -> None:
    path = tmp_path / "rules.local.yaml"
    # A name encoded twice in the statement holds characters YAML refuses raw.
    name = "B\u00c3\x96R \x7f\ufffe"
    append_rule(path, name, "out", "Trains")
    rules = load_rules([path])
    assert classify([_txn(name)], rules)[0].category == "Trains"
    assert classify([_txn("B\u00c3R")], rules)[0].category == ""


ANNA = (re.compile("ANNA MEIER", re.IGNORECASE),)


def test_each_person_has_a_rules_file_of_their_own_read_first(tmp_path: Path) -> None:
    shared = _rules(
        tmp_path,
        "rules:\n"
        "  - {match: 'from lukas meier', category: Revolut top-up}\n"
        "  - {match: 'konsum', category: Groceries}\n",
    )
    hers = personal_file(shared, "Anna")
    assert hers == tmp_path / "rules.anna.yaml"
    hers.write_text("rules:\n  - {match: 'from lukas meier', category: Refunds}\n", "utf-8")
    rows = [_txn("Transfer from LUKAS MEIER", "50", kind=Kind.INCOME), _txn("Konsum")]
    his = classify(rows, load_rules([personal_file(shared, "Lukas"), shared]))
    assert [t.category for t in his] == ["Revolut top-up", "Groceries"]
    her = classify(rows, load_rules([hers, shared]))
    assert [(t.category, t.kind) for t in her] == [
        ("Refunds", Kind.INCOME),
        ("Groceries", Kind.EXPENSE),
    ]


def test_no_rule_makes_a_transfer_of_money_from_the_other_person(tmp_path: Path) -> None:
    text = (
        "rules:\n"
        "  - {match: 'revolut', category: Revolut top-up}\n"
        "  - {match: 'exchanged', category: Own transfer}\n"
        "  - {match: 'anna', category: Refunds}\n"
    )
    rules = between_people(load_rules([_rules(tmp_path, text)]), ANNA)
    rows = [
        _txn("REVOLUT LTD, London", "-200"),
        _txn("REVOLUT LTD, London", "-200", counterparty="12345, Anna Meier"),
        _txn("Exchanged, ref Anna Meier", "-3", kind=Kind.FX),  # not income or an expense
    ]
    assert [(t.category, t.kind) for t in classify(rows, rules)] == [
        ("Revolut top-up", Kind.TRANSFER),
        ("Refunds", Kind.EXPENSE),
        ("Own transfer", Kind.FX),
    ]


@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        (None, 'rules:\n  - {match: "(?m)^KIOSK$", category: "Gifts", direction: out}\n'),
        ("rules: []\n", 'rules:\n  - {match: "(?m)^KIOSK$", category: "Gifts", direction: out}\n'),
        (
            "# mine\nrules:\n  - {match: 'KIOSK', category: Groceries}\n",
            '# mine\nrules:\n  - {match: "(?m)^KIOSK$", category: "Gifts", direction: out}\n'
            "  - {match: 'KIOSK', category: Groceries}\n",
        ),
    ],
)
def test_a_rule_can_be_put_before_the_ones_already_there(
    tmp_path: Path, existing: str | None, expected: str
) -> None:
    path = tmp_path / "rules.yaml"
    if existing is not None:
        path.write_text(existing, encoding="utf-8")
    append_rule(path, "KIOSK", "out", "Gifts", first=True)
    assert path.read_text(encoding="utf-8") == expected
    assert load_rules([path])[0].category == "Gifts"  # the first to match wins


def test_unmatched_takes_only_expenses_and_income_no_rule_matched() -> None:
    rows = (
        _txn("A SHOP"),
        _txn("A SALARY", "100", kind=Kind.INCOME),
        _txn("DEPOSIT", "1000", kind=Kind.TRANSFER),  # left blank on purpose: review
        _txn("A BAR", category="Eating out"),
        _txn("CARD BILL", kind=Kind.INTERNAL),
    )
    filled = unmatched(rows, categories.UNSORTED)
    assert [t.category for t in filled] == [
        categories.UNSORTED,
        categories.UNSORTED,
        "",
        "Eating out",
        "",
    ]
    assert [t.kind for t in filled] == [t.kind for t in rows]
    assert uncategorized(filled) == (rows[2],)


def test_unsorted_is_a_built_in_category_for_expenses_and_income() -> None:
    assert categories.UNSORTED in categories.USCITE[categories.UNSORTED]
    assert categories.UNSORTED in categories.ENTRATE
