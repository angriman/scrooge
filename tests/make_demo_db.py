"""Builds a demo database with two years of invented history: `make demo`.

    uv run python -m tests.make_demo_db [folder]     (default: output/demo)

`make dev-db` holds the fixtures: one month, enough to develop against. This one is for
showing the app (the README's screenshots): the same invented people, Lukas and Anna Meier,
with the 24 months before the current one written out as their statements would be, in each
source's own format, and imported and committed through the services `scrooge import` and
`scrooge commit` use, so that every balance reconciles as a real one must.

Lukas has a Migros account his salary is paid into, Revolut for his card and an IBKR account
that buys an ETF every month and a stock now and then; Anna has her own Migros and Revolut
accounts and a finpension 3a she pays into every month. Each half of the rent goes from each
person's Migros account. Their pension certificates are the fixtures' scans. Every name,
amount and price is invented: the closes and rates are random walks, the same on every run
(`SEED`), and nothing connects (D9).

Like `make dev-db`, only a folder this script made is ever emptied, and never the real
database's.
"""

from __future__ import annotations

import random
import shutil
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import TextIO

import yaml

from scrooge import importing, marketdata, networth, overview, prices
from scrooge.storage import db
from tests.conftest import FIXTURES
from tests.fakes import FakeSource
from tests.make_dev_db import (
    OWNERS,
    PEOPLE_YAML,
    REAL,
    REAL_CONFIG,
    ROOT,
    SCANS,
    SETUPS,
    Built,
    RefusedError,
    _check,
    _configured,
    _load,
    _people,
    _shown,
    can_read_scans,
)

DEFAULT = ROOT / "output" / "demo"
DATABASE = "scrooge.db"
MARK = ".made-by-make-demo"
MONTHS = 24
SEED = 20261123
CENT = Decimal("0.01")
NICKEL = Decimal("0.05")  # francs are paid to the five centimes
ITALIAN = [
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
]
# Rules for the invented shops and payers below, before the starter's generic words.
RULES = """rules:
  - {match: 'IMMOBILIEN LINDENHOF', category: Rent}
  - {match: 'ACME SWITZERLAND|BEISPIEL PHARMA', category: Salary}
  - {match: 'KONSUM|FRISCHMARKT', category: Groceries}
  - {match: 'RAMEN|OSTERIA', category: Eating out}
  - {match: 'TELCO MOBILE', category: SIM}
  - {match: 'LISTENLY|VIDORA', category: Digital subscriptions}
  - {match: 'MODARIO', category: Clothes}
  - {match: 'FITPARK', category: Sport}
  - {match: 'ALPENBAHN', category: Trains}
  - {match: 'VELOSHARE', category: Local transport}
  - {match: 'AEROSUR', category: Flights}
  - {match: 'MOBILHAUS', category: Household}
  - {match: 'TECHNIKUM', category: Electronics}
  - {match: 'FINPENSION', category: Own transfer, kind: transfer}
"""
# Closes in the instrument's currency on the first day, and their yearly drift and volatility.
STOCKS = {
    "WIDG": ("48.20", 0.09, 0.15),
    "ACME": ("96.40", 0.12, 0.28),
    "GOOG": ("166.10", 0.15, 0.30),
}
FUNDS = {
    "CH0000000011": ("ALPHACH.SW", "Equities Switzerland", "1402", 0.06, 0.12),
    "CH0000000029": ("ALPHAWLD.SW", "Equities World ex CH", "1158", 0.09, 0.14),
}
RATES = {
    "USD": ("0.8850", -0.04, 0.06),
    "EUR": ("0.9420", -0.01, 0.04),
    "GBP": ("1.1210", 0.00, 0.05),
    "JPY": ("0.0058", -0.02, 0.08),
}
INSTRUMENTS = "instruments:\n  WIDG: {tab: ETFs}\n"


