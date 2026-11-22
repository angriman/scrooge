"""`finanze init`: the first run makes a setup in users/<name>/ that works as it is."""

import io
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from finanze import dbcli, onboarding
from tests.conftest import FILES, FIXTURES

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
STARTER = Path(onboarding.__file__).parent / "starter"


def init(*answers: str) -> tuple[int, str, list[str]]:
    out, left, asked = io.StringIO(), list(answers), []

    def ask(question: str) -> str:
        asked.append(question)
        return left.pop(0)

    code = dbcli.main(["init"], out, ask=ask, now=lambda: NOW)
    assert not left, "every answer is asked for"
    return code, out.getvalue(), asked


@pytest.fixture(autouse=True)
def here(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_one_person_gets_a_setup_that_imports_as_it_is(here: Path) -> None:
    code, output, asked = init("Lukas", "LUKAS MEIER, CH93 0076 2011 6238 5295 7", "")
    assert code == dbcli.OK
    home = here / "users" / "lukas"
    assert sorted(p.name for p in home.iterdir()) == [
        "categories.yaml",
        "finanze.yaml",
        "instruments.yaml",
        "rules.yaml",
        "statements",
    ]
    assert len(asked) == 3
    assert f"Your setup is in {Path('users/lukas')}/:\n  finanze.yaml: written\n" in output
    assert "  1. Put your statement exports in users/lukas/statements/\n" in output
    # No --user: it is the only one.
    assert "  2. finanze import users/lukas/statements\n" in output

    config = dbcli._config(home / "finanze.yaml")
    # Relative to the folder the commands run in, as every config's paths.
    assert config["database"] == "users/lukas/finanze.db"
    assert config["rules"] == ["users/lukas/rules.yaml"]
    assert config["instruments"] == "users/lukas/instruments.yaml"
    assert config["owner"] == "Lukas"
    iban = r"C\s?H\s?9\s?3\s?0\s?0\s?7\s?6\s?2\s?0\s?1\s?1\s?6\s?2\s?3\s?8\s?5\s?2\s?9\s?5\s?7"
    assert config["owners"] == {"Lukas": {"match": ["LUKAS\\ MEIER", iban]}}
    for name in onboarding.STARTERS:
        text = (STARTER / name).read_text(encoding="utf-8")
        assert (home / name).read_text(encoding="utf-8") == text

    shutil.copy(FIXTURES / FILES["Migros.csv"], home / "statements" / "Migros.csv")
    out = io.StringIO()
    code = dbcli.main(["import", "users/lukas/statements"], out, now=lambda: NOW)
    assert code != dbcli.FAILED, out.getvalue()
    assert "  staged Migros.csv (Migros Bank account statement)\n" in out.getvalue()
    assert (home / "finanze.db").is_file()
    assert (home / "originals").is_dir()


@pytest.mark.parametrize(
    ("printed", "matches"),
    [
        ("CH93 0076 2011 6238 5295 7", ["CH9300762011623852957", "CH93 0076 2011 6238 5295 7"]),
        ("CH9300762011623852957", ["CH9300762011623852957", "ch93 0076 2011 6238 5295 7"]),
        ("LUKAS MEIER", ["LUKAS MEIER"]),
    ],
)
def test_an_iban_matches_with_and_without_its_spaces(printed: str, matches: list[str]) -> None:
    found = re.compile(onboarding.pattern(printed), re.IGNORECASE)
    assert all(found.fullmatch(text) for text in matches)
    assert not re.fullmatch(onboarding.pattern("LUKAS MEIER"), "LUKASMEIER")


def test_a_second_person_and_a_name_that_is_no_folder_name(here: Path) -> None:
    code, output, _ = init("Zoë Müller", "ZOE MULLER", "Ann", "ANN MEIER")
    assert code == dbcli.OK, output
    home = here / "users" / "zoe-muller"
    config = dbcli._config(home / "finanze.yaml")
    assert config["owner"] == "Zoë Müller"
    assert config["owners"] == {
        "Zoë Müller": {"match": ["ZOE\\ MULLER"]},
        "Ann": {"match": ["ANN\\ MEIER"]},
    }
    assert config["database"] == "users/zoe-muller/finanze.db"


def test_what_cannot_be_used_is_asked_again(here: Path) -> None:
    code, output, asked = init("  ", "Lukas", "", "LUKAS MEIER", "")
    assert code == dbcli.OK
    assert "  a name with at least one letter or digit, please\n" in output
    assert asked[0] == asked[1]  # the name, again
    assert asked[2] == asked[3]  # how it is printed, again
    assert (here / "users" / "lukas" / "finanze.yaml").is_file()


def test_a_name_whose_setup_exists_is_refused_and_nothing_changes(here: Path) -> None:
    assert init("Lukas", "LUKAS MEIER", "")[0] == dbcli.OK
    config = here / "users" / "lukas" / "finanze.yaml"
    before = config.read_text(encoding="utf-8")
    code, output, _ = init("LUKAS")
    assert code == dbcli.FAILED
    assert output == (
        f"error: {Path('users/lukas')} is a setup already: use it with --user lukas, "
        "or give another name\n"
    )
    assert config.read_text(encoding="utf-8") == before


def test_files_already_there_are_kept(here: Path) -> None:
    home = here / "users" / "lukas"
    home.mkdir(parents=True)
    (home / "rules.yaml").write_text("rules: []\n", encoding="utf-8")
    code, output, _ = init("Lukas", "LUKAS MEIER", "")
    assert code == dbcli.OK
    assert "  rules.yaml: kept, it was there already\n" in output
    assert (home / "rules.yaml").read_text(encoding="utf-8") == "rules: []\n"


def test_a_config_from_before_users_is_pointed_out(here: Path) -> None:
    (here / "finanze.yaml").write_text("database: user/finanze.db\n", encoding="utf-8")
    (here / "uv").write_text("#!/bin/sh\n", encoding="utf-8")
    assert init("Anna", "ANNA MEIER", "")[0] == dbcli.OK
    code, output, _ = init("Lukas", "LUKAS MEIER", "")
    assert code == dbcli.OK
    assert output.startswith("Note: finanze.yaml is a setup made before users/ existed.")
    # Two setups now: each command names the one it is about, through the folder's uv.
    assert "  2. ./uv run --no-dev finanze import --user lukas users/lukas/statements\n" in output


def test_with_a_config_here_the_next_steps_name_the_new_setup(here: Path) -> None:
    (here / "finanze.yaml").write_text("database: user/finanze.db\n", encoding="utf-8")
    code, output, _ = init("Anna", "ANNA MEIER", "")
    assert code == dbcli.OK
    assert "While it is there, it is the one `finanze` uses unless --user" in output
    assert "  3. finanze commit --user anna" in output


def test_the_starter_config_holds_every_answer() -> None:
    text = onboarding.config_text(Path("users/a"), "A", {"A": ["A.B"], 'C "D"': ["E"]})
    assert "$" not in text.replace("scrypt$...", "")
    loaded = yaml.safe_load(text)
    assert loaded["owners"] == {"A": {"match": ["A\\.B"]}, 'C "D"': {"match": ["E"]}}
    # The rest is comments.
    assert loaded.keys() == {"database", "rules", "instruments", "owner", "owners"}


@pytest.mark.parametrize(
    ("name", "folder"),
    [("Anna", "anna"), ("Zoë Müller", "zoe-muller"), ("a/../b", "a-b"), ("--", ""), ("_x_", "_x_")],
)
def test_folder_names(name: str, folder: str) -> None:
    assert onboarding.folder_name(name) == folder


@pytest.mark.parametrize(
    ("named", "chosen"),
    [("Lukas", "Lukas"), ("lukas", "Lukas"), ("zoë müller", "zoe-muller"), ("x", None)],
)
def test_a_setup_is_found_by_its_folder_s_name_or_what_init_makes_of_it(
    named: str, chosen: str | None
) -> None:
    assert onboarding.find_setup(named, ["Lukas", "zoe-muller"]) == chosen
