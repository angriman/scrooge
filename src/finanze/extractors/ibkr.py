"""IBKR Activity Statement (CSV) -> normalized transactions.

The CSV is a stack of sections, each `<Section>,<Header|Data|SubTotal|Total|Notes>,...`, and a
section may repeat its header with different columns (Trades does, once per asset class).
Reconciliation targets come from the Cash Report (trade-date basis, `Total` column).
Older statements suffix asset categories with " - Held with ... carried by ...".
"""

from __future__ import annotations

import calendar
import csv
import io
import re
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from finanze.errors import ExtractionError, UnsupportedStatementError
from finanze.extractors.common import parse_decimal, parse_iso_date
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
_HOME_CURRENCY = "CHF"  # converting back into it is a move between one's own accounts
# Shares promised and taken back before they vest never were in the account.
_UNVESTED_GRANTS = ("Stock Award Grant", "Stock Award Return")
_STOCK_TABS = {"ETF": "ETFs", "COMMON": "Stocks", "ADR": "Stocks", "REIT": "Stocks"}
_T_BILL_MATURITY = re.compile(r"Treasury Bill Maturity \((\w+),")


def extract(content: str, source_file: str) -> Extraction:
    """Parse the text of an IBKR Activity Statement CSV."""
    sections = _read_sections(content.removeprefix("\ufeff"))
    start, end = _period(_fields(sections, "Statement"))
    instruments = _Instruments(
        maturities=_maturities(sections.get("Financial Instrument Information", [])),
        asset_tabs=_asset_tabs(sections.get("Financial Instrument Information", [])),
        futures=_futures_positions(sections.get("Mark-to-Market Performance Summary", [])),
    )
    parser = _Parser(source_file, start, end, instruments)

    transactions: list[Transaction] = []
    transactions += parser.deposits_withdrawals(sections.get("Deposits & Withdrawals", []))
    transactions += parser.trades(sections.get("Trades", []))
    transactions += parser.corporate_actions(sections.get("Corporate Actions", []))
    transactions += parser.grants(sections.get("Grant Activity", []))
    parser.check_transfers(sections.get("Transfers", []))
    transactions += parser.dividends(sections.get("Dividends", []))
    transactions += parser.cash_items(
        "Withholding Tax",
        sections.get("Withholding Tax", []),
        Kind.TAX,
        "Ritenute",
        backdated=True,  # corrections keep the original payment date
    )
    transactions += parser.cash_items(
        "Interest", sections.get("Interest", []), Kind.INTEREST, "Interessi"
    )
    transactions += parser.fees(sections.get("Fees", []))
    if "GST/VAT Details" in sections:
        transactions += parser.vat(sections["GST/VAT Details"])
    else:  # yearly exports itemise no VAT, only the Cash Report total
        transactions += parser.sales_tax(sections.get("Cash Report", []))
    balances = _balances(sections.get("Cash Report", []))
    return Extraction(
        month=start,
        transactions=tuple(transactions),
        balances=balances,
        end=end,
        account_id=_fields(sections, "Account Information").get("Account", ""),
    )


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


def _period(statement: dict[str, str]) -> tuple[date, date]:
    text = statement.get("Period", "")
    match = _PERIOD.fullmatch(text)
    if not match or match[1] not in _MONTHS or match[4] not in _MONTHS:
        msg = f"cannot read statement period {text!r}"
        raise ExtractionError(msg)
    start = date(int(match[3]), _MONTHS[match[1]], int(match[2]))
    end = date(int(match[6]), _MONTHS[match[4]], int(match[5]))
    if start.day != 1 or end.day != calendar.monthrange(end.year, end.month)[1] or end < start:
        msg = f"statement must cover whole calendar months, got {text!r}"
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
            target[currency] = parse_decimal(row["Total"], f"Cash Report {row['Currency Summary']}")
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


def _asset_category(row: _Row) -> str:
    return row["Asset Category"].partition(" - Held with ")[0]


