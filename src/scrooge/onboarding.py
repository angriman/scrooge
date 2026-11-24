"""`scrooge init`: the first run. Asks who you are and makes your setup in `users/<name>/`.

A setup is one folder holding everything personal: the config, the rules, the categories
added, the instruments, the statements to import, and the database with its backups and
originals. The four files are written from the starter files of `src/scrooge/starter/`, the
config with the answers filled in; a file that is already there is kept as it is. The paths
in the config start with `users/<name>/`: like every config's, they are relative to the
folder the commands run in.

`scrooge` finds the setup by itself while it is the only one in `users/` and no
`scrooge.yaml` is in the folder (see `dbcli._find_config`), and by `--user <name>` otherwise.

`scrooge init --container` makes it for the app in a container (deploy/README.md): the pages
are reached from outside it, so the config's `serve` listens on every address of the container
and names the password asked for, and the next steps are `docker compose` commands.
"""

from __future__ import annotations

import getpass
import json
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from importlib import resources
from pathlib import Path
from string import Template
from typing import TextIO

from scrooge import networth
from scrooge.web import auth

USERS = "users"  # the folder of the setups, in the folder the commands run in
CONFIG = "scrooge.yaml"
STARTERS = ("rules.yaml", "categories.yaml", "instruments.yaml")  # copied as they are
STATEMENTS = "statements"
OK, FAILED = 0, 1
_DEFAULT_CURRENCY = "CHF"
# Where the pages are seen from, written down for later: on this machine only until then.
SERVE_LATER = """\
# To reach the pages from other devices, with a password (`scrooge password` prints it):
# serve:
#   host: 0.0.0.0
#   names: [nas.example.ts.net]
#   password: scrypt$...  # pragma: allowlist secret
"""
COMPOSE = "docker compose exec scrooge scrooge"  # a command in the container of compose.yaml
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


def run(users: Path, out: TextIO, ask: Callable[[str], str], *, container: bool = False) -> int:
    """Ask, then write the setup; nothing is written when the name is taken. In a `container`
    the pages are served beyond it: under the names given, with a password."""
    earlier = users.parent / CONFIG
    if earlier.is_file():
        out.write(
            f"Note: {earlier} is a setup made before {users}/ existed. While it is there, it is "
            "the one `scrooge` uses unless --user names another. To move it, put it with its "
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
    currency = _currency(ask)
    serve = _served(ask, out) if container else SERVE_LATER

    (home / STATEMENTS).mkdir(parents=True, exist_ok=True)
    written = [_write(home / CONFIG, config_text(home, owner, people, currency, serve))]
    starters = resources.files("scrooge") / "starter"
    written += [
        _write(home / name, starters.joinpath(name).read_text("utf-8")) for name in STARTERS
    ]
    out.write(f"\nYour setup is in {home}/:\n")
    out.writelines(f"  {line}\n" for line in written)
    out.write(_next_steps(users, home, container=container))
    return OK


def config_text(
    home: Path,
    owner: str,
    people: Mapping[str, Sequence[str]],
    currency: str,
    serve: str = SERVE_LATER,
) -> str:
    """The starter config of the setup in `home`, with the answers in it. JSON strings are
    valid YAML, and quote whatever a name may hold. `serve` is the text of its end: where
    the pages are seen from."""
    template = (resources.files("scrooge") / "starter" / CONFIG).read_text("utf-8")
    owners = "\n".join(
        f"  {json.dumps(name, ensure_ascii=False)}: {{match: "
        f"[{', '.join(json.dumps(pattern(m), ensure_ascii=False) for m in matches)}]}}"
        for name, matches in people.items()
    )
    return Template(template).substitute(
        home=home.as_posix(),
        owner=json.dumps(owner, ensure_ascii=False),
        owners=owners,
        currency=currency,
        serve=serve,
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


def _currency(ask: Callable[[str], str]) -> str:
    choices = ", ".join(networth.CURRENCIES)
    while True:
        answer = ask(f"Show figures in ({choices}) [{_DEFAULT_CURRENCY}]: ").strip().upper()
        if not answer:
            return _DEFAULT_CURRENCY
        if answer in networth.CURRENCIES:
            return answer


def _served(ask: Callable[[str], str], out: TextIO) -> str:
    """`serve` for the app in a container: every address of it (compose.yaml says which of
    the host's ports leads there), the names typed in a browser, and a password."""
    answer = ask("What is typed in a browser to reach the pages, comma-separated [localhost]: ")
    names = [name.strip() for name in answer.split(",") if name.strip()] or ["localhost"]
    while True:
        password = getpass.getpass("A password for the pages: ")  # not shown as typed
        if len(password) < auth.SHORTEST:
            out.write(f"  at least {auth.SHORTEST} characters, please\n")
        elif getpass.getpass("Once more: ") != password:
            out.write("  the two do not match, again please\n")
        else:
            break
    listed = ", ".join(json.dumps(name, ensure_ascii=False) for name in names)
    return (
        "# The pages, from the container they run in (`scrooge init --container`): on every\n"
        "# address of it, under these names, with this password (`scrooge password` prints\n"
        "# another).\n"
        "serve:\n"
        "  host: 0.0.0.0\n"
        f"  names: [{listed}]\n"
        f"  password: {auth.digest(password)}\n"
    )


def _write(path: Path, text: str) -> str:
    if path.exists():
        return f"{path.name}: kept, it was there already"
    path.write_text(text, encoding="utf-8")
    return f"{path.name}: written"


def _next_steps(users: Path, home: Path, *, container: bool) -> str:
    command = "./uv run --no-dev scrooge" if (users.parent / "uv").is_file() else "scrooge"
    several = len(list(users.glob(f"*/{CONFIG}"))) > 1 or (users.parent / CONFIG).is_file()
    user = f" --user {home.name}" if several else ""
    if container:
        return (
            "\nNext, in the folder of compose.yaml:\n"
            "  1. docker compose up -d\n"
            "       starts the pages (http://localhost:8765 unless .env says another port)\n"
            "       and the daily prices\n"
            "  2. Import statements on the Import page, or put them in the data folder's\n"
            f"     {home / STATEMENTS}/ and run:\n"
            f"     {COMPOSE} import{user} {home / STATEMENTS}\n"
            f"     {COMPOSE} commit{user}\n"
        )
    return (
        "\nNext:\n"
        f"  1. Put your statement exports in {home / STATEMENTS}/\n"
        f"  2. {command} import{user} {home / STATEMENTS}\n"
        "       reads them and checks every balance\n"
        f"  3. {command} commit{user}      stores them\n"
        f"  4. {command} prices{user}      fetches the daily closes and exchange rates\n"
        f"  5. {command} serve{user}       the pages, on http://127.0.0.1:8765\n"
    )