def build(
    folder: Path,
    out: TextIO,
    *,
    now: datetime | None = None,
    real: Sequence[Path] = REAL,
    config: Path = REAL_CONFIG,
) -> Built:
    """(Re)build the demo database in `folder`; what it prints goes to `out`."""
    folder = folder.expanduser().resolve()
    setups = sorted(SETUPS.glob("*/scrooge.yaml"))
    places = [place for c in (config, *setups) for place in _configured(c)]
    _check(folder, [*real, *places], MARK)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    (folder / MARK).write_text("made by make demo: emptied by the next run\n", encoding="utf-8")
    now = now or datetime.now(UTC)
    built = _files(folder)
    setup = importing.Setup([built.rules], built.instruments, _people(), {})
    first = _month(now.date(), -MONTHS)
    market = Market.walk(first - timedelta(days=7), now.date() - timedelta(days=1))
    scans = can_read_scans()
    if not scans:
        out.write("No Tesseract: the pension certificates are left out (make ocr).\n")
    conn = db.connect(built.database)
    try:
        db.migrate(conn, now)
        for owner, files in _statements(folder, first, market, scans=scans).items():
            _load(conn, built.database, owner, [files], setup=setup, now=now)
        refreshed = marketdata.refresh(
            conn, market.source(), through=market.last, now=now, currencies=networth.CURRENCIES
        )
        shown = overview.run(conn, currency="CHF")
    finally:
        conn.close()
    out.write(
        f"{_shown(built.database)}: {MONTHS} months for {' and '.join(OWNERS)}, "
        f"{refreshed.closes} closes and {refreshed.rates} rates; "
        f"net worth {shown.total:,.2f} CHF on {shown.day}\n"
    )
    return Built(built.database, built.config, built.rules, built.instruments, scans)