def _maturities(rows: list[_Row]) -> dict[str, date]:
    return {
        row["Symbol"]: parse_iso_date(row["Maturity"], "Financial Instrument Information")
        for row in rows
        if _asset_category(row) == "Treasury Bills"
    }


def _asset_tabs(rows: list[_Row]) -> dict[str, str]:
    """Stock symbol -> asset tab. IBKR files ETFs under "Stocks" but gives the type here (D12).

    A symbol with aliases is listed as "B26A, CEBE". Types other than ETF and COMMON are left
    out: such trades need an `instruments.yaml` entry.
    """
    tabs: dict[str, str] = {}
    for row in rows:
        tab = _STOCK_TABS.get(row["Type"]) if _asset_category(row) == "Stocks" else None
        if tab:
            tabs.update(dict.fromkeys(row["Symbol"].split(", "), tab))
    return tabs


def _futures_positions(rows: list[_Row]) -> dict[str, tuple[Decimal, Decimal]]:
    """Futures symbol -> (prior, current) quantity, from the Mark-to-Market summary."""
    return {
        row["Symbol"]: (
            parse_decimal(row["Prior Quantity"], "Mark-to-Market prior quantity"),
            parse_decimal(row["Current Quantity"], "Mark-to-Market current quantity"),
        )
        for row in rows
        if _asset_category(row) == "Futures"
    }


# ------------------------------------------------------------------ section parsers


@dataclass(frozen=True, slots=True)
class _Instruments:
    """What the statement's reference sections say about the traded instruments."""

    maturities: dict[str, date]
    asset_tabs: dict[str, str]
    futures: dict[str, tuple[Decimal, Decimal]]


