"""IBKR Activity Statement (CSV) -> normalized transactions.

The CSV is a stack of sections, each `<Section>,<Header|Data|SubTotal|Total|Notes>,...`, and a
section may repeat its header with different columns (Trades does, once per asset class).
Reconciliation targets come from the Cash Report (trade-date basis, `Total` column).
"""

from __future__ import annotations

import calendar
import csv
import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from finanze.errors import ExtractionError, UnsupportedStatementError
from finanze.models import CashBalance, Extraction, Kind, SecurityTrade, Transaction


class _Row(dict[str, str]):
    """One data row keyed by its section's header. A missing column is a format error."""

    def __init__(self, section: str, header: list[str], cells: list[str]) -> None:
        super().__init__(zip(header, cells, strict=False))
        self.section = section

    def __missing__(self, column: str) -> str:
        msg = f"{self.section}: no column {column!r}"
        raise ExtractionError(msg)

    def is_total(self) -> bool:
        # Total rows put "Total..." in the first column (Currency, or Subtitle for Fees).
        return next(iter(self.values()), "").startswith("Total")


_Sections = dict[str, list[_Row]]

_PERIOD = re.compile(r"(\w+) (\d{1,2}), (\d{4}) - (\w+) (\d{1,2}), (\d{4})")
_MONTHS = {name: number for number, name in enumerate(calendar.month_name) if name}
_OPTION_CATEGORIES = {"Options On Futures", "Equity and Index Options"}
_FX_CATEGORY_CURRENCIES = {"EUR", "USD"}  # Acquisto <ccy> exists only for these


def extract(content: str, source_file: str) -> Extraction:
    """Parse the text of an IBKR Activity Statement CSV."""
    sections = _read_sections(content.removeprefix("\ufeff"))
    start, end = _period(_fields(sections, "Statement"))
    parser = _Parser(source_file, start, end)

    transactions: list[Transaction] = []
    transactions += parser.deposits_withdrawals(sections.get("Deposits & Withdrawals", []))
    transactions += parser.trades(sections.get("Trades", []))
    transactions += parser.dividends(sections.get("Dividends", []))
    transactions += parser.cash_items(
        "Withholding Tax", sections.get("Withholding Tax", []), Kind.TAX, "Ritenute"
    )
    transactions += parser.cash_items(
        "Interest", sections.get("Interest", []), Kind.INTEREST, "Interessi"
    )
    transactions += parser.cash_items(
        "Fees", sections.get("Fees", []), Kind.COMMISSION, "Commissioni"
    )
    balances = _balances(sections.get("Cash Report", []))
    return Extraction(month=start, transactions=tuple(transactions), balances=balances)


# ------------------------------------------------------------------ CSV plumbing


def _read_sections(content: str) -> _Sections:
    sections: _Sections = {}
    headers: dict[str, list[str]] = {}
    for row in csv.reader(io.StringIO(content)):
        if len(row) < 2:
            continue
        name, row_type, cells = row[0], row[1], row[2:]
        if row_type == "Header":
            headers[name] = cells
        elif row_type == "Data":
            if name not in headers:
                msg = f"{name}: data row before any header row"
                raise ExtractionError(msg)
            sections.setdefault(name, []).append(_Row(name, headers[name], cells))
    return sections


def _fields(sections: _Sections, name: str) -> dict[str, str]:
    return {row["Field Name"]: row["Field Value"] for row in sections.get(name, [])}


def _decimal(text: str, what: str) -> Decimal:
    try:
        return Decimal(text.replace(",", "").strip())
    except InvalidOperation:
        msg = f"cannot read amount {text!r} ({what})"
        raise ExtractionError(msg) from None


def _iso_date(text: str, what: str) -> date:
    try:
        return date.fromisoformat(text.split(",", maxsplit=1)[0].strip())
    except ValueError:
        msg = f"cannot read date {text!r} ({what})"
        raise ExtractionError(msg) from None


