"""`finanze init`: the first run. Asks who you are and makes your setup in `users/<name>/`.

A setup is one folder holding everything personal: the config, the rules, the categories
added, the instruments, the statements to import, and the database with its backups and
originals. The four files are written from the starter files of `src/finanze/starter/`, the
config with the answers filled in; a file that is already there is kept as it is. The paths
in the config start with `users/<name>/`: like every config's, they are relative to the
folder the commands run in.

`finanze` finds the setup by itself while it is the only one in `users/` and no
`finanze.yaml` is in the folder (see `dbcli._find_config`), and by `--user <name>` otherwise.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from importlib import resources
from pathlib import Path
from string import Template
from typing import TextIO

USERS = "users"  # the folder of the setups, in the folder the commands run in
CONFIG = "finanze.yaml"
STARTERS = ("rules.yaml", "categories.yaml", "instruments.yaml")  # copied as they are
STATEMENTS = "statements"
OK, FAILED = 0, 1
# An IBAN as typed: a country, two check digits, then letters and digits, maybe spaced.
_IBAN = re.compile(r"[A-Z]{2}\d{2}(?: ?[A-Z0-9]){10,30}", re.IGNORECASE)


def folder_name(name: str) -> str:
    """The folder a name is kept under: `Anna` -> `anna`, `Zoë Müller` -> `zoe-muller`."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9_-]+", "-", plain.lower()).strip("-")


def find_setup(named: str, found: Sequence[str]) -> str | None:
    """Which of the setups `found` (their folders' names) `named` means: the folder's own
    name, or what `folder_name` makes of it, ignoring case."""
    for candidate in (named, folder_name(named)):
        if candidate in found:
            return candidate
    folded = {name.casefold(): name for name in found}
    return folded.get(named.casefold()) or folded.get(folder_name(named).casefold())


def run(users: Path, out: TextIO, ask: Callable[[str], str]) -> int:
    """Ask, then write the setup; nothing is written when the name is taken."""
    earlier = users.parent / CONFIG
    if earlier.is_file():
        out.write(
            f"Note: {earlier} is a setup made before {users}/ existed. While it is there, it is "
            "the one `finanze` uses unless --user names another. To move it, put it with its "
            f"rules, categories and instruments files into {users}/<name>/ and point its "
            "paths there. A new one is made now.\n"
        )
    owner, folder = _name(ask, out)
    home = users / folder
    if (home / CONFIG).is_file():
        out.write(
            f"error: {home} is a setup already: use it with --user {folder}, or give another name\n"
        )
        return FAILED
    people = {owner: _printed(ask, "How is your name printed on your statements")}
    other = ask("Someone else in the same database, e.g. a partner (name, or Enter: no): ")
    if other.strip():
        people[other.strip()] = _printed(ask, f"How is {other.strip()}'s name printed")

    (home / STATEMENTS).mkdir(parents=True, exist_ok=True)
    written = [_write(home / CONFIG, config_text(home, owner, people))]
    starters = resources.files("finanze") / "starter"
    written += [
        _write(home / name, starters.joinpath(name).read_text("utf-8")) for name in STARTERS
    ]
    out.write(f"\nYour setup is in {home}/:\n")
    out.writelines(f"  {line}\n" for line in written)
    out.write(_next_steps(users, home))
    return OK


def config_text(home: Path, owner: str, people: Mapping[str, Sequence[str]]) -> str:
    """The starter config of the setup in `home`, with the answers in it. JSON strings are
    valid YAML, and quote whatever a name may hold."""
    template = (resources.files("finanze") / "starter" / CONFIG).read_text("utf-8")
    owners = "\n".join(
        f"  {json.dumps(name, ensure_ascii=False)}: {{match: "
        f"[{', '.join(json.dumps(pattern(m), ensure_ascii=False) for m in matches)}]}}"
        for name, matches in people.items()
    )
    return Template(template).substitute(
        home=home.as_posix(), owner=json.dumps(owner, ensure_ascii=False), owners=owners
    )


def pattern(printed: str) -> str:
    """The regular expression of a name as printed, or of an IBAN with or without its spaces:
    statements print it both ways."""
    if _IBAN.fullmatch(printed):
        return r"\s?".join(re.escape(char) for char in printed if not char.isspace())
    return re.escape(printed)


def _name(ask: Callable[[str], str], out: TextIO) -> tuple[str, str]:
    while True:
        name = ask("Your name (your folder is users/<name>/): ").strip()
        if folder_name(name):
            return name, folder_name(name)
        out.write("  a name with at least one letter or digit, please\n")


def _printed(ask: Callable[[str], str], question: str) -> list[str]:
    """Names as printed and IBANs, comma-separated; at least one."""
    while True:
        answer = ask(f"{question} (e.g. MARIO ROSSI; add IBANs after commas)? ")
        found = [part.strip() for part in answer.split(",") if part.strip()]
        if found:
            return found


def _write(path: Path, text: str) -> str:
    if path.exists():
        return f"{path.name}: kept, it was there already"
    path.write_text(text, encoding="utf-8")
    return f"{path.name}: written"


def _next_steps(users: Path, home: Path) -> str:
    command = "./uv run --no-dev finanze" if (users.parent / "uv").is_file() else "finanze"
    several = len(list(users.glob(f"*/{CONFIG}"))) > 1 or (users.parent / CONFIG).is_file()
    user = f" --user {home.name}" if several else ""
    return (
        "\nNext:\n"
        f"  1. Put your statement exports in {home / STATEMENTS}/\n"
        f"  2. {command} import{user} {home / STATEMENTS}\n"
        "       reads them and checks every balance\n"
        f"  3. {command} commit{user}      stores them\n"
        f"  4. {command} prices{user}      fetches the daily closes and exchange rates\n"
        f"  5. {command} serve{user}       the pages, on http://127.0.0.1:8765\n"
    )