class _Parser:
    def __init__(self, source_file: str, start: date, end: date, instruments: _Instruments) -> None:
        self._source_file = source_file
        self._start = start
        self._end = end
        self._maturities = instruments.maturities
        self._asset_tabs = instruments.asset_tabs
        self._futures = instruments.futures

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
        backdated: bool = False,
    ) -> Transaction:
        booked = self._in_period(day, backdated=backdated)
        if booked != day:
            description = f"{description} (dated {day})"
        return Transaction(
            date=day,
            account=_account(currency),
            amount=amount,
            currency=currency,
            description=description,
            kind=kind,
            source_file=self._source_file,
            source_ref=ref,
            counterparty=counterparty,
            category=category,
            booking_date=booked,
        )

    def _in_period(self, day: date, *, backdated: bool = False) -> date:
        # Some rows IBKR books in this period still carry an earlier date: those are booked
        # on the first day of the period when the caller allows it.
        if backdated and day < self._start:
            return self._start
        if not self._start <= day <= self._end:
            msg = f"{day} is outside the statement period {self._start} to {self._end}"
            raise ExtractionError(msg)
        return day

    def deposits_withdrawals(self, rows: list[_Row]) -> list[Transaction]:
        out: list[Transaction] = []
        for n, row in enumerate(rows, start=1):
            if row.is_total():
                continue
            amount = parse_decimal(row["Amount"], "Deposits & Withdrawals")
            description = row["Description"]
            kind, category = Kind.TRANSFER, ""
            if description.startswith("Electronic Fund Transfer") and amount > 0:
                category = "Topup IBKR"
            elif description.startswith("Disbursement") and amount < 0:
                category = "Prelievi"
            elif description.startswith("Commission Adjustment"):  # e.g. CAT fee refund
                kind, category = Kind.COMMISSION, "Commissioni"
            out.append(
                self._txn(
                    day=parse_iso_date(row["Settle Date"], "Deposits & Withdrawals"),
                    currency=row["Currency"],
                    amount=amount,
                    description=description,
                    kind=kind,
                    category=category,
                    ref=f"Deposits & Withdrawals:{n}",
                )
            )
        return out

    def cash_items(
        self,
        section: str,
        rows: list[_Row],
        kind: Kind,
        category: str,
        *,
        backdated: bool = False,
    ) -> list[Transaction]:
        out: list[Transaction] = []
        for n, row in enumerate(rows, start=1):
            if row.is_total():
                continue
            description = row["Description"]
            ticker = re.match(r"(\S+?)\(", description)
            out.append(
                self._txn(
                    day=parse_iso_date(row["Date"], section),
                    currency=row["Currency"],
                    amount=parse_decimal(row["Amount"], section),
                    description=description,
                    kind=kind,
                    category=category,
                    ref=f"{section}:{n}",
                    counterparty=ticker[1] if ticker else "",
                    backdated=backdated,
                )
            )
        return out

    def fees(self, rows: list[_Row]) -> list[Transaction]:
        # Positive rows are fee reversals or waivers (same description as the charge), except
        # referral bonuses: those are income, left for review.
        return [
            replace(txn, kind=Kind.INCOME, category="")
            if txn.description.startswith("REFERRER CREDIT")
            else txn
            for txn in self.cash_items("Fees", rows, Kind.COMMISSION, "Commissioni")
        ]

    def vat(self, rows: list[_Row]) -> list[Transaction]:
        # VAT on market data fees; reversals carry the opposite sign. Totals are `Total` rows.
        out: list[Transaction] = []
        for n, row in enumerate(rows, start=1):
            out.append(
                self._txn(
                    day=parse_iso_date(row["Date"], "GST/VAT Details"),
                    currency=row["Currency"],
                    amount=parse_decimal(row["GST/VAT"], "GST/VAT Details"),
                    description=f"VAT {row['Description'].strip()}",
                    kind=Kind.COMMISSION,
                    category="Commissioni",
                    ref=f"GST/VAT Details:{n}",
                )
            )
        return out

    def sales_tax(self, rows: list[_Row]) -> list[Transaction]:
        # The Base Currency Summary block labels its Sales Tax line with the base currency, so
        # only lines inside their own currency's block (after its Starting Cash) count.
        out: list[Transaction] = []
        block = ""
        for row in rows:
            currency = row["Currency"]
            if row["Currency Summary"] == "Starting Cash":
                block = currency
            elif row["Currency Summary"] == "Sales Tax" and currency == block:
                out.append(
                    self._txn(
                        day=self._end,
                        currency=currency,
                        amount=parse_decimal(row["Total"], "Cash Report Sales Tax"),
                        description="Sales Tax (VAT on fees)",
                        kind=Kind.COMMISSION,
                        category="Commissioni",
                        ref=f"Cash Report:Sales Tax:{currency}",
                    )
                )
        return out

    def corporate_actions(self, rows: list[_Row]) -> list[Transaction]:
        out: list[Transaction] = []
        for n, row in enumerate(rows, start=1):
            if row.is_total():
                continue
            proceeds = parse_decimal(row["Proceeds"], "Corporate Actions proceeds")
            if proceeds == 0:  # splits and the like: no cash moves
                continue
            description = row["Description"]
            match = _T_BILL_MATURITY.search(description)
            if _asset_category(row) != "Treasury Bills" or not match:
                msg = f"Corporate Actions row {n}: unsupported corporate action {description!r}"
                raise UnsupportedStatementError(msg)
            out += self._t_bill_maturity(row, match[1], proceeds, f"Corporate Actions:{n}")
        return out

    def grants(self, rows: list[_Row]) -> list[Transaction]:
        """Shares the broker awards (for a deposit) come into the account the day they vest.

        No cash moves. What they were worth that day is their cost, so that a later sale
        does not show the whole proceeds as a gain. Vesting rows count the shares as they are
        after any split in between, so the split of unvested shares needs no row of its own.
        """
        out: list[Transaction] = []
        for n, row in enumerate(rows, start=1):
            description = row["Description"]
            if row.is_total() or description.startswith(_UNVESTED_GRANTS):
                continue
            if description != "Stock Award Vesting":
                msg = f"Grant Activity row {n}: unsupported grant activity {description!r}"
                raise UnsupportedStatementError(msg)
            symbol = row["Symbol"]
            quantity = parse_decimal(row["Quantity"], "Grant Activity quantity")
            price = parse_decimal(row["Price"], "Grant Activity price")
            # The statement says nowhere which currency the award is in: the broker's own.
            out.append(
                SecurityTrade(
                    date=self._in_period(parse_iso_date(row["Report Date"], "Grant Activity")),
                    account=_account("USD"),
                    amount=Decimal(0),
                    currency="USD",
                    description=f"{description}: {format(quantity, 'f')} {symbol} "
                    f"@ {format(price, 'f')}",
                    kind=Kind.SECURITY_TRADE,
                    source_file=self._source_file,
                    source_ref=f"Grant Activity:{n}",
                    category="Investimenti",
                    ticker=symbol,
                    quantity=quantity,
                    price=price,
                    cost=parse_decimal(row["Value"], "Grant Activity value"),
                    asset_tab=self._asset_tabs.get(symbol, ""),
                )
            )
        return out

    def _t_bill_maturity(
        self, row: _Row, cusip: str, proceeds: Decimal, ref: str
    ) -> list[Transaction]:
        # D13 on IBKR's basis: principal = proceeds - realized P/L (FIFO cost incl. the buy
        # commission), interest = realized P/L. Dated on the maturity (report) date.
        interest = parse_decimal(row["Realized P/L"], "Corporate Actions realized P/L")
        day = parse_iso_date(row["Report Date"], "Corporate Actions")
        legs = (
            ("principal", proceeds - interest, Kind.INCOME, "Investimenti"),
            ("interest", interest, Kind.INTEREST, "Interessi"),
        )
        return [
            self._txn(
                day=day,
                currency=row["Currency"],
                amount=amount,
                description=f"T-bill {cusip} maturity: {leg}",
                kind=kind,
                category=category,
                ref=f"{ref}:{leg}",
                counterparty=cusip,
            )
            for leg, amount, kind, category in legs
            if amount != 0
        ]

    def check_transfers(self, rows: list[_Row]) -> None:
        # Position transfers (e.g. shares moved in from another broker) move no cash here.
        for n, row in enumerate(rows, start=1):
            if row.is_total():
                continue
            cash = parse_decimal(row["Cash Amount"], "Transfers cash amount")
            if cash != 0:
                msg = f"Transfers row {n}: transfer of {row['Symbol']} moves cash {cash}"
                raise UnsupportedStatementError(msg)

    def dividends(self, rows: list[_Row]) -> list[Transaction]:
        # Payments in lieu of dividends are booked as dividends too.
        return self.cash_items("Dividends", rows, Kind.DIVIDEND, "Dividendi")

    def trades(self, rows: list[_Row]) -> list[Transaction]:
        out: list[Transaction] = []
        for n, row in enumerate(rows, start=1):
            if row["DataDiscriminator"] != "Order":
                msg = f"Trades row {n}: unexpected discriminator {row['DataDiscriminator']!r}"
                raise ExtractionError(msg)
            category = _asset_category(row)
            ref = f"Trades:{n}"
            if category == "Stocks":
                out.append(self._security_trade(row, ref, row["Symbol"]))
            elif category == "Treasury Bills":
                out.append(self._t_bill_trade(row, ref))
            elif category == "Futures":
                out.append(self._futures_trade(row, ref))
            elif category in _OPTION_CATEGORIES:
                out += self._option_trade(row, ref)
            elif category == "Forex":
                out += self._forex_trade(row, ref)
            else:
                msg = f"Trades row {n}: unsupported asset category {category!r}"
                raise UnsupportedStatementError(msg)
            out += self._commission(row, ref, backdated=category == "Forex")
        return out

    def _commission(self, row: _Row, ref: str, *, backdated: bool) -> list[Transaction]:
        # Forex rows label the column "Comm in <base ccy>": the fee is charged in that currency.
        column = next((key for key in row if key.startswith("Comm")), None)
        if column is None:
            msg = f"Trades {ref}: no commission column"
            raise ExtractionError(msg)
        currency = column.removeprefix("Comm in ") if column.startswith("Comm in ") else None
        amount = parse_decimal(row[column], "commission")
        if amount == 0:
            return []
        return [
            self._txn(
                day=parse_iso_date(row["Date/Time"], "Trades"),
                currency=currency or row["Currency"],
                amount=amount,
                description=f"Commission {row['Symbol']}",
                kind=Kind.COMMISSION,
                category="Commissioni",
                ref=f"{ref}:comm",
                backdated=backdated,
            )
        ]

    def _t_bill_trade(self, row: _Row, ref: str) -> SecurityTrade:
        # Symbol is "<CUSIP> <yield>%"; quantity is face value, price is % of face.
        cusip = row["Symbol"].split(maxsplit=1)[0]
        maturity = self._maturities.get(cusip)
        if maturity is None:
            msg = f"Trades {ref}: no maturity for {cusip} in Financial Instrument Information"
            raise ExtractionError(msg)
        return self._security_trade(row, ref, cusip, maturity)

    def _security_trade(
        self, row: _Row, ref: str, symbol: str, maturity: date | None = None
    ) -> SecurityTrade:
        quantity = parse_decimal(row["Quantity"], "Trades quantity")
        price = parse_decimal(row["T. Price"], "Trades price")
        proceeds = parse_decimal(row["Proceeds"], "Trades proceeds")
        return SecurityTrade(
            date=self._in_period(parse_iso_date(row["Date/Time"], "Trades")),
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
            asset_tab="Bonds" if maturity else self._asset_tabs.get(symbol, ""),
            maturity=maturity,
        )

    def _futures_trade(self, row: _Row, ref: str) -> Transaction:
        # Futures settle daily (Cash Settling MTM), which the statement doesn't itemise. For a
        # position flat at both ends of the period the notional values net to that cash.
        symbol = row["Symbol"]
        position = self._futures.get(symbol)
        if position is None:
            msg = f"Trades {ref}: no Mark-to-Market row for futures {symbol}"
            raise ExtractionError(msg)
        if any(position):
            msg = f"Trades {ref}: futures {symbol} is open across the statement period boundary"
            raise UnsupportedStatementError(msg)
        quantity = parse_decimal(row["Quantity"], "Trades quantity")
        price = parse_decimal(row["T. Price"], "Trades price")
        return self._txn(
            day=parse_iso_date(row["Date/Time"], "Trades"),
            currency=row["Currency"],
            amount=parse_decimal(row["Notional Value"], "Trades notional value"),
            description=_describe(quantity, symbol, price),
            kind=Kind.TRADING,
            category="Trading",
            ref=ref,
        )

    def _option_trade(self, row: _Row, ref: str) -> list[Transaction]:
        proceeds = parse_decimal(row["Proceeds"], "Trades proceeds")
        if proceeds == 0:  # expiry: no cash moves
            return []
        quantity = parse_decimal(row["Quantity"], "Trades quantity")
        price = parse_decimal(row["T. Price"], "Trades price")
        return [
            self._txn(
                day=parse_iso_date(row["Date/Time"], "Trades"),
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
        day = parse_iso_date(row["Date/Time"], "Trades")
        quantity = parse_decimal(row["Quantity"], "Trades quantity")
        proceeds = parse_decimal(row["Proceeds"], "Trades proceeds")
        bought = base if quantity > 0 else quote
        category = f"Acquisto {bought}" if bought in _FX_CATEGORY_CURRENCIES else ""
        if bought == _HOME_CURRENCY:
            category = "Giroconto"
        price = parse_decimal(row["T. Price"], "FX price")
        description = f"FX {row['Symbol']} @ {format(price, 'f')}"
        return [
            self._txn(
                day=day,
                currency=currency,
                amount=amount,
                description=description,
                kind=Kind.FX,
                category=category,
                ref=f"{ref}:{leg}",
                backdated=True,  # executed after IBKR's 17:00 ET roll: next period's trade date
            )
            for leg, currency, amount in (("base", base, quantity), ("quote", quote, proceeds))
        ]


def _describe(quantity: Decimal, symbol: str, price: Decimal) -> str:
    side = "Buy" if quantity > 0 else "Sell"
    return f"{side} {format(abs(quantity), 'f')} {symbol} @ {format(price, 'f')}"