def _period(statement: dict[str, str]) -> tuple[date, date]:
    text = statement.get("Period", "")
    match = _PERIOD.fullmatch(text)
    if not match or match[1] not in _MONTHS or match[4] not in _MONTHS:
        msg = f"cannot read statement period {text!r}"
        raise ExtractionError(msg)
    start = date(int(match[3]), _MONTHS[match[1]], int(match[2]))
    end = date(int(match[6]), _MONTHS[match[4]], int(match[5]))
    last_day = calendar.monthrange(start.year, start.month)[1]
    if start.day != 1 or end != date(start.year, start.month, last_day):
        msg = f"statement must cover exactly one calendar month, got {text!r}"
        raise ExtractionError(msg)
    return start, end


def _balances(rows: list[_Row]) -> tuple[CashBalance, ...]:
    if not rows:
        msg = "statement has no Cash Report section"
        raise ExtractionError(msg)
    opening: dict[str, Decimal] = {}
    closing: dict[str, Decimal] = {}
    for row in rows:
        currency = row["Currency"]
        if currency == "Base Currency Summary":
            continue
        target = {"Starting Cash": opening, "Ending Cash": closing}.get(row["Currency Summary"])
        if target is not None:
            target[currency] = _decimal(row["Total"], f"Cash Report {row['Currency Summary']}")
    for label, found in (("Starting Cash", opening), ("Ending Cash", closing)):
        missing = (opening.keys() | closing.keys()) - found.keys()
        if missing:
            msg = f"Cash Report has no {label} for {sorted(missing)}"
            raise ExtractionError(msg)
    return tuple(
        CashBalance(_account(ccy), ccy, opening[ccy], closing[ccy]) for ccy in sorted(opening)
    )


def _account(currency: str) -> str:
    return f"IBKR {currency}"


# ------------------------------------------------------------------ section parsers


