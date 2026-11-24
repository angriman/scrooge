"""A rules file changed one rule at a time keeps everything else of itself."""

from pathlib import Path

import pytest

from scrooge import rulesfile
from scrooge.classification import load_rules
from scrooge.errors import ConfigError

TEXT = """# Rules of the tests.
rules:
  # --- Shops
  - {match: 'KONSUM|FRISCHMARKT', category: Spesa}  # food
  - match: ACME, INC
    category: Stipendio
    direction: in

  - {match: '.', category: Regali, account: Migros}
"""
PATH = Path("rules.yaml")


def _entries(text: str = TEXT) -> tuple[rulesfile.Entry, ...]:
    return rulesfile.entries(text, PATH)


def test_entries_say_what_each_rule_gives() -> None:
    found = _entries()
    assert [(e.match, e.category, e.direction, e.account) for e in found] == [
        ("KONSUM|FRISCHMARKT", "Spesa", "", ""),
        ("ACME, INC", "Stipendio", "in", ""),
        (".", "Regali", "", "Migros"),
    ]
    first, last = found[0].lines
    assert TEXT[first:last] == "  - {match: 'KONSUM|FRISCHMARKT', category: Spesa}  # food\n"


def test_a_rule_written_again_keeps_the_rest_and_its_comment(tmp_path: Path) -> None:
    found = _entries()
    text = rulesfile.rewritten(TEXT, found[0], {"match": "KONSUM", "direction": "out"})
    assert text == TEXT.replace(
        "{match: 'KONSUM|FRISCHMARKT', category: Spesa}  # food",
        '{match: "KONSUM", category: Spesa, direction: out}  # food',
    )
    path = tmp_path / "rules.yaml"
    path.write_text(text, encoding="utf-8")
    assert load_rules([path])[0].direction == "out"


def test_a_rule_on_lines_of_its_own_comes_back_between_braces(tmp_path: Path) -> None:
    text = rulesfile.rewritten(TEXT, _entries()[1], {"direction": "", "kind": "income"})
    assert '  - {match: "ACME, INC", category: "Stipendio", kind: income}\n\n' in text
    path = tmp_path / "rules.yaml"
    path.write_text(text, encoding="utf-8")
    assert [rule.pattern.pattern for rule in load_rules([path])][1] == "ACME, INC"


def test_a_rule_removed_takes_its_lines_and_nothing_else() -> None:
    text = rulesfile.removed(TEXT, _entries()[1])
    assert text == TEXT.replace(
        "  - match: ACME, INC\n    category: Stipendio\n    direction: in\n", ""
    )


def test_two_rules_swapped_leave_what_is_between_them() -> None:
    found = _entries()
    text = rulesfile.swapped(TEXT, found[1], found[2])
    assert [e.match for e in _entries(text)] == ["KONSUM|FRISCHMARKT", ".", "ACME, INC"]
    assert "# --- Shops\n" in text
    assert "direction: in\n\n" not in text  # the blank line stayed where it was
    assert "  - {match: '.', category: Regali, account: Migros}\n\n  - match: ACME" in text


def test_recategorized_changes_the_category_alone() -> None:
    text, count = rulesfile.recategorized(TEXT, _entries(), "Spesa", "Cibo")
    assert count == 1
    assert "  - {match: 'KONSUM|FRISCHMARKT', category: \"Cibo\"}  # food\n" in text


def test_find_refuses_a_rule_that_changed(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text(TEXT.rstrip("\n"), encoding="utf-8")  # no newline at the end
    text, entry = rulesfile.find(path, 3, ".", "Regali")
    assert text.endswith("\n")
    assert entry.account == "Migros"
    with pytest.raises(rulesfile.StaleRuleError, match="changed since"):
        rulesfile.find(path, 1, "KONSUM", "Spesa")
    with pytest.raises(rulesfile.StaleRuleError, match="no rule 4"):
        rulesfile.find(path, 4, ".", "Regali")


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
        ("rules: [{match: a, category: Spesa}]\n", "one per line"),
        ("rules:\n  - just text\n", "needs 'match' and 'category'"),
        ("rules:\n  - {match: a}\n", "needs 'match' and 'category'"),
    ],
)
def test_files_that_cannot_be_changed_here_say_why(text: str, problem: str) -> None:
    with pytest.raises(ConfigError, match=problem):
        _entries(text)


def test_a_key_the_rules_do_not_read_is_kept_as_it_is() -> None:
    text = "rules:\n  - match: KONSUM\n    category: Spesa\n    seen: [2025]\n"
    (entry,) = _entries(text)
    assert rulesfile.rewritten(text, entry, {"category": "Cibo"}) == (
        'rules:\n  - {match: "KONSUM", category: "Cibo", seen: [2025]}\n'
    )