def _files(folder: Path) -> Built:
    """The config, the rules, the categories and the instruments the database is built with."""
    database = folder / DATABASE
    config = folder / "scrooge.yaml"
    config.write_text(
        "# The demo database: invented people and history, built by make demo.\n"
        + yaml.safe_dump(
            {"database": str(database), "owner": OWNERS[0], "owners": PEOPLE_YAML},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    starter = ROOT / "src" / "scrooge" / "starter"
    generic = (starter / "rules.yaml").read_text(encoding="utf-8").partition("rules:\n")[2]
    rules = folder / "rules.yaml"
    rules.write_text(RULES + generic, encoding="utf-8")
    shutil.copy(starter / "categories.yaml", folder / "categories.yaml")
    instruments = folder / "instruments.yaml"
    instruments.write_text(INSTRUMENTS, encoding="utf-8")
    return Built(database, config, rules, instruments, certificates=False)


def _month(day: date, shift: int) -> date:
    """The first day of the month `shift` months from `day`'s."""
    index = day.year * 12 + day.month - 1 + shift
    return date(index // 12, index % 12 + 1, 1)


def _months(first: date) -> Iterator[tuple[date, date]]:
    """The first and last day of each of the `MONTHS` months from `first`."""
    for n in range(MONTHS):
        start = _month(first, n)
        yield start, _month(first, n + 1) - timedelta(days=1)


# ----------------------------------------------------------------------------- the market


@dataclass(frozen=True, slots=True)
class Market:
    """Every close and rate of the demo, by symbol and day."""

    closes: Mapping[str, Mapping[date, Decimal]]
    last: date

    @classmethod
    def walk(cls, first: date, last: date) -> Market:
        draw = random.Random(SEED)  # noqa: S311  # invented figures, the same on every run
        days = [first + timedelta(days=n) for n in range((last - first).days + 1)]
        walks = {
            **STOCKS,
            **{symbol: (start, drift, vol) for symbol, _, start, drift, vol in FUNDS.values()},
            **{prices.rate_symbol(c, marketdata.BASE): spec for c, spec in RATES.items()},
        }
        return cls(
            {symbol: _walk(draw, days, *spec) for symbol, spec in walks.items()},
            last,
        )

    def close(self, symbol: str, day: date) -> Decimal:
        return self.closes[symbol][day]

    def rate(self, currency: str, day: date) -> Decimal:
        return self.close(prices.rate_symbol(currency, marketdata.BASE), day)

    def source(self) -> FakeSource:
        return FakeSource(
            {s: [(day, str(c)) for day, c in sorted(by.items())] for s, by in self.closes.items()},
            currencies={symbol: "CHF" for symbol, *_ in FUNDS.values()},
            isins={isin: symbol for isin, (symbol, *_) in FUNDS.items()},
        )


def _walk(
    draw: random.Random, days: Sequence[date], start: str, drift: float, volatility: float
) -> dict[date, Decimal]:
    """A geometric random walk from `start`, a step a day, kept to four significant places."""
    value = float(start)
    places = Decimal(1).scaleb(min(-2, Decimal(start).adjusted() - 3))
    walk = {}
    for day in days:
        walk[day] = Decimal(str(value)).quantize(places)
        value *= 1 + drift / 365 + volatility / 365**0.5 * draw.gauss(0, 1)
    return walk


# ----------------------------------------------------------------------------- statements


def _statements(folder: Path, first: date, market: Market, *, scans: bool) -> dict[str, list[Path]]:
    """Each owner's statement files, written under `statements/<owner>/`."""
    draw = random.Random(SEED)  # noqa: S311  # invented figures, the same on every run
    lukas = Household("Lukas", draw, salary=Decimal(7850), opening=Decimal("14250.35"))
    anna = Household("Anna", draw, salary=Decimal(6420), opening=Decimal("9810.80"))
    ibkr = Broker(market)
    pension = Pension(market)
    files: dict[str, list[Path]] = {owner: [] for owner in OWNERS}
    for owner in OWNERS:
        (folder / "statements" / owner).mkdir(parents=True)

    def write(owner: str, name: str, text: str) -> None:
        path = folder / "statements" / owner / name
        path.write_text(text, encoding="utf-8")
        files[owner].append(path)

    for start, end in _months(first):
        lukas.month(start, end, invest=Decimal(4500))
        anna.month(start, end, pension=Decimal("604.80"))
        pension.month(start, end, Decimal("604.80"))
        write("Lukas", f"Migros {start:%Y-%m}.csv", lukas.bank.statement(start, end))
        write("Anna", f"Migros {start:%Y-%m}.csv", anna.bank.statement(start, end))
        write("Lukas", f"IBKR {start:%Y-%m}.csv", ibkr.statement(start, end, Decimal(4500)))
    write("Lukas", "Revolut.tsv", lukas.card.statement())
    write("Anna", "Revolut.tsv", anna.card.statement())
    write("Anna", "finpension.tsv", pension.statement())
    if scans:
        for owner in OWNERS:
            for name, source in SCANS[owner].items():
                path = folder / "statements" / owner / name
                shutil.copy(FIXTURES / source / "certificate.pdf", path)
                files[owner].append(path)
    return files


def _chf(value: float) -> Decimal:
    return (Decimal(str(value)) / NICKEL).quantize(Decimal(1)) * NICKEL


def _at(day: date, draw: random.Random) -> str:
    return f"{day:%Y-%m-%d} {draw.randint(8, 21)}:{draw.randint(0, 59):02}:{draw.randint(0, 59):02}"


@dataclass(slots=True)
class Bank:
    """A Migros account: its rows, each with the balance after it."""

    name: str  # the holder, as the statement prints them
    iban: str
    balance: Decimal
    rows: list[tuple[date, str, str, Decimal]] = field(default_factory=list)  # day, text, note

    def book(self, day: date, text: str, amount: Decimal, note: str = "") -> None:
        self.rows.append((day, text, note, amount))

    def card(self, day: date, shop: str, amount: Decimal) -> None:
        self.book(
            day,
            f"Acquisto {shop} {day:%d.%m.%Y} 12:{day.day:02} Carta: 000000******0000 "
            f"Importo: CHF {amount:.2f}",
            -amount,
        )

    def statement(self, start: date, end: date) -> str:
        """The month's rows as Migros exports them, in the order they were booked."""
        lines = []
        for day, text, note, amount in sorted(
            (row for row in self.rows if start <= row[0] <= end), key=lambda row: row[0]
        ):
            self.balance += amount
            lines.append(
                f'"{day:%d.%m.%Y}";"{text}";"{note}";;"{amount:.2f}";"{self.balance:.2f}";'
                f'"{day:%d.%m.%Y}"'
            )
        return "\n".join(
            [
                f'"Estratto conto da:";"{start}"',
                f'"Estratto conto fino:";"{end}"',
                ";",
                '"Contratto:";"EB00000000"',
                f'"Numero di conto / IBAN:";"00.000.000 / {self.iban}"',
                '"Denominazione:";"Conto privato"',
                f'"Saldo:";"CHF {self.balance:.2f}"',
                ";",
                f'"{self.name}"',
                '"Musterstrasse 1"',
                '"8000 Zürich"',
                ";",
                ";",
                '"Data";"Testo di registrazione";"Comunicazione";"Numero di riferimento";'
                '"Importo";"Saldo";"Valuta"',
                *lines,
                "",
            ]
        )


@dataclass(slots=True)
class Card:
    """A Revolut account in francs: its rows in completion order."""

    draw: random.Random
    balance: Decimal = Decimal(0)
    rows: list[str] = field(default_factory=list)

    def pay(self, day: date, shop: str, amount: Decimal) -> None:
        self._row("Card Payment", day, shop, -amount)

    def top_up(self, day: date, amount: Decimal) -> None:
        self._row("Deposit", day, "Top-up by *1234", amount)

    def _row(self, kind: str, day: date, text: str, amount: Decimal) -> None:
        self.balance += amount
        at = _at(day, self.draw)
        self.rows.append(
            f"{kind}\tCurrent\t{at}\t{at}\t{text}\t{amount}\t0\tCHF\tCOMPLETED\t{self.balance}"
        )

    def statement(self) -> str:
        header = "Type\tProduct\tStarted Date\tCompleted Date\tDescription\tAmount\tFee\t"
        return "\n".join([header + "Currency\tState\tBalance", *self.rows, ""])


# Where each person spends by card: the shop, how often in a month, and a typical amount.
OUT = {
    "Lukas": (
        ("Pizzeria Bellavista", 2, 42),
        ("Cafe Lindenhof", 4, 9),
        ("Ramen Kumo", 1, 31),
        ("VELOSHARE", 3, 4),
        ("ALPENBAHN", 1, 58),
        ("Listenly", 1, 12.9),
        ("Kino Lichtspiel", 0.5, 19),
        ("Buchhandlung Seitenweise", 0.4, 28),
        ("Coiffeur Lumen", 0.5, 38),
    ),
    "Anna": (
        ("Bakery Kornfeld", 4, 8),
        ("Osteria del Ponte", 1.5, 54),
        ("Vidora", 1, 15.9),
        ("FITPARK Talstadt", 1, 79),
        ("Modario", 0.8, 86),
        ("Apotheke am Platz", 0.6, 24),
        ("Kunstmuseum Talstadt", 0.3, 22),
        ("ALPENBAHN", 0.8, 46),
    ),
}
# Card purchases on the bank account: groceries every week or so, and now and then a big one.
SHOPS = (("SUPERMARKET ALPINA ZUERICH", 5, 74), ("KONSUM-1234 ZUERICH", 2, 31))
BIG = (
    ("TECHNIKUM ZUERICH", 0.12, 640),
    ("MOBILHAUS DIETIKON", 0.1, 880),
    ("FRISCHMARKT OERLIKON", 0.5, 46),
)


@dataclass(slots=True)
class Household:
    """One person's money: the bank account their salary is paid into, and their card."""

    owner: str
    draw: random.Random
    salary: Decimal
    opening: Decimal
    bank: Bank = field(init=False)
    card: Card = field(init=False)

    def __post_init__(self) -> None:
        number = "1" if self.owner == "Lukas" else "2"
        self.bank = Bank(f"Meier {self.owner}", f"CH00 0000 0000 0000 0000 {number}", self.opening)
        self.card = Card(self.draw)

    def month(
        self, start: date, end: date, *, invest: Decimal = Decimal(0), pension: Decimal = Decimal(0)
    ) -> None:
        bank, draw, month = self.bank, self.draw, ITALIAN[start.month - 1]
        bank.book(
            start,
            "Immobilien Lindenhof AG, Seestrasse 12, 8002 Zürich",
            Decimal(-1075),
            f"Affitto {month} (metà)",
        )
        employer = (
            "ACME SWITZERLAND GMBH, 0000000001"
            if self.owner == "Lukas"
            else "Beispiel Pharma AG, 0000000002"
        )
        bank.book(start.replace(day=25), employer, self.salary, "Salario")
        if start.month == 12:
            bank.book(start.replace(day=20), employer, self.salary, "Tredicesima")
        insurance = Decimal("389.50") if self.owner == "Lukas" else Decimal("362.20")
        bank.book(start.replace(day=5), "Krankenversicherung Helvia AG, 6000 Luzern", -insurance)
        bank.book(start.replace(day=8), "Telco Mobile AG, Postfach, 8000 Zürich", Decimal("-39.90"))
        if self.owner == "Lukas" and start.month % 3 == 0:
            bill = _chf(draw.uniform(120, 190))
            bank.book(start.replace(day=12), "Elektrizitätswerk Talstadt, 8000 Zürich", -bill)
        if start.month == 3:
            tax = Decimal(4380) if self.owner == "Lukas" else Decimal(3620)
            bank.book(start.replace(day=28), "Steueramt Zürich, 8090 Zürich", -tax)
        if invest:
            bank.book(
                start.replace(day=10),
                "Interactive Brokers LLC, One Pickwick Plaza, 06830 Greenwich, Connecticut",
                -invest,
                "U1234567",
            )
        if pension:
            bank.book(start.replace(day=2), "finpension AG, 6300 Zug", -pension, "Pilastro 3a")
        days = (end - start).days + 1
        for shop, often, typical in SHOPS:
            for _ in range(self._times(often)):
                day = start + timedelta(days=draw.randrange(days))
                bank.card(day, shop, _chf(draw.uniform(0.5, 1.5) * typical))
        for shop, chance, typical in BIG:
            if draw.random() < chance:
                day = start + timedelta(days=draw.randrange(days))
                bank.card(day, shop, _chf(draw.uniform(0.6, 1.4) * typical))
        bank.book(end, "Spese per traffico pagamenti", Decimal("-4.40"))
        self._card(start, end)

    def _times(self, often: float) -> int:
        """How many times in a month something that happens `often` times a month does."""
        whole = int(often)
        return whole + (self.draw.random() < often - whole)

    def _card(self, start: date, end: date) -> None:
        """A month of card payments, topped up from the bank whenever the card runs low."""
        draw, days = self.draw, (end - start).days + 1
        payments = [
            (start + timedelta(days=draw.randrange(days)), shop, _chf(draw.uniform(0.6, 1.4) * t))
            for shop, often, t in OUT[self.owner]
            for _ in range(self._times(often))
        ]
        if draw.random() < 0.15:  # a trip
            day = start + timedelta(days=draw.randrange(days - 4))
            payments += [
                (day, "AEROSUR", _chf(draw.uniform(260, 520))),
                (day + timedelta(days=1), "Hotel Seeblick", _chf(draw.uniform(380, 720))),
                (day + timedelta(days=2), "Ristorante Al Lago", _chf(draw.uniform(70, 140))),
            ]
        for day, shop, amount in sorted(payments, key=lambda row: row[0]):
            if self.card.balance < amount + 50:
                top_up = Decimal(300) * (1 + int((amount + 50 - self.card.balance) // 300))
                self.card.top_up(day, top_up)
                self.bank.book(
                    day,
                    f"Acquisto Revolut**1234* {day:%d.%m.%Y} 07:00 Carta: 000000******0000 "
                    f"Importo: CHF {top_up:.2f}",
                    -top_up,
                )
            self.card.pay(day, shop, amount)


@dataclass(slots=True)
class Broker:
    """Lukas's IBKR account: francs in every month, changed into dollars and invested."""

    market: Market
    cash: dict[str, Decimal] = field(default_factory=lambda: {"CHF": Decimal(0), "USD": Decimal(0)})
    held: dict[str, Decimal] = field(default_factory=dict)  # shares
    cost: dict[str, Decimal] = field(default_factory=dict)  # dollars paid for them

    def statement(self, start: date, end: date, deposit: Decimal) -> str:
        """One month's activity statement: the deposit, the exchange, the buys, the dividend."""
        opening = dict(self.cash)
        deposits = [
            f"Deposits & Withdrawals,Data,CHF,{start.replace(day=12)},"
            f"Electronic Fund Transfer,{deposit}"
        ]
        self.cash["CHF"] += deposit
        trades: list[str] = []
        fx_day = start.replace(day=13)
        rate = self.market.rate("USD", fx_day)
        dollars = ((self.cash["CHF"] - 10) / rate).quantize(Decimal(1), ROUND_DOWN)
        paid = -(dollars * rate)
        self.cash["CHF"] += paid - 2
        self.cash["USD"] += dollars
        forex = [
            f'Trades,Data,Order,Forex,CHF,USD.CHF,"{fx_day}, 09:42:07",{dollars},{rate},,'
            f"{paid},-2,,,,0,"
        ]
        buys = [("WIDG", Decimal("0.6"))]
        if start.month % 4 == 1:
            buys.append(("ACME", Decimal("0.9")))
        elif start.month % 4 == 3:
            buys.append(("GOOG", Decimal("0.9")))
        trade_day = start.replace(day=15)
        for symbol, share in buys:
            price = self.market.close(symbol, trade_day)
            quantity = ((self.cash["USD"] - 5) * share / price).quantize(Decimal(1), ROUND_DOWN)
            if quantity <= 0:
                continue
            proceeds = -(quantity * price)
            self.cash["USD"] += proceeds - 1
            self.held[symbol] = self.held.get(symbol, Decimal(0)) + quantity
            self.cost[symbol] = self.cost.get(symbol, Decimal(0)) - proceeds + 1
            trades.append(
                f'Trades,Data,Order,Stocks,USD,{symbol},"{trade_day}, 15:31:07",{quantity},'
                f"{price},{price},{proceeds},-1,{-proceeds + 1},0,0,0,O"
            )
        dividends, withheld = [], []
        if start.month % 3 == 0 and self.held.get("WIDG"):
            paid_on = start.replace(day=22)
            gross = (self.held["WIDG"] * Decimal("0.38")).quantize(CENT)
            tax = -(gross * Decimal("0.15")).quantize(CENT)
            self.cash["USD"] += gross + tax
            what = "WIDG(US0000000002) Cash Dividend USD 0.38 per Share"
            dividends.append(f"Dividends,Data,USD,{paid_on},{what} (Ordinary Dividend),{gross}")
            withheld.append(f"Withholding Tax,Data,USD,{paid_on},{what} - US Tax,{tax},")
        return "\n".join(
            [
                "Statement,Header,Field Name,Field Value",
                "Statement,Data,BrokerName,Interactive Brokers (U.K.) Limited",
                "Statement,Data,Title,Activity Statement",
                f'Statement,Data,Period,"{start:%B} {start.day}, {start.year} - '
                f'{end:%B} {end.day}, {end.year}"',
                "Account Information,Header,Field Name,Field Value",
                "Account Information,Data,Name,Lukas Meier",
                "Account Information,Data,Account,U1234567",
                "Account Information,Data,Base Currency,CHF",
                "Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures",
                *(
                    f"Cash Report,Data,{label},{currency},{amount},{amount},0"
                    for currency in sorted(self.cash)
                    for label, amount in (
                        ("Starting Cash", opening[currency]),
                        ("Ending Cash", self.cash[currency]),
                    )
                ),
                "Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,"
                "Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Unrealized P/L %,Code",
                *self._positions(end),
                "Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,"
                "Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,Realized P/L %,"
                "MTM P/L,Code",
                *trades,
                "Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,"
                "T. Price,,Proceeds,Comm in CHF,,,,MTM in CHF,Code",
                *forex,
                "Deposits & Withdrawals,Header,Currency,Settle Date,Description,Amount",
                *deposits,
                "Dividends,Header,Currency,Date,Description,Amount",
                *dividends,
                "Withholding Tax,Header,Currency,Date,Description,Amount,Code",
                *withheld,
                "Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,"
                "Security ID,Underlying,Listing Exch,Multiplier,Type,Code",
                "Financial Instrument Information,Data,Stocks,ACME,ACME CORP,1001,US0000000001,"
                "ACME,NYSE,1,COMMON,",
                "Financial Instrument Information,Data,Stocks,WIDG,WIDGET WORLD ETF,1003,"
                "US0000000002,WIDG,ARCA,1,ETF,",
                "Financial Instrument Information,Data,Stocks,GOOG,ALPHABET INC-CL C,1004,"
                "US0000000003,GOOG,NASDAQ,1,COMMON,",
                "",
            ]
        )

    def _positions(self, end: date) -> Iterator[str]:
        for symbol, quantity in sorted(self.held.items()):
            close = self.market.close(symbol, end)
            cost = self.cost[symbol].quantize(CENT)
            value = (quantity * close).quantize(CENT)
            gain = value - cost
            yield (
                f"Open Positions,Data,Summary,Stocks,USD,{symbol},{quantity},1,"
                f"{(cost / quantity).quantize(CENT)},{cost},{close},{value},{gain},"
                f"{(gain / cost * 100).quantize(CENT)},"
            )


@dataclass(slots=True)
class Pension:
    """Anna's finpension 3a: a deposit a month, invested at once in two funds, 60 to 40."""

    market: Market
    balance: Decimal = Decimal(0)
    paid: Decimal = Decimal(0)  # every deposit so far, which the fee is a part of
    rows: list[str] = field(default_factory=list)

    def month(self, start: date, end: date, deposit: Decimal) -> None:
        self._row(start.replace(day=3), "Deposit", deposit)
        self.paid += deposit
        day = start.replace(day=4)
        for (isin, (symbol, name, *_)), share in zip(FUNDS.items(), ("0.6", "0.4"), strict=True):
            price = self.market.close(symbol, day)
            units = (deposit * Decimal(share) / price).quantize(Decimal("0.001"), ROUND_DOWN)
            self._row(day, "Buy", -(units * price), (name, isin, units, price))
        if end.month % 3 == 0:
            fee = (self.paid * Decimal("0.0004")).quantize(Decimal("0.0001"))
            self._row(end, "Flat-rate administrative fee", -fee)

    def _row(
        self,
        day: date,
        category: str,
        flow: Decimal,
        fund: tuple[str, str, Decimal, Decimal] | None = None,
    ) -> None:
        self.balance += flow
        name, isin, units, price = (
            (f"ALPHA (CH) Index Fund - {fund[0]} I-acc", fund[1], str(fund[2]), str(fund[3]))
            if fund
            else ("", "", "", "")
        )
        self.rows.append(
            f"{day}\t{category}\t{name}\t{isin}\t{units}\tCHF\t1\t{price}\t{flow}\t{self.balance}"
        )

    def statement(self) -> str:
        return "\n".join(
            [
                "Date\tCategory\tAsset Name\tISIN\tNumber of Shares\tAsset Currency\t"
                "Currency Rate\tAsset Price in CHF\tCash Flow\tBalance",
                *self.rows,
                "",
            ]
        )


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
