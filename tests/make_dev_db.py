"""Builds the development database from the redacted fixtures: `make dev-db`.

    uv run python -m tests.make_dev_db [folder]     (default: output/dev)

Once the real data lives on the NAS, the Mac only ever sees this one: two invented people,
Lukas and Anna Meier, whose statements are the fixtures under `tests/fixtures/`, imported and
committed through the services `scrooge import` and `scrooge commit` use (`importing`,
`staging`). The closes and exchange rates are invented, handed over by the source the tests
use (`tests/fakes.py`): nothing connects (D9). Next to the database go the config, the rules
and the instruments it was built with, so that `scrooge serve` shows it as it would the real
one.

The folder is made again from nothing on every run, and only a folder this script made is
ever emptied: one that holds anything without its mark is refused, and so is the real
database's folder, wherever a config or the default puts it.

The Swisscard fixture is the text of an invoice: it is turned into a PDF whose text layer
reads back as that text. The pension certificates are scans: without Tesseract they are left
out, and said so.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TextIO

import yaml

from scrooge import importing, marketdata, networth, overview, prices, staging
from scrooge.extractors import ocr
from scrooge.staging import People
from scrooge.storage import db
from tests.conftest import AUGUST_PURCHASE, FIXTURES, INSTRUMENTS_YAML, MATURED, RULES
from tests.fakes import FakeSource

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "output" / "dev"
DATABASE = "scrooge.db"
MARK = ".made-by-make-dev-db"  # what tells a folder this script may empty
# Where the real data is kept: never touched, whatever folder is asked for. The folder of the
# database each real config names (the one in the root, and every setup's in users/) is added
# to these.
REAL = (Path("~/.local/share/scrooge_db"), ROOT / "statements", ROOT / "users")
REAL_CONFIG = ROOT / "scrooge.yaml"
SETUPS = ROOT / "users"  # scrooge init's setups, one folder each
OWNERS = ("Lukas", "Anna")
PEOPLE_YAML = {"Lukas": {"match": ["LUKAS MEIER"]}, "Anna": {"match": ["ANNA MEIER"]}}
# Whose statement each fixture is, under the name it is imported as. A statement that prints
# its account number (IBKR, Migros, Swisscard) can be one person's only; the card invoice goes
# with the bank statement that pays it, whose debits it is checked against.
STATEMENTS: Mapping[str, Mapping[str, str]] = {
    "Lukas": {
        "Migros.csv": "migros/migros.csv",
        "IBKR.csv": "ibkr/activity.csv",
        "Revolut CHF.tsv": "revolut/chf.tsv",
    },
    "Anna": {
        "tastytrade.csv": "tastytrade/transactions.csv",
        "Revolut Savings.tsv": "revolut_savings/savings.tsv",
        "finpension.tsv": "finpension/transactions.tsv",
        "Schwab.csv": "schwab/transactions.csv",
    },
}
CARD = ("Lukas", "swisscard.pdf", "swisscard/statement.txt")
SCANS: Mapping[str, Mapping[str, str]] = {
    "Lukas": {"AXA 2026-01.pdf": "axa_yearly", "AXA 2026-03.pdf": "axa"},
    "Anna": {"Servisa 2026-01.pdf": "servisa"},
}
# What is held: the bill Anna's September statement sees mature.
INSTRUMENTS = INSTRUMENTS_YAML + MATURED
# The rules naming the invented people and shops of the fixtures come first, then those of
# the starter rules.yaml. What no rule matches is a present, so that
# everything can be committed.
SHOPS = """  - {match: 'ALPENBAHN', category: Treni}
  - {match: 'VELOSHARE', category: Trasporti locali}
  - {match: 'AEROSUR', category: Voli}
  - {match: 'MOBILHAUS', category: Arredamento e casa}
  - {match: 'TECHNIKUM', category: Elettronica}
  - {match: 'FOOD RUN', category: "Ristoranti e bar"}
  - {match: 'KASSA BILLING', category: Acquisti vari}
