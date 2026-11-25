"""The rule offered for a category changed by hand on a staged row (plan decision 56)."""

import re
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from scrooge import categories, classification, query, staging, suggestions
from scrooge.errors import ConfigError
from scrooge.models import CashBalance
from scrooge.storage import db
from scrooge.suggestions import Places, Suggestion
from tests.storage.helpers import OWNER, PARTNER, PEOPLE, batch, rows, statement, txn

NOW = datetime(2026, 12, 20, 12, 0, tzinfo=UTC)  # after every day the fixtures cover
LATER = datetime(2026, 12, 21, 9, 30, tzinfo=UTC)
BAKERY = "BAKERY 12 ZURICH, Main Street 1"  # a shop number, a place, then an address


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, NOW)
    yield connection
    connection.close()


@pytest.fixture
def places(tmp_path: Path) -> Places:
    shared = tmp_path / "rules.yaml"
    shared.write_text("rules:\n  - {match: BAKERY, category: Groceries}\n", encoding="utf-8")
    return Places([shared, tmp_path / "rules.more.yaml"], PEOPLE)


def stage(conn: sqlite3.Connection, owner: str = OWNER) -> dict[str, str]:
    """September staged for `owner`, each row with the category an import gave it; returns
    the source reference of each row -> its id."""
    found = [
        txn("-12.50", source_ref="1", counterparty=BAKERY),
        txn("-4", source_ref="2", counterparty="Bakery 77"),
        txn("-200", source_ref="3", description="TWINT ANNA MEIER", category=categories.GIFTS),
        txn("-9", source_ref="4", description="1234 / 5678", category=categories.GROCERIES),
        txn("-3", source_ref="5", counterparty="TWO\nLINES"),
        txn("-1", source_ref="6", description="LUKAS MEIER POCKET", category=categories.GIFTS),
        txn("-6", source_ref="7", counterparty="Bakery 5 Zurich"),
    ]
    total = sum((row.amount for row in found), Decimal(0))
    balance = CashBalance("Migros", "CHF", Decimal(1000), Decimal(1000) + total)
    staging.stage(conn, batch(statement("migros", found, [balance]), owner=owner), NOW)
    return dict(rows(conn, "SELECT source_ref, id FROM transactions"))


def change(conn: sqlite3.Connection, ids: list[str], category: str) -> None:
    """Another category for these rows alone, as the import page gives a row chosen on its
    own."""
    staging.categorize(
        conn, owner=OWNER, ids=ids, category=category, now=LATER, remember=None, change=True
    )


def test_a_category_changed_by_hand_is_offered_as_a_rule_for_its_merchant(
    conn: sqlite3.Connection, places: Places
) -> None:
    ids = stage(conn)
    change(conn, [ids["1"]], categories.GIFTS)
    (offered,) = suggestions.suggest(conn, places, OWNER, [ids["1"]])
    assert offered == Suggestion(
        ids["1"],
        "BAKERY ZURICH",
        classification.merchant_match(["BAKERY", "ZURICH"]),
        "out",
        categories.GIFTS,
        places.shared[0],
    )


def test_an_added_rule_goes_first_in_its_file_and_is_offered_no_more(
    conn: sqlite3.Connection, places: Places
) -> None:
    ids = stage(conn)
    change(conn, [ids["1"]], categories.GIFTS)
    added = suggestions.add(conn, places, OWNER, ids["1"])
    assert added.merchant == "BAKERY ZURICH"
    match = classification.quoted(added.match)
    assert places.shared[0].read_text(encoding="utf-8") == (
        "rules:\n"
        f'  - {{match: {match}, category: "Gifts", direction: out}}\n'
        "  - {match: BAKERY, category: Groceries}\n"
    )
    (rule, _) = classification.load_rules([places.shared[0]])
    assert (rule.category, rule.direction) == (categories.GIFTS, "out")
    assert suggestions.suggest(conn, places, OWNER, [ids["1"]]) == []
    with pytest.raises(suggestions.SuggestionError, match="not needed any more"):
        suggestions.add(conn, places, OWNER, ids["1"])


def test_rows_of_one_merchant_changed_to_one_category_are_offered_one_rule(
    conn: sqlite3.Connection, places: Places
) -> None:
    ids = stage(conn)
    change(conn, [ids["1"], ids["7"]], categories.GIFTS)
    offered = suggestions.suggest(conn, places, OWNER, [ids["1"], ids["7"]])
    assert [s.merchant for s in offered] == ["BAKERY ZURICH"]


def test_nothing_is_offered_where_the_rules_already_give_the_category(
    conn: sqlite3.Connection, places: Places
) -> None:
    ids = stage(conn)
    # Given by the rule and never changed.
    assert suggestions.suggest(conn, places, OWNER, [ids["1"], ids["2"]]) == []
    # Changed to what a rule before the wrong one gives.
    places.shared[0].write_text(
        "rules:\n  - {match: '77', category: Gifts}\n  - {match: BAKERY, category: Groceries}\n",
        encoding="utf-8",
    )
    change(conn, [ids["2"]], categories.GIFTS)
    assert suggestions.suggest(conn, places, OWNER, [ids["2"]]) == []


