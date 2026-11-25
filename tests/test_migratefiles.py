import io
from pathlib import Path

import pytest

from scrooge import categories, dbcli, migratefiles
from scrooge.migratefiles import Change, RefusedError

LABELS = categories.Labels()
RULES = """# My rules.
rules:
  # --- Food
  - {match: 'KONSUM', category: 'Spesa'}   # the shop downstairs
  - {match: "BAR", category: "Ristoranti e bar", direction: out}
  - {match: TRAIN, category: Treni}
  - match: MEDIC
    category: Medico/Farmacia  # by hand
  - {match: FUEL, category: Fuel}
  - {match: COURIER, category: Spedizioni}
"""
CATEGORIES = """# Added by me.
categories:
  uscite:
    Trasporti: [Spedizioni]   # couriers
    Casa:
    - Garden
  entrate: [Prestiti, Rimborsi]
"""
CONFIG = """database: {database}
owner: Lukas
owners:
  Lukas: {{match: ['LUKAS MEIER']}}
  Anna: {{match: ['ANNA MEIER']}}
unmatched: Non classificato  # every row
"""


def _plan(path: Path, text: str, kind: migratefiles.Kind) -> migratefiles.Plan:
    path.write_text(text, encoding="utf-8")
    found = migratefiles.plan(path, kind, LABELS)
    assert found is not None
    return found


def test_italian_names_of_rules_are_written_in_english_and_the_rest_is_kept(
    tmp_path: Path,
) -> None:
    found = _plan(tmp_path / "rules.yaml", RULES, "rules")
    assert found.new == (
        RULES.replace("'Spesa'", "'Groceries'")
        .replace('"Ristoranti e bar"', '"Eating out"')
        .replace("Treni", "Trains")
        .replace("Medico/Farmacia", "Doctor/Pharmacy")
    )
    assert found.changes == (
        Change(4, "Spesa", "Groceries"),
        Change(5, "Ristoranti e bar", "Eating out"),
        Change(6, "Treni", "Trains"),
        Change(8, "Medico/Farmacia", "Doctor/Pharmacy"),
    )
    assert (tmp_path / "rules.yaml").read_text(encoding="utf-8") == RULES  # nothing written


def test_the_sides_and_names_of_categories_yaml_are_written_with_today_s_names(
    tmp_path: Path,
) -> None:
    found = _plan(tmp_path / "categories.yaml", CATEGORIES, "categories")
    assert found.new == (
        "# Added by me.\ncategories:\n  expenses:\n    Transport: [Spedizioni]   # couriers\n"
        "    Home:\n    - Garden\n  income: [Prestiti, Refunds]\n"
    )
    assert [change.new for change in found.changes] == [
        "expenses",
        "Transport",
        "Home",
        "income",
        "Refunds",
    ]


def test_unmatched_in_the_config_is_written_in_english(tmp_path: Path) -> None:
    config = CONFIG.format(database="scrooge.db")
    found = _plan(tmp_path / "scrooge.yaml", config, "config")
    assert found.new == config.replace("Non classificato", "Uncategorized")


def test_a_name_whose_label_would_mean_another_category_is_written_as_its_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown = {**categories.LABELS_EN, categories.GROCERIES: "Eating out"}
    monkeypatch.setitem(categories.LABELS, "xx", shown)
    path = tmp_path / "rules.yaml"
    path.write_text("rules:\n  - {match: A, category: Spesa}\n", encoding="utf-8")
    found = migratefiles.plan(path, "rules", categories.Labels(language="xx"))
    assert found is not None
    assert found.new == "rules:\n  - {match: A, category: groceries}\n"


def test_a_file_without_old_names_or_categories_has_nothing_to_change(tmp_path: Path) -> None:
    texts: list[tuple[str, migratefiles.Kind]] = [
        ("rules:\n  - {match: A, category: Groceries}\n  - just text\n", "rules"),
        ("rules: none\n", "rules"),
        ("[a, b]\n", "config"),
        ("unmatched: 3\n", "config"),
        ("categories: {expenses: {Home: [Garden]}, income: []}\n", "categories"),
        ("categories: none\n", "categories"),
        ("", "categories"),
    ]
    for text, kind in texts:
        found = _plan(tmp_path / "file.yaml", text, kind)
        assert found.changes == ()
        assert found.new == text


def test_a_rule_that_is_no_mapping_is_left_as_it_is(tmp_path: Path) -> None:
    text = "rules:\n  - just text\n  - {match: A, category: Spesa}\n"
    found = _plan(tmp_path / "rules.yaml", text, "rules")
    assert found.new == "rules:\n  - just text\n  - {match: A, category: Groceries}\n"


def test_a_missing_file_has_no_plan(tmp_path: Path) -> None:
    assert migratefiles.plan(tmp_path / "rules.yaml", "rules", LABELS) is None


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        # Casa becomes Home, which the mapping has already: one of the two would be lost.
        ("categories:\n  expenses:\n    Casa: [Garden]\n    Home: [Pets]\n", "categories"),
        ("rules:\n  - {match: A, category: &s Spesa}\n  - {match: B, category: *s}\n", "rules"),
        (
            "rules:\n  - match: A\n    category: |\n      Spesa\n  - {match: B, category: X}\n",
            "rules",
        ),
    ],
)
def test_a_file_whose_meaning_would_change_is_refused(
    tmp_path: Path, text: str, kind: migratefiles.Kind
) -> None:
    path = tmp_path / "file.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(RefusedError, match="change them by hand"):
        migratefiles.plan(path, kind, LABELS)