class _Parser:
    def __init__(self, source_file: str, start: date, end: date) -> None:
        self._source_file = source_file
        self._start = start
        self._end = end

    def _txn(  # noqa: PLR0913  # builder mirroring the Transaction fields it fills
        self,
        *,
        day: date,
        currency: str,
        amount: Decimal,
        description: str,
        kind: Kind,
        category: str,
        ref: str,
        counterparty: str = "",
    ) -> Transaction:
        return Transaction(
            date=self._in_period(day),
            account=_account(currency),
            amount=amount,
            currency=currency,
            description=description,
            kind=kind,
            source_file=self._source_file,
            source_ref=ref,
            counterparty=counterparty,
            category=category,
        )

    def _in_period(self, day: date) -> date:
        if not self._start <= day <= self._end:
            msg = f"{day} is outside the statement period {self._start} to {self._end}"
            raise ExtractionError(msg)
        return day

    def deposits_withdrawals(self, rows: list[_Row]) -> list[Transaction]:
        out: list[Transaction] = []
        for n, row in enumerate(rows, start=1):
            if row.is_total():
                continue
            amount = _decimal(row["Amount"], "Deposits & Withdrawals")
            description = row["Description"]
            category = ""
            if description.startswith("Electronic Fund Transfer") and amount > 0:
                category = "Topup IBKR"
            elif description.startswith("Disbursement") and amount < 0:
                category = "Prelievi"
            out.append(
                self._txn(
                    day=_iso_date(row["Settle Date"], "Deposits & Withdrawals"),
                    currency=row["Currency"],
                    amount=amount,
                    description=description,
                    kind=Kind.TRANSFER,
                    category=category,
                    ref=f"Deposits & Withdrawals:{n}",
                )
            )
        return out

    def cash_items(
        self, section: str, rows: list[_Row], kind: Kind, category: str
    ) -> list[Transaction]:
        out: list[Transaction] = []
        for n, row in enumerate(rows, start=1):
            if row.is_total():
                continue
            description = row["Description"]
            ticker = re.match(r"(\S+?)\(", description)
            out.append(
                self._txn(
                    day=_iso_date(row["Date"], section),
                    currency=row["Currency"],
                    amount=_decimal(row["Amount"], section),
                    description=description,
                    kind=kind,
                    category=category,
                    ref=f"{section}:{n}",
                    counterparty=ticker[1] if ticker else "",
                )
            )
        return out

    def dividends(self, rows: list[_Row]) -> list[Transaction]:
        # Payments in lieu of dividends are booked as dividends too.
        return self.cash_items("Dividends", rows, Kind.DIVIDEND, "Dividendi")

    def trades(self, rows: list[_Row]) -> list[Transaction]:
        out: list[Transaction] = []
        for n, row in enumerate(rows, start=1):
            if row["DataDiscriminator"] != "Order":
                msg = f"Trades row {n}: unexpected discriminator {row['DataDiscriminator']!r}"
                raise ExtractionError(msg)
            category = row["Asset Category"]
            ref = f"Trades:{n}"
            if category == "Stocks":
                out.append(self._security_trade(row, ref))
            elif category in _OPTION_CATEGORIES:
                out += self._option_trade(row, ref)
            elif category == "Forex":
                out += self._forex_trade(row, ref)
            else:
                msg = f"Trades row {n}: unsupported asset category {category!r}"
                raise UnsupportedStatementError(msg)
            out += self._commission(row, ref)
        return out

    def _commission(self, row: _Row, ref: str) -> list[Transaction]:
        # Forex rows label the column "Comm in <base ccy>": the fee is charged in that currency.
        column = next((key for key in row if key.startswith("Comm")), None)
        if column is None:
            msg = f"Trades {ref}: no commission column"
            raise ExtractionError(msg)
        currency = column.removeprefix("Comm in ") if column.startswith("Comm in ") else None
        amount = _decimal(row[column], "commission")
        if amount == 0:
            return []
        return [
            self._txn(
                day=_iso_date(row["Date/Time"], "Trades"),
                currency=currency or row["Currency"],
                amount=amount,
                description=f"Commission {row['Symbol']}",
                kind=Kind.COMMISSION,
                category="Commissioni",
                ref=f"{ref}:comm",
            )
        ]

    def _security_trade(self, row: _Row, ref: str) -> SecurityTrade:
        quantity = _decimal(row["Quantity"], "Trades quantity")
        price = _decimal(row["T. Price"], "Trades price")
        proceeds = _decimal(row["Proceeds"], "Trades proceeds")
        symbol = row["Symbol"]
        return SecurityTrade(
            date=self._in_period(_iso_date(row["Date/Time"], "Trades")),
            account=_account(row["Currency"]),
            amount=proceeds,
            currency=row["Currency"],
            description=_describe(quantity, symbol, price),
            kind=Kind.SECURITY_TRADE,
            source_file=self._source_file,
            source_ref=ref,
            category="Investimenti",
            ticker=symbol,
            quantity=quantity,
            price=price,
            cost=-proceeds,
        )

    def _option_trade(self, row: _Row, ref: str) -> list[Transaction]:
        proceeds = _decimal(row["Proceeds"], "Trades proceeds")
        if proceeds == 0:  # expiry: no cash moves
            return []
        quantity = _decimal(row["Quantity"], "Trades quantity")
        price = _decimal(row["T. Price"], "Trades price")
        return [
            self._txn(
                day=_iso_date(row["Date/Time"], "Trades"),
                currency=row["Currency"],
                amount=proceeds,
                description=_describe(quantity, row["Symbol"], price),
                kind=Kind.TRADING,
                category="Trading",
                ref=ref,
            )
        ]

    def _forex_trade(self, row: _Row, ref: str) -> list[Transaction]:
        base, _, quote = row["Symbol"].partition(".")
        if quote != row["Currency"]:
            msg = f"Trades {ref}: quote currency of {row['Symbol']} is not {row['Currency']}"
            raise ExtractionError(msg)
        day = _iso_date(row["Date/Time"], "Trades")
        quantity = _decimal(row["Quantity"], "Trades quantity")
        proceeds = _decimal(row["Proceeds"], "Trades proceeds")
        bought = base if quantity > 0 else quote
        category = f"Acquisto {bought}" if bought in _FX_CATEGORY_CURRENCIES else ""
        description = f"FX {row['Symbol']} @ {format(_decimal(row['T. Price'], 'FX price'), 'f')}"
        return [
            self._txn(
                day=day,
                currency=currency,
                amount=amount,
                description=description,
                kind=Kind.FX,
                category=category,
                ref=f"{ref}:{leg}",
            )
            for leg, currency, amount in (("base", base, quantity), ("quote", quote, proceeds))
        ]


def _describe(quantity: Decimal, symbol: str, price: Decimal) -> str:
    side = "Buy" if quantity > 0 else "Sell"
    return f"{side} {format(abs(quantity), 'f')} {symbol} @ {format(price, 'f')}"
