"""A rules file changed one rule at a time keeps everything else of itself."""

from pathlib import Path

import pytest

from scrooge import categories, rulesfile
from scrooge.classification import load_rules
from scrooge.errors import ConfigError

TEXT = """# Rules of the tests.
rules:
  # --- Shops
  - {match: 'KONSUM|FRISCHMARKT', category: Groceries}  # food
  - match: ACME, INC
    category: Salary
    direction: in

  - {match: '.', category: Gifts, account: Migros}
"""
PATH = Path("rules.yaml")


def _entries(text: str = TEXT) -> tuple[rulesfile.Entry, ...]:
    return rulesfile.entries(text, PATH)


def test_entries_say_what_each_rule_gives() -> None:
    found = _entries()
    assert [(e.match, e.category, e.direction, e.account) for e in found] == [
        ("KONSUM|FRISCHMARKT", categories.GROCERIES, "", ""),
        ("ACME, INC", categories.SALARY, "in", ""),
        (".", categories.GIFTS, "", "Migros"),
    ]
    first, last = found[0].lines
    assert TEXT[first:last] == "  - {match: 'KONSUM|FRISCHMARKT', category: Groceries}  # food\n"


def test_a_rule_written_again_keeps_the_rest_and_its_comment(tmp_path: Path) -> None:
    found = _entries()
    text = rulesfile.rewritten(TEXT, found[0], {"match": "KONSUM", "direction": "out"})
    assert text == TEXT.replace(
        "{match: 'KONSUM|FRISCHMARKT', category: Groceries}  # food",
        "{match: 'KONSUM', category: Groceries, direction: out}  # food",
    )
    path = tmp_path / "rules.yaml"
    path.write_text(text, encoding="utf-8")
    assert load_rules([path])[0].direction == "out"


def test_a_rule_on_lines_of_its_own_comes_back_between_braces(tmp_path: Path) -> None:
    text = rulesfile.rewritten(TEXT, _entries()[1], {"direction": "", "kind": "income"})
    # Its plain match has a comma, which braces would read as the end of it: it is quoted.
    assert "  - {match: 'ACME, INC', category: Salary, kind: income}\n\n" in text
    path = tmp_path / "rules.yaml"
    path.write_text(text, encoding="utf-8")
    assert [rule.pattern.pattern for rule in load_rules([path])][1] == "ACME, INC"


@pytest.mark.parametrize(
    ("before", "changes", "after"),
    [
        # A value given again as it was keeps its text, escapes and all.
        (
            '{match: "\\\\d+ SHOP", category: Groceries}',
            {"match": "\\d+ SHOP", "category": "Groceries", "direction": "in"},
            '{match: "\\\\d+ SHOP", category: Groceries, direction: in}',
        ),
        # Each value changed keeps its quotes, or their absence.
        (
            "{match: SHOP, category: 'Groceries', direction: \"out\"}",
            {"match": "STORE", "category": "Gifts", "direction": "in"},
            "{match: STORE, category: 'Gifts', direction: \"in\"}",
        ),
        ("{match: 'A', category: Gifts}", {"match": "it's"}, "{match: 'it''s', category: Gifts}"),
        # A plain value YAML would read as something else is quoted, once.
        ("{match: A, category: Gifts}", {"match": "yes"}, "{match: 'yes', category: Gifts}"),
        ("{match: A, category: Gifts}", {"match": "A #1"}, "{match: 'A #1', category: Gifts}"),
        ("{match: A, category: Gifts}", {"match": "[A]"}, "{match: '[A]', category: Gifts}"),
        ("{match: A, category: Gifts}", {"match": "A: B"}, "{match: 'A: B', category: Gifts}"),
        ("{match: A, category: Gifts}", {"match": " A"}, "{match: ' A', category: Gifts}"),
        ("{match: A, category: Gifts}", {"match": "A\tB"}, '{match: "A\\tB", category: Gifts}'),
        ("{match: A, category: Gifts}", {"match": "A|B.*"}, "{match: A|B.*, category: Gifts}"),
    ],
)
def test_a_value_changed_keeps_the_style_of_the_file(
    tmp_path: Path, before: str, changes: dict[str, str], after: str
) -> None:
    text = f"rules:\n  - {before}\n"
    written = rulesfile.rewritten(text, _entries(text)[0], changes)
    assert written == f"rules:\n  - {after}\n"
    path = tmp_path / "rules.yaml"
    path.write_text(written, encoding="utf-8")
    (rule,) = load_rules([path])
    assert rule.pattern.pattern == changes.get("match", _entries(text)[0].match)


def test_a_rule_on_lines_of_its_own_keeps_its_quotes() -> None:
    text = "rules:\n  - match: 'A'\n    category: \"Gifts\"\n    direction: out\n"
    written = rulesfile.rewritten(text, _entries(text)[0], {"direction": "in"})
    assert written == "rules:\n  - {match: 'A', category: \"Gifts\", direction: in}\n"