"""
FALLBACK = "\n  # make dev-db: whatever is left\n  - {match: '.', category: Regali}\n"
# Invented closes, in the instrument's currency, from the first day prices are asked for.
CLOSES = {"ACME": "113.1", "GOOG": "204.3", "WIDG": "54.25"}
FUNDS = {"CH0000000011": ("ALPHACH.SW", "1510"), "CH0000000029": ("ALPHAWLD.SW", "1310")}
RATES = {"USD": "0.80", "EUR": "0.94", "GBP": "1.07", "JPY": "0.0054"}
FIRST = date(2024, 12, 1)


class RefusedError(Exception):
    """The folder is not one this script may empty."""


@dataclass(frozen=True, slots=True)
class Built:
    """What `build` made."""

    database: Path
    config: Path
    rules: Path
    instruments: Path
    certificates: bool  # the scans were read: Tesseract is installed

    def serve(self) -> str:
        """The command that shows it."""
        return (
            f"uv run scrooge serve --config {_shown(self.config)} --rules {_shown(self.rules)} "
            f"--instruments {_shown(self.instruments)}"
        )


def _shown(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def build(
    folder: Path,
    out: TextIO,
    *,
    now: datetime | None = None,
    real: Iterable[Path] = REAL,
    config: Path = REAL_CONFIG,
) -> Built:
    """(Re)build the development database in `folder`; what it prints goes to `out`.

    `real` are the folders of the real data, and `config` the real config: its database's
    folder, and that of every setup in users/, are among them. None of them is ever touched.
    """
    folder = folder.expanduser().resolve()
    setups = sorted(SETUPS.glob("*/scrooge.yaml"))
    _check(folder, [*real, *(place for c in (config, *setups) for place in _configured(c))])
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    (folder / MARK).write_text("made by make dev-db: emptied by the next run\n", encoding="utf-8")
    now = now or datetime.now(UTC)
    built = _files(folder)
    setup = importing.Setup([built.rules], built.instruments, _people(), {})
    scans = can_read_scans()
    if not scans:
        out.write(
            f"No `{ocr.PROGRAM}` on PATH: the pension certificates are left out "
            "(make ocr to have them).\n"
        )
    conn = db.connect(built.database)
    try:
        db.migrate(conn, now)
        for owner in OWNERS:
            imports = _statements(folder, owner, scans=scans)
            _load(conn, built.database, owner, imports, setup=setup, now=now)
        refreshed = _prices(conn, now)
        if refreshed.problems:
            msg = f"prices missing: {'; '.join(refreshed.problems)}"
            raise RuntimeError(msg)
        shown = overview.run(conn, currency="CHF")
        committed = conn.execute(
            "SELECT o.name, count(*) FROM statement_files f JOIN owners o ON o.id = f.owner_id "
            "WHERE f.committed_at IS NOT NULL GROUP BY o.name ORDER BY o.name DESC"
        ).fetchall()
    finally:
        conn.close()
    out.writelines(f"  {name}: {count} statements committed\n" for name, count in committed)
    out.write(
        f"  {refreshed.closes} invented closes and {refreshed.rates} exchange rates stored\n"
        f"{_shown(built.database)}: net worth {shown.total:,.2f} CHF on {shown.day} "
        f"({', '.join(part.name for part in shown.assets)})\n"
    )
    return Built(built.database, built.config, built.rules, built.instruments, scans)


def can_read_scans() -> bool:
    """Whether Tesseract is there to read the certificates' scans."""
    return shutil.which(ocr.PROGRAM) is not None


def _check(folder: Path, real: Iterable[Path], mark: str = MARK) -> None:
    """Refuse the real data's folders, and any folder with something in it this script did
    not make (no `mark` in it): what is there is never removed."""
    for path in real:
        place = path.expanduser().resolve()
        if folder == place or folder in place.parents or place in folder.parents:
            msg = f"{folder}: the real data is kept at {place}: the dev database goes elsewhere"
            raise RefusedError(msg)
    if folder.exists() and (not folder.is_dir() or _foreign(folder, mark)):
        maker = mark.removeprefix(".made-by-").replace("-", " ", 1)
        msg = f"{folder}: not made by {maker} (no {mark} in it): nothing is removed"
        raise RefusedError(msg)


def _configured(config: Path) -> list[Path]:
    """The folder of the database `config` names, if it names one."""
    if not config.is_file():
        return []
    found = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    database = found.get("database") if isinstance(found, dict) else None
    # Relative to the folder the commands run in: the repository's.
    return [(ROOT / Path(str(database)).expanduser()).parent] if database else []


def _foreign(folder: Path, mark: str) -> bool:
    return any(folder.iterdir()) and not (folder / mark).is_file()


def _files(folder: Path) -> Built:
    """The config, the rules, the categories and the instruments the database is built with."""
    database = folder / DATABASE
    config = folder / "scrooge.yaml"
    config.write_text(
        "# The development database: invented people, built by make dev-db from the fixtures.\n"
        + yaml.safe_dump(
            {"database": str(database), "owner": OWNERS[0], "owners": PEOPLE_YAML},
            sort_keys=False,
        )
        + "# No 'serve': on this machine only (127.0.0.1), no password.\n",
        encoding="utf-8",
    )
    # Never the repository's own rules.yaml or categories.yaml: those are the user's, real.
    starter = ROOT / "src" / "scrooge" / "starter"
    generic = (starter / "rules.yaml").read_text(encoding="utf-8").partition("rules:\n")[2]
    own = RULES.replace("  - {match: '.', category: Regali}\n", SHOPS).rstrip("\n") + "\n"
    rules = folder / "rules.yaml"
    rules.write_text(own + generic.rstrip("\n") + FALLBACK, encoding="utf-8")
    shutil.copy(starter / "categories.yaml", folder / "categories.yaml")
    instruments = folder / "instruments.yaml"
    instruments.write_text(INSTRUMENTS, encoding="utf-8")
    return Built(database, config, rules, instruments, certificates=False)


