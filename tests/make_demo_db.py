"""Builds a demo database with two years of invented history: `make demo`.

    uv run python -m tests.make_demo_db [folder]     (default: output/demo)

`make dev-db` holds the fixtures: one month, enough to develop against. This one is for
showing the app (the README's screenshots): the same invented people, Lukas and Anna Meier,
with the 24 months before the current one written out as their statements would be, in each
source's own format, and imported and committed through the services `scrooge import` and
`scrooge commit` use, so that every balance reconciles as a real one must.

Lukas has a Migros EUR account his salary is paid into, Revolut for his card and an IBKR
account that buys a world ETF in euros every month plus European and US stocks now and then;
Anna has her own Migros EUR and Revolut accounts and a finpension 3a she pays into every month.
Each half of the rent goes from each person's bank account. Every name, amount and price is
invented: the closes and rates are random walks, the same on every run (`SEED`), and nothing
connects (D9).

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

from scrooge import benchmark, importing, marketdata, networth, overview, prices
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
CURRENCY = "EUR"
MONTHS = 24
SEED = 20261123
CENT = Decimal("0.01")
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
    "WIDG": ("EUR", "54.20", 0.09, 0.14),
    "VELO": ("EUR", "68.50", 0.10, 0.22),
    "ACME": ("USD", "96.40", 0.12, 0.26),
    "GOOG": ("USD", "166.10", 0.24, 0.26),
}
FUNDS = {
    "CH0000000011": ("ALPHACH.SW", "Equities Switzerland", "1402", 0.06, 0.12),
    "CH0000000029": ("ALPHAWLD.SW", "Equities World ex CH", "1158", 0.09, 0.14),
}
# The benchmarks the investments are set beside (`benchmark.DEFAULT`), in dollars: held by no one.
BENCHMARKS = {"VT": ("112.30", 0.08, 0.15), "VOO": ("468.50", 0.10, 0.16)}
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
            conn,
            market.source(),
            through=market.last,
            now=now,
            currencies=networth.CURRENCIES,
            benchmarks=benchmark.DEFAULT,
        )
        shown = overview.run(conn, currency=CURRENCY)
    finally:
        conn.close()
    out.write(
        f"{_shown(built.database)}: {MONTHS} months for {' and '.join(OWNERS)}, "
        f"{refreshed.closes} closes and {refreshed.rates} rates; "
        f"net worth {shown.total:,.2f} {CURRENCY} on {shown.day}\n"
    )
    return Built(built.database, built.config, built.rules, built.instruments, scans)


def _files(folder: Path) -> Built:
    """The config, the rules, the categories and the instruments the database is built with."""
    database = folder / DATABASE
    config = folder / "scrooge.yaml"
    config.write_text(
        "# The demo database: invented people and history, built by make demo.\n"
        + yaml.safe_dump(
            {
                "database": str(database),
                "owner": OWNERS[0],
                "currency": CURRENCY,
                "owners": PEOPLE_YAML,
            },
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
            **{symbol: spec[1:] for symbol, spec in STOCKS.items()},
            **{symbol: (start, drift, vol) for symbol, _, start, drift, vol in FUNDS.values()},
            **{prices.rate_symbol(c, marketdata.BASE): spec for c, spec in RATES.items()},
            **BENCHMARKS,  # last: the walks before them stay as they were
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
            currencies={
                **{symbol: currency for symbol, (currency, *_) in STOCKS.items()},
                **{symbol: "CHF" for symbol, *_ in FUNDS.values()},
            },
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
    lukas = Household("Lukas", draw, salary=Decimal(2450), opening=Decimal("2850.35"))
    anna = Household("Anna", draw, salary=Decimal(2050), opening=Decimal("2180.80"))
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
        lukas.month(start, end, invest=Decimal(900))
        anna.month(start, end, pension=Decimal(220))
        pension.month(start, end, Decimal(205))
        write("Lukas", f"Migros {start:%Y-%m}.csv", lukas.bank.statement(start, end))
        write("Anna", f"Migros {start:%Y-%m}.csv", anna.bank.statement(start, end))
        write("Lukas", f"IBKR {start:%Y-%m}.csv", ibkr.statement(start, end, Decimal(900)))
    write("Lukas", "Revolut.tsv", lukas.card.statement())
    write("Anna", "Revolut.tsv", anna.card.statement())
    write("Anna", "finpension.tsv", pension.statement())
    if scans:
        for name, source in SCANS["Anna"].items():
            path = folder / "statements" / "Anna" / name
            shutil.copy(FIXTURES / source / "certificate.pdf", path)
            files["Anna"].append(path)
    return files


def _eur(value: float) -> Decimal:
    return Decimal(str(value)).quantize(CENT)


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
            f"Importo: EUR {amount:.2f}",
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
                f'"Saldo:";"EUR {self.balance:.2f}"',
                ";",
                f'"{self.name}"',
                '"Musterstrasse 1"',
                '"8000 Zürich"',
                ";",
                ";",
                (
                    '"Data";"Testo di registrazione";"Comunicazione";"Numero di riferimento";'
                    '"Importo";"Saldo";"Valuta"'
                ),
                *lines,
                "",
            ]
        )


@dataclass(slots=True)
class Card:
    """A Revolut account in euros: its rows in completion order."""

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
            f"{kind}\tCurrent\t{at}\t{at}\t{text}\t{amount}\t0\tEUR\tCOMPLETED\t{self.balance}"
        )

    def statement(self) -> str:
        header = "Type\tProduct\tStarted Date\tCompleted Date\tDescription\tAmount\tFee\t"
        return "\n".join([header + "Currency\tState\tBalance", *self.rows, ""])


# Where each person spends by card: the shop, how often in a month, and a typical amount.
OUT = {
    "Lukas": (
        ("Pizzeria Bellavista", 2, 26),
        ("Cafe Lindenhof", 4, 5.5),
        ("Ramen Kumo", 1, 19),
        ("VELOSHARE", 3, 3),
        ("ALPENBAHN", 1, 34),
        ("Listenly", 1, 10.99),
        ("Kino Lichtspiel", 0.5, 13),
        ("Buchhandlung Seitenweise", 0.4, 21),
        ("Coiffeur Lumen", 0.5, 26),
    ),
    "Anna": (
        ("Bakery Kornfeld", 4, 5.5),
        ("Osteria del Ponte", 1.5, 36),
        ("Vidora", 1, 12.99),
        ("FITPARK Talstadt", 1, 45),
        ("Modario", 0.8, 58),
        ("Apotheke am Platz", 0.6, 18),
        ("Kunstmuseum Talstadt", 0.3, 14),
        ("ALPENBAHN", 0.8, 29),
    ),
}
# Card purchases on the bank account: groceries every week or so, and now and then a big one.
SHOPS = (("SUPERMARKET ALPINA", 4, 48), ("KONSUM-1234", 2, 22))
BIG = (
    ("TECHNIKUM", 0.1, 290),
    ("MOBILHAUS", 0.08, 380),
    ("FRISCHMARKT", 0.4, 32),
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
            Decimal(-625),
            f"Affitto {month} (metà)",
        )
        employer = (
            "ACME SWITZERLAND GMBH, 0000000001"
            if self.owner == "Lukas"
            else "Beispiel Pharma AG, 0000000002"
        )
        bank.book(start.replace(day=25), employer, self.salary, "Salario")
        insurance = Decimal("145.50") if self.owner == "Lukas" else Decimal("138.20")
        bank.book(start.replace(day=5), "Krankenversicherung Helvia AG, 6000 Luzern", -insurance)
        bank.book(start.replace(day=8), "Telco Mobile AG, Postfach, 8000 Zürich", Decimal("-19.90"))
        if self.owner == "Lukas" and start.month % 3 == 0:
            bill = _eur(draw.uniform(85, 130))
            bank.book(start.replace(day=12), "Elektrizitätswerk Talstadt, 8000 Zürich", -bill)
        if start.month == 3:
            tax = Decimal(580) if self.owner == "Lukas" else Decimal(440)
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
                bank.card(day, shop, _eur(draw.uniform(0.6, 1.4) * typical))
        for shop, chance, typical in BIG:
            if draw.random() < chance:
                day = start + timedelta(days=draw.randrange(days))
                bank.card(day, shop, _eur(draw.uniform(0.7, 1.3) * typical))
        bank.book(end, "Spese per traffico pagamenti", Decimal("-2.50"))
        self._card(start, end)

    def _times(self, often: float) -> int:
        """How many times in a month something that happens `often` times a month does."""
        whole = int(often)
        return whole + (self.draw.random() < often - whole)

    def _card(self, start: date, end: date) -> None:
        """A month of card payments, topped up from the bank whenever the card runs low."""
        draw, days = self.draw, (end - start).days + 1
        payments = [
            (start + timedelta(days=draw.randrange(days)), shop, _eur(draw.uniform(0.6, 1.4) * t))
            for shop, often, t in OUT[self.owner]
            for _ in range(self._times(often))
        ]
        if draw.random() < 0.12:  # a trip
            day = start + timedelta(days=draw.randrange(days - 4))
            payments += [
                (day, "AEROSUR", _eur(draw.uniform(140, 280))),
                (day + timedelta(days=1), "Hotel Seeblick", _eur(draw.uniform(210, 390))),
                (day + timedelta(days=2), "Ristorante Al Lago", _eur(draw.uniform(55, 95))),
            ]
        for day, shop, amount in sorted(payments, key=lambda row: row[0]):
            if self.card.balance < amount + 40:
                top_up = Decimal(200) * (1 + int((amount + 40 - self.card.balance) // 200))
                self.card.top_up(day, top_up)
                self.bank.book(
                    day,
                    f"Acquisto Revolut**1234* {day:%d.%m.%Y} 07:00 Carta: 000000******0000 "
                    f"Importo: EUR {top_up:.2f}",
                    -top_up,
                )
            self.card.pay(day, shop, amount)


@dataclass(slots=True)
class Broker:
    """Lukas's IBKR account: euros in every month, invested in euro ETFs/stocks and US stocks."""

    market: Market
    cash: dict[str, Decimal] = field(default_factory=lambda: {"EUR": Decimal(0), "USD": Decimal(0)})
    held: dict[str, Decimal] = field(default_factory=dict)  # shares
    cost: dict[str, Decimal] = field(default_factory=dict)  # paid in the stock's currency

    def statement(self, start: date, end: date, deposit: Decimal) -> str:
        """One month's activity statement: the deposit, any USD exchange, the buys, the dividend."""
        opening = dict(self.cash)
        deposits = [
            (
                f"Deposits & Withdrawals,Data,EUR,{start.replace(day=12)},"
                f"Electronic Fund Transfer,{deposit}"
            )
        ]
        self.cash["EUR"] += deposit
        trades: list[str] = []
        forex: list[str] = []
        usd_pick = "ACME" if start.month % 4 == 1 else "GOOG" if start.month % 4 == 3 else ""
        if usd_pick:
            fx_day = start.replace(day=13)
            eur_per_usd = (
                self.market.rate("USD", fx_day) / self.market.rate("EUR", fx_day)
            ).quantize(Decimal("0.0001"))
            dollars = ((self.cash["EUR"] * Decimal("0.42")) / eur_per_usd).quantize(
                Decimal(1), ROUND_DOWN
            )
            paid = -(dollars * eur_per_usd)
            self.cash["EUR"] += paid - 1
            self.cash["USD"] += dollars
            forex.append(
                f'Trades,Data,Order,Forex,EUR,USD.EUR,"{fx_day}, 09:42:07",{dollars},'
                f"{eur_per_usd},,{paid},-1,,,,0,"
            )
        buys: list[tuple[str, Decimal]] = [("WIDG", Decimal("0.65"))]
        if start.month % 4 == 2:
            buys.append(("VELO", Decimal("0.85")))
        elif usd_pick:
            buys.append((usd_pick, Decimal("0.9")))
        trade_day = start.replace(day=15)
        for symbol, share in buys:
            ccy = STOCKS[symbol][0]
            price = self.market.close(symbol, trade_day)
            quantity = ((self.cash[ccy] - 5) * share / price).quantize(Decimal(1), ROUND_DOWN)
            if quantity <= 0:
                continue
            proceeds = -(quantity * price)
            self.cash[ccy] += proceeds - 1
            self.held[symbol] = self.held.get(symbol, Decimal(0)) + quantity
            self.cost[symbol] = self.cost.get(symbol, Decimal(0)) - proceeds + 1
            trades.append(
                f'Trades,Data,Order,Stocks,{ccy},{symbol},"{trade_day}, 15:31:07",{quantity},'
                f"{price},{price},{proceeds},-1,{-proceeds + 1},0,0,0,O"
            )
        dividends, withheld = [], []
        if start.month % 3 == 0 and self.held.get("WIDG"):
            paid_on = start.replace(day=22)
            gross = (self.held["WIDG"] * Decimal("0.34")).quantize(CENT)
            tax = -(gross * Decimal("0.15")).quantize(CENT)
            self.cash["EUR"] += gross + tax
            what = "WIDG(DE0000000002) Cash Dividend EUR 0.34 per Share"
            dividends.append(f"Dividends,Data,EUR,{paid_on},{what} (Ordinary Dividend),{gross}")
            withheld.append(f"Withholding Tax,Data,EUR,{paid_on},{what} - Tax,{tax},")
        return "\n".join(
            [
                "Statement,Header,Field Name,Field Value",
                "Statement,Data,BrokerName,Interactive Brokers Ireland Limited",
                "Statement,Data,Title,Activity Statement",
                (
                    f'Statement,Data,Period,"{start:%B} {start.day}, {start.year} - '
                    f'{end:%B} {end.day}, {end.year}"'
                ),
                "Account Information,Header,Field Name,Field Value",
                "Account Information,Data,Name,Lukas Meier",
                "Account Information,Data,Account,U1234567",
                "Account Information,Data,Base Currency,EUR",
                "Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures",
                *(
                    f"Cash Report,Data,{label},{currency},{amount},{amount},0"
                    for currency in sorted(self.cash)
                    for label, amount in (
                        ("Starting Cash", opening[currency]),
                        ("Ending Cash", self.cash[currency]),
                    )
                ),
                (
                    "Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,"
                    "Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,"
                    "Unrealized P/L %,Code"
                ),
                *self._positions(end),
                (
                    "Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,"
                    "Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,"
                    "Realized P/L %,MTM P/L,Code"
                ),
                *trades,
                (
                    "Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,"
                    "Quantity,T. Price,,Proceeds,Comm in EUR,,,,MTM in EUR,Code"
                ),
                *forex,
                "Deposits & Withdrawals,Header,Currency,Settle Date,Description,Amount",
                *deposits,
                "Dividends,Header,Currency,Date,Description,Amount",
                *dividends,
                "Withholding Tax,Header,Currency,Date,Description,Amount,Code",
                *withheld,
                (
                    "Financial Instrument Information,Header,Asset Category,Symbol,Description,"
                    "Conid,Security ID,Underlying,Listing Exch,Multiplier,Type,Code"
                ),
                (
                    "Financial Instrument Information,Data,Stocks,ACME,ACME CORP,1001,US0000000001,"
                    "ACME,NYSE,1,COMMON,"
                ),
                (
                    "Financial Instrument Information,Data,Stocks,WIDG,WIDGET WORLD ETF,1003,"
                    "DE0000000002,WIDG,IBIS,1,ETF,"
                ),
                (
                    "Financial Instrument Information,Data,Stocks,GOOG,ALPHABET INC-CL C,1004,"
                    "US0000000003,GOOG,NASDAQ,1,COMMON,"
                ),
                (
                    "Financial Instrument Information,Data,Stocks,VELO,VELOX INDUSTRIE SE,1005,"
                    "DE0000000004,VELO,IBIS,1,COMMON,"
                ),
                "",
            ]
        )

    def _positions(self, end: date) -> Iterator[str]:
        for symbol, quantity in sorted(self.held.items()):
            ccy = STOCKS[symbol][0]
            close = self.market.close(symbol, end)
            cost = self.cost[symbol].quantize(CENT)
            value = (quantity * close).quantize(CENT)
            gain = value - cost
            yield (
                f"Open Positions,Data,Summary,Stocks,{ccy},{symbol},{quantity},1,"
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
                (
                    "Date\tCategory\tAsset Name\tISIN\tNumber of Shares\tAsset Currency\t"
                    "Currency Rate\tAsset Price in CHF\tCash Flow\tBalance"
                ),
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
