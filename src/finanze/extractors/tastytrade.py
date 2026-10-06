"""Tastytrade Transactions CSV -> normalized transactions (D1).

One row per fill or cash movement, newest first, all in USD. The export prints no balances, so
the control total is the sum of the `Total` column. `Value` is rounded independently of `Total`
on share trades (off by a cent), so the trade amount is `Total` minus commissions and fees.
"""

from __future__ import annotations

from decimal import Decimal

from finanze.errors import ExtractionError, UnsupportedStatementError
from finanze.extractors.common import parse_decimal, parse_iso_date, read_table, single_month
from finanze.models import ControlTotal, Extraction, Kind, SecurityTrade, Transaction

ACCOUNT = "Tastytrade"
CURRENCY = "USD"

_COLUMNS = (
    "Date",
    "Type",
    "Sub Type",
    "Action",
    "Symbol",
    "Instrument Type",
    "Description",
    "Value",
    "Quantity",
    "Average Price",
    "Commissions",
    "Fees",
    "Underlying Symbol",
    "Total",
    "Currency",
)
_ROUNDING = Decimal("0.01")
# Shares, ETFs and T-bills are investments (D11), not trading P/L, and go to the asset tabs.
_T_BILL = "Fixed Income Security"
_SECURITIES = {"Equity", _T_BILL}
_MONEY_MOVEMENTS = {
    "Mark to Market": (Kind.TRADING, "Trading"),
    "Credit Interest": (Kind.INTEREST, "Interessi"),
    "Debit Interest": (Kind.INTEREST, "Interessi"),
    "Dividend": (Kind.DIVIDEND, "Dividendi"),
    "Balance Adjustment": (Kind.COMMISSION, "Commissioni"),  # regulatory fee adjustments
    "Deposit": (Kind.TRANSFER, ""),  # wires in: the category is left for review
    "Withdrawal": (Kind.TRANSFER, ""),
}


def extract(content: str, source_file: str) -> Extraction:
    """Parse the text of a Tastytrade Transactions CSV covering one calendar month."""
    rows = read_table(content, ",", _COLUMNS, "Tastytrade")
    transactions: list[Transaction] = []
    total = Decimal(0)
    for n, row in enumerate(rows, start=1):
        if row["Currency"] != CURRENCY:
            msg = f"Tastytrade row {n}: unsupported currency {row['Currency']!r}"
            raise UnsupportedStatementError(msg)
        cash = _amount(row["Total"], "Total")
        charges = _amount(row["Commissions"], "Commissions") + _amount(row["Fees"], "Fees")
        gross = cash - charges
        if abs(gross - _amount(row["Value"], "Value")) > _ROUNDING:
            msg = f"Tastytrade row {n}: Value + Commissions + Fees does not match Total {cash}"
            raise ExtractionError(msg)
        total += cash
        kind, category = _classify(row, n)
        day = parse_iso_date(row["Date"], "Tastytrade Date")
        for ref, amount, leg_kind, leg_category in (
            (str(n), gross, kind, category),
            (f"{n}:fees", charges, Kind.COMMISSION, "Commissioni"),
        ):
            if amount != 0:  # expirations and futures opens move no cash
                txn = Transaction(
                    date=day,
                    account=ACCOUNT,
                    amount=amount,
                    currency=CURRENCY,
                    description=row["Description"],
                    kind=leg_kind,
                    source_file=source_file,
                    source_ref=ref,
                    counterparty=row["Underlying Symbol"] or row["Symbol"],
                    category=leg_category,
                )
                is_trade = leg_kind is Kind.SECURITY_TRADE
                transactions.append(_security_trade(txn, row, n) if is_trade else txn)
    month = single_month(
        (parse_iso_date(row["Date"], "Tastytrade Date") for row in rows), "Tastytrade export"
    )
    return Extraction(
        month=month,
        transactions=tuple(transactions),
        controls=(ControlTotal(ACCOUNT, CURRENCY, total),),
    )


def _amount(text: str, what: str) -> Decimal:
    return Decimal(0) if text == "--" else parse_decimal(text, f"Tastytrade {what}")


def _security_trade(txn: Transaction, row: dict[str, str], n: int) -> SecurityTrade:
    """A share, ETF or T-bill trade, in the asset tabs' terms (D11).

    T-bills are quoted per 1000 of face value ("Bought 10 ... @ 985.10"): quantity becomes the
    face value and price the % of face, as on IBKR. The export has neither the instrument type
    nor a bill's maturity, so the tab (and maturity) must come from `instruments.yaml`.
    """
    action = row["Action"]
    if not action.startswith(("BUY", "SELL")):
        msg = f"Tastytrade row {n}: cannot tell buy from sell in action {action!r}"
        raise ExtractionError(msg)
    quantity = parse_decimal(row["Quantity"], "Tastytrade Quantity")
    price = abs(parse_decimal(row["Average Price"], "Tastytrade Average Price"))
    bill = row["Instrument Type"] == _T_BILL
    if bill:
        quantity, price = quantity * 1000, price / 10
    return SecurityTrade(
        date=txn.date,
        account=txn.account,
        amount=txn.amount,
        currency=txn.currency,
        description=txn.description,
        kind=txn.kind,
        source_file=txn.source_file,
        source_ref=txn.source_ref,
        counterparty=txn.counterparty,
        category=txn.category,
        booking_date=txn.booking_date,
        ticker=row["Symbol"],
        quantity=quantity if action.startswith("BUY") else -quantity,
        price=price,
        cost=-txn.amount,
        asset_tab="Bonds" if bill else "",
    )


def _classify(row: dict[str, str], n: int) -> tuple[Kind, str]:
    if row["Type"] in {"Trade", "Receive Deliver"}:
        if row["Instrument Type"] in _SECURITIES:
            if row["Sub Type"] == "Maturity":  # principal back; the export has no cost to split
                return Kind.INCOME, "Investimenti"
            return Kind.SECURITY_TRADE, "Investimenti"
        return Kind.TRADING, "Trading"
    if row["Type"] == "Money Movement" and row["Sub Type"] in _MONEY_MOVEMENTS:
        if row["Sub Type"] == "Deposit" and row["Instrument Type"] == "Equity":
            return Kind.DIVIDEND, "Dividendi"  # small fund distributions arrive as "Deposit"
        return _MONEY_MOVEMENTS[row["Sub Type"]]
    msg = f"Tastytrade row {n}: unsupported row type {row['Type']!r} / {row['Sub Type']!r}"
    raise UnsupportedStatementError(msg)