def test_the_category_of_the_rows_no_rule_matches_counts_as_given(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    ids = stage(conn)
    unmatched = Places([tmp_path / "rules.yaml"], PEOPLE, unmatched="Groceries")
    assert suggestions.suggest(conn, unmatched, OWNER, [ids["1"]]) == []
    change(conn, [ids["1"]], categories.GIFTS)
    (offered,) = suggestions.suggest(conn, unmatched, OWNER, [ids["1"]])
    assert offered.category == categories.GIFTS
    none = Places([tmp_path / "rules.yaml"], PEOPLE)  # no rule gives what the import gave
    assert [s.row for s in suggestions.suggest(conn, none, OWNER, [ids["2"]])] == [ids["2"]]


def test_a_row_naming_a_person_gets_a_rule_of_the_owners_own(
    conn: sqlite3.Connection, places: Places
) -> None:
    ids = stage(conn)
    change(conn, [ids["6"]], categories.EATING_OUT)
    (offered,) = suggestions.suggest(conn, places, OWNER, [ids["6"]])
    assert offered.file == places.own(OWNER)
    suggestions.add(conn, places, OWNER, ids["6"])
    assert classification.load_rules([places.own(OWNER)])[0].category == categories.EATING_OUT
    assert "POCKET" not in places.shared[0].read_text(encoding="utf-8")


def test_a_rule_of_the_owners_own_that_was_wrong_is_corrected_there(
    conn: sqlite3.Connection, places: Places
) -> None:
    ids = stage(conn)
    places.own(OWNER).write_text("rules:\n  - {match: BAKERY, category: Dentist}\n")
    change(conn, [ids["1"]], categories.GIFTS)
    (offered,) = suggestions.suggest(conn, places, OWNER, [ids["1"]])
    assert offered.file == places.own(OWNER)


def test_no_rule_makes_money_with_the_other_person_a_transfer(
    conn: sqlite3.Connection, places: Places
) -> None:
    ids = stage(conn)
    change(conn, [ids["3"]], categories.OWN_TRANSFER)
    assert suggestions.suggest(conn, places, OWNER, [ids["3"]]) == []
    change(conn, [ids["1"]], categories.OWN_TRANSFER)  # nobody named: a transfer may be a rule
    assert [s.category for s in suggestions.suggest(conn, places, OWNER, [ids["1"]])] == [
        categories.OWN_TRANSFER
    ]


def test_a_row_without_letters_or_found_on_no_line_is_offered_nothing(
    conn: sqlite3.Connection, places: Places
) -> None:
    ids = stage(conn)
    change(conn, [ids["4"], ids["5"]], categories.GIFTS)
    assert suggestions.suggest(conn, places, OWNER, [ids["4"], ids["5"]]) == []


def test_only_the_owners_rows_whose_category_can_change_are_offered_anything(
    conn: sqlite3.Connection, places: Places
) -> None:
    ids = stage(conn)
    change(conn, [ids["1"]], categories.GIFTS)
    assert suggestions.suggest(conn, places, PARTNER, [ids["1"]]) == []
    assert suggestions.suggest(conn, places, "Nobody", [ids["1"]]) == []
    assert suggestions.suggest(conn, places, OWNER, []) == []
    assert suggestions.suggest(conn, places, OWNER, ["no such row"]) == []


def test_a_rules_file_that_cannot_be_read_is_said(conn: sqlite3.Connection, places: Places) -> None:
    ids = stage(conn)
    change(conn, [ids["1"]], categories.GIFTS)
    places.shared[0].write_text("rules: 3\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="'rules' list"):
        suggestions.suggest(conn, places, OWNER, [ids["1"]])


def test_a_merchant_rule_finds_the_merchant_whatever_numbers_and_address() -> None:
    rule = classification.Rule(
        re.compile(classification.merchant_match(["BAKERY", "ZURICH"]), re.IGNORECASE), "x"
    )
    found = [
        "BAKERY 12 ZURICH, Main Street 1",
        "bakery-77 zurich",
        "*Bakery/Zurich 3",
        "CARD 1234\nBAKERY ZURICH",
    ]
    for text in found:
        description, _, counterparty = text.rpartition("\n")
        row = txn("-1", description=description, counterparty=counterparty)
        assert rule.matches(row), text
    for text in ["BAKERY ZURICH WEST", "BAKERYZURICH", "MY BAKERY ZURICH", "BAKERY, ZURICH"]:
        assert not rule.matches(txn("-1", description="", counterparty=text)), text


LINE = st.text(alphabet="abcAB é,.-*/ 1_", max_size=20)


@given(LINE, LINE)
def test_a_merchant_rule_matches_a_line_exactly_when_it_names_the_same_merchant(
    one: str, other: str
) -> None:
    words = query.merchant_words(one)
    if not words:
        return
    pattern = re.compile(classification.merchant_match(words), re.IGNORECASE)
    assert pattern.search(one)
    same = [w.lower() for w in query.merchant_words(other)] == [w.lower() for w in words]
    assert bool(pattern.search(other)) == same