def test_both_names_of_a_side_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "categories.yaml"
    path.write_text("categories: {expenses: {}, uscite: {Casa: [Pets]}}\n", encoding="utf-8")
    with pytest.raises(RefusedError, match="both 'uscite' and 'expenses'"):
        migratefiles.plan(path, "categories", LABELS)


def test_a_file_that_is_no_yaml_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text("rules: [\n", encoding="utf-8")
    with pytest.raises(RefusedError, match=r"rules\.yaml"):
        migratefiles.plan(path, "rules", LABELS)


def test_writing_keeps_the_file_as_it_was_and_never_over_an_older_copy(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    found = _plan(path, RULES, "rules")
    path.chmod(0o640)
    (tmp_path / "rules.yaml.before-migrate-files").write_text("older", encoding="utf-8")
    kept = migratefiles.write(found)
    assert kept == tmp_path / "rules.yaml.before-migrate-files.2"
    assert kept.read_text(encoding="utf-8") == RULES
    assert path.read_text(encoding="utf-8") == found.new
    assert path.stat().st_mode & 0o777 == 0o640
    assert (tmp_path / "rules.yaml.before-migrate-files").read_text(encoding="utf-8") == "older"


def test_a_file_changed_since_it_was_read_is_not_written(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    found = _plan(path, RULES, "rules")
    path.write_text(RULES + "  - {match: NEW, category: Spesa}\n", encoding="utf-8")
    with pytest.raises(RefusedError, match="changed while it was read"):
        migratefiles.write(found)
    assert migratefiles.kept_as(path) == tmp_path / "rules.yaml.before-migrate-files"  # none


def test_the_files_are_those_the_app_reads_each_once(tmp_path: Path) -> None:
    rules = [tmp_path / "rules.yaml", tmp_path / "more.yaml", tmp_path / "rules.yaml"]
    assert migratefiles.files(rules, ["Lukas", "Anna"], tmp_path / "scrooge.yaml") == [
        (tmp_path / "rules.lukas.yaml", "rules"),
        (tmp_path / "rules.anna.yaml", "rules"),
        (tmp_path / "rules.yaml", "rules"),
        (tmp_path / "more.yaml", "rules"),
        (tmp_path / "categories.yaml", "categories"),
        (tmp_path / "scrooge.yaml", "config"),
    ]


class _Setup:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.config = folder / "scrooge.yaml"
        self.config.write_text(CONFIG.format(database=folder / "scrooge.db"), encoding="utf-8")
        (folder / "rules.yaml").write_text(RULES, encoding="utf-8")
        (folder / "categories.yaml").write_text(CATEGORIES, encoding="utf-8")
        (folder / "rules.anna.yaml").write_text(
            "rules:\n  - {match: LUKAS, category: Giroconto}\n", encoding="utf-8"
        )
        self.output = ""

    def __call__(self, *args: str) -> int:
        out = io.StringIO()
        argv = ["migrate-files", "--config", str(self.config), "--rules", str(self.rules), *args]
        code = dbcli.main(argv, out)
        self.output = out.getvalue()
        return code

    @property
    def rules(self) -> Path:
        return self.folder / "rules.yaml"

    def texts(self) -> dict[str, str]:
        return {path.name: path.read_text(encoding="utf-8") for path in self.folder.iterdir()}


def test_the_command_says_what_it_would_change_and_writes_nothing(tmp_path: Path) -> None:
    run = _Setup(tmp_path)
    before = run.texts()
    assert run() == dbcli.OK
    assert run.texts() == before
    assert f"{tmp_path / 'rules.lukas.yaml'}: not found, skipped" in run.output
    assert f"{tmp_path / 'rules.anna.yaml'}:\n  2: Giroconto -> Own transfer\n" in run.output
    assert "  4: Spesa -> Groceries\n" in run.output
    assert "  6: Non classificato -> Uncategorized\n" in run.output
    assert run.output.endswith("run again with --write to write it.\n")
    assert not (tmp_path / "scrooge.db").exists()  # no database is needed


def test_the_command_writes_with_write_and_a_second_run_changes_nothing(tmp_path: Path) -> None:
    run = _Setup(tmp_path)
    before = run.texts()
    assert run("--write") == dbcli.OK
    after = run.texts()
    for name in ("rules.yaml", "categories.yaml", "scrooge.yaml", "rules.anna.yaml"):
        assert after[f"{name}.before-migrate-files"] == before[name]
        assert after[name] != before[name]
        assert f"written; as it was: {name}.before-migrate-files" in run.output
    assert "category: Own transfer" in after["rules.anna.yaml"]
    assert run("--write") == dbcli.OK
    assert run.texts() == after
    assert "nothing to change" in run.output
    assert run.output.endswith("Every file has today's names already.\n")


def test_a_refused_file_is_said_and_the_others_are_written(tmp_path: Path) -> None:
    run = _Setup(tmp_path)
    (tmp_path / "categories.yaml").write_text(
        "categories:\n  expenses:\n    Casa: [Garden]\n    Home: [Pets]\n", encoding="utf-8"
    )
    assert run("--write") == dbcli.FAILED
    assert "error: " in run.output
    assert "change them by hand" in run.output
    assert not (tmp_path / "categories.yaml.before-migrate-files").exists()
    assert (tmp_path / "rules.yaml.before-migrate-files").exists()
    assert "Every file" not in run.output