def test_a_rule_removed_takes_its_lines_and_nothing_else() -> None:
    text = rulesfile.removed(TEXT, _entries()[1])
    assert text == TEXT.replace(
        "  - match: ACME, INC\n    category: Salary\n    direction: in\n", ""
    )


def test_two_rules_swapped_leave_what_is_between_them() -> None:
    found = _entries()
    text = rulesfile.swapped(TEXT, found[1], found[2])
    assert [e.match for e in _entries(text)] == ["KONSUM|FRISCHMARKT", ".", "ACME, INC"]
    assert "# --- Shops\n" in text
    assert "direction: in\n\n" not in text  # the blank line stayed where it was
    assert "  - {match: '.', category: Gifts, account: Migros}\n\n  - match: ACME" in text


def test_recategorized_changes_the_category_alone() -> None:
    text, count = rulesfile.recategorized(TEXT, _entries(), categories.GROCERIES, "Cibo")
    assert count == 1
    assert "  - {match: 'KONSUM|FRISCHMARKT', category: Cibo}  # food\n" in text
    quoted = (
        "rules:\n  - {match: A, category: 'Groceries'}\n  - match: B\n    category: Groceries\n"
    )
    text, count = rulesfile.recategorized(quoted, _entries(quoted), categories.GROCERIES, "Bio")
    assert (text, count) == (quoted.replace("Groceries", "Bio"), 2)


def test_find_refuses_a_rule_that_changed(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text(TEXT.rstrip("\n"), encoding="utf-8")  # no newline at the end
    versions = [entry.version for entry in _entries()]
    assert len(set(versions)) == 3
    text, found, entry = rulesfile.find(path, 3, versions[2])
    assert text.endswith("\n")
    assert (len(found), entry.account) == (3, "Migros")
    with pytest.raises(rulesfile.StaleRuleError, match="changed since"):
        rulesfile.find(path, 1, versions[2])
    with pytest.raises(rulesfile.StaleRuleError, match="no rule 4"):
        rulesfile.find(path, 4, versions[2])


def test_what_cannot_be_changed_line_by_line_is_said() -> None:
    text = (
        "rules:\n  -\n    match: A\n    category: Groceries\n"
        "  - match: B  # why\n    category: Groceries\n"
        "  - {match: C,  # why\n     category: Groceries}\n"
        "  - {match: D, category: '#1'}\n"
    )
    lone, commented, flow, quoted = _entries(text)
    assert lone.refusal(editing=False).startswith("its dash stands on a line of its own")
    assert commented.refusal(editing=False) == ""
    assert "comments among its keys" in commented.refusal(editing=True)
    assert flow.comments
    assert not quoted.comments  # a '#' inside a value is no comment


@pytest.mark.parametrize(
    "text",
    ["", "# nothing yet\n", "rules:\n", "rules: []\n", "other: 1\n", "- a list\n"],
)
def test_files_without_rules_have_none(text: str) -> None:
    assert _entries(text) == ()


def test_a_file_missing_has_no_rules(tmp_path: Path) -> None:
    assert rulesfile.read(tmp_path / "rules.anna.yaml") == ("", ())


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("rules: [\n", "rules.yaml"),
        ("rules: {a: 1}\n", "expected a top-level 'rules' list"),
        ("rules: [{match: a, category: Groceries}]\n", "one per line"),
        ("rules:\n  - just text\n", "needs 'match' and 'category'"),
        ("rules:\n  - {match: a}\n", "needs 'match' and 'category'"),
    ],
)
def test_files_that_cannot_be_changed_here_say_why(text: str, problem: str) -> None:
    with pytest.raises(ConfigError, match=problem):
        _entries(text)


def test_a_key_the_rules_do_not_read_is_kept_as_it_is() -> None:
    text = "rules:\n  - match: KONSUM\n    category: Groceries\n    seen: [2025]\n"
    (entry,) = _entries(text)
    assert rulesfile.rewritten(text, entry, {"category": "Cibo"}) == (
        "rules:\n  - {match: KONSUM, category: Cibo, seen: [2025]}\n"
    )


def test_a_category_of_before_is_given_by_its_english_name() -> None:
    (entry,) = _entries("rules:\n  - match: KONSUM\n    category: Spesa\n")
    assert entry.category == categories.GROCERIES