def _people() -> People:
    return {
        name: [re.compile(match, re.IGNORECASE) for match in entry["match"]]
        for name, entry in PEOPLE_YAML.items()
    }


def _statements(folder: Path, owner: str, *, scans: bool) -> list[list[Path]]:
    """The owner's statement files, copied under `statements/<owner>/`, in the imports they
    arrive in: what came before first."""
    place = folder / "statements" / owner
    place.mkdir(parents=True)
    imports: list[list[Path]] = []
    if owner == "Anna":
        august = place / "tastytrade August.csv"
        august.write_text(AUGUST_PURCHASE, encoding="utf-8")
        imports.append([august])
    files = []
    for name, source in STATEMENTS[owner].items():
        shutil.copy(FIXTURES / source, place / name)
        files.append(place / name)
    if owner == CARD[0]:
        text = (FIXTURES / CARD[2]).read_text(encoding="utf-8")
        (place / CARD[1]).write_bytes(text_pdf(text.splitlines()))
        files.append(place / CARD[1])
    if scans:
        for name, source in SCANS[owner].items():
            shutil.copy(FIXTURES / source / "certificate.pdf", place / name)
            files.append(place / name)
    imports.append(files)
    return imports


def _load(
    conn: sqlite3.Connection,
    database: Path,
    owner: str,
    imports: Sequence[Sequence[Path]],
    *,
    setup: importing.Setup,
    now: datetime,
) -> None:
    """Import and commit each batch for `owner`, as `scrooge import` and `commit` would."""
    for files in imports:
        found = importing.stage(conn, database, importing.read(owner, files, setup), setup, now)
        if found.refused or not found.staged or found.staged.uncategorized:
            problems = "; ".join(f"{f.name}: {f.problem}" for f in found.refused)
            msg = f"{owner}: the fixtures did not import as they should: {problems or found}"
            raise RuntimeError(msg)
        staging.commit_with_copy(conn, database, owner=owner, now=now)


def _prices(conn: sqlite3.Connection, now: datetime) -> marketdata.Refreshed:
    """Invented closes and rates up to yesterday, stored as `scrooge prices` stores them."""
    last = now.date() - timedelta(days=1)
    days = [last - timedelta(days=n) for n in range((last - FIRST).days, -1, -1)]
    bases = {**CLOSES, **dict(FUNDS.values())}
    closes = {symbol: _series(days, last, base) for symbol, base in bases.items()}
    for currency, base in RATES.items():
        closes[prices.rate_symbol(currency, marketdata.BASE)] = _series(days, last, base)
    source = FakeSource(
        closes,
        currencies={symbol: "CHF" for symbol, _ in FUNDS.values()},
        isins={isin: symbol for isin, (symbol, _) in FUNDS.items()},
    )
    return marketdata.refresh(conn, source, through=last, now=now, currencies=networth.CURRENCIES)


def _series(days: Sequence[date], last: date, base: str) -> list[tuple[date, str]]:
    """`base` on `last`, and up to 1.5% below it on the days before, back and forth."""
    shape = [Decimal(abs((last - day).days % 30 - 15) - 15) / 1000 for day in days]
    return [(day, str(Decimal(base) * (1 + step))) for day, step in zip(days, shape, strict=True)]


# ------------------------------------------------------------------------- the card's PDF


def text_pdf(lines: Sequence[str]) -> bytes:
    """A one-page PDF whose text layer is `lines`, one under the other, in Courier."""
    content = ["BT /F1 8 Tf 11 TL 30 810 Td"]
    for line in lines:
        data = line.encode("cp1252")  # the PDF's WinAnsiEncoding: accents and the en dash
        escaped = data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
        content.append(f"({escaped.decode('latin-1')}) Tj T*")
    content.append("ET")
    stream = "\n".join(content).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>",
        b"<< /Length %d >>\nstream\n%b\nendstream" % (len(stream), stream),
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n%b\nendobj\n" % (number, body)
    table = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    pdf += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        table,
    )
    return bytes(pdf)


def main(argv: Sequence[str], out: TextIO) -> int:
    folder = Path(argv[0]) if argv else DEFAULT
    try:
        built = build(folder, out)
    except RefusedError as exc:
        out.write(f"error: {exc}\n")
        return 1
    out.write(f"Serve it with:\n  {built.serve()}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdout))