@pytest.mark.parametrize(
    ("before", "expected"),
    [
        ("", "rules:\n  - {match: 'NEW', category: Groceries}\n"),
        ("# nothing yet", "# nothing yet\nrules:\n  - {match: 'NEW', category: Groceries}\n"),
        ("# nothing yet\n", "# nothing yet\nrules:\n  - {match: 'NEW', category: Groceries}\n"),
        ("rules:\n", "rules:\n  - {match: 'NEW', category: Groceries}\n"),
        ("rules: null\n", "rules:\n  - {match: 'NEW', category: Groceries}\n"),
        ("rules: ~\n", "rules:\n  - {match: 'NEW', category: Groceries}\n"),
        ("rules: []\n", "rules:\n  - {match: 'NEW', category: Groceries}\n"),
        (
            "rules: []  # none yet\n",
            "rules:  # none yet\n  - {match: 'NEW', category: Groceries}\n",
        ),
        (
            "rules: []#tight\n",
            "rules: #tight\n  - {match: 'NEW', category: Groceries}\n",
        ),
        (
            "rules:\n  []\n",
            "rules:\n  - {match: 'NEW', category: Groceries}\n  \n",
        ),
        (
            "other: 1\n",
            "other: 1\nrules:\n  - {match: 'NEW', category: Groceries}\n",
        ),
        (
            "rules:\nother: 1\n",
            "rules:\n  - {match: 'NEW', category: Groceries}\nother: 1\n",
        ),
        (
            "rules: null\nother: 1\n",
            "rules:\n  - {match: 'NEW', category: Groceries}\nother: 1\n",
        ),
    ],
)
def test_inserted_handles_empty_and_missing_rules_shapes(
    tmp_path: Path, before: str, expected: str
) -> None:
    block = "  - {match: 'NEW', category: Groceries}"  # also verifies trailing newline added
    written = rulesfile.inserted(before, PATH, block)
    assert written == expected
    path = tmp_path / "rules.yaml"
    path.write_text(written, encoding="utf-8")
    assert [rule.pattern.pattern for rule in load_rules([path])] == ["NEW"]


def test_inserted_places_first_before_comments_and_last_after_last_rule(tmp_path: Path) -> None:
    block = "  - match: NEW  # inline\n    category: Gifts\n    account: Migros\n"
    at_start = rulesfile.inserted(TEXT, PATH, block, first=True)
    assert at_start.startswith(
        "# Rules of the tests.\nrules:\n  - match: NEW  # inline\n    category: Gifts\n"
        "    account: Migros\n  # --- Shops\n"
    )
    with_other = TEXT + "other: 1\n"
    at_end = rulesfile.inserted(with_other, PATH, block, first=False)
    assert at_end.endswith(
        "  - {match: '.', category: Gifts, account: Migros}\n"
        "  - match: NEW  # inline\n    category: Gifts\n    account: Migros\nother: 1\n"
    )
    path = tmp_path / "rules.yaml"
    path.write_text(at_start, encoding="utf-8")
    assert load_rules([path])[0].pattern.pattern == "NEW"
    path.write_text(at_end, encoding="utf-8")
    assert load_rules([path])[-1].pattern.pattern == "NEW"


@pytest.mark.parametrize(
    ("dest", "block", "expected_matches"),
    [
        (
            "rules:\n- {match: A, category: Groceries}\n",
            "  - match: B\n\n    category: Gifts\n",
            ["A", "B"],
        ),
        (
            "rules:\n    - {match: A, category: Groceries}\n",
            "  - match: B\n    category: Gifts\n",
            ["A", "B"],
        ),
        (
            "rules:\n  - {match: A, category: Groceries}\n",
            "    - {match: B, category: Gifts}\n",
            ["A", "B"],
        ),
    ],
)
def test_inserted_reindents_block_to_match_destination(
    tmp_path: Path, dest: str, block: str, expected_matches: list[str]
) -> None:
    written = rulesfile.inserted(dest, PATH, block)
    path = tmp_path / "rules.yaml"
    path.write_text(written, encoding="utf-8")
    assert [rule.pattern.pattern for rule in load_rules([path])] == expected_matches


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("- a list\n", "expected a mapping"),
        ("rules: [\n", "rules.yaml"),
        ("rules: {a: 1}\n", "expected a top-level 'rules' list"),
        ("rules: [{match: a, category: Groceries}]\n", "one per line"),
    ],
)
def test_inserted_refuses_unwritable_targets(text: str, problem: str) -> None:
    with pytest.raises(ConfigError, match=problem):
        rulesfile.inserted(text, PATH, "  - {match: A, category: Groceries}\n")


@pytest.mark.parametrize("first", [True, False])
def test_inserted_into_file_whose_first_rule_has_lone_dash(tmp_path: Path, first: bool) -> None:
    dest = "rules:\n  -\n    match: A\n    category: Groceries\n"
    block = "    - {match: B, category: Gifts}\n"
    written = rulesfile.inserted(dest, PATH, block, first=first)
    path = tmp_path / "rules.yaml"
    path.write_text(written, encoding="utf-8")
    expected = ["B", "A"] if first else ["A", "B"]
    assert [rule.pattern.pattern for rule in load_rules([path])] == expected
    assert "  - {match: B, category: Gifts}\n" in written
