from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from finanze.errors import ExtractionError, UnsupportedStatementError
from finanze.extractors import ibkr
from finanze.models import (
    CashBalance,
    Extraction,
    Holding,
    Kind,
    PrintedValue,
    Rate,
    SecurityTrade,
)
from finanze.validation import check_balances
from tests.extractors.golden import assert_matches_golden

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ibkr"
CSV = (FIXTURES / "activity.csv").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def result() -> Extraction:
    return ibkr.extract(CSV, source_file="activity.csv")


def _totals(result: Extraction, kind: Kind) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for txn in result.transactions:
        if txn.kind is kind:
            totals[txn.currency] += txn.amount
    return dict(totals)


def test_matches_golden_file(result: Extraction) -> None:
    assert_matches_golden(result, "ibkr")


def test_a_statement_of_several_months_covers_them_all() -> None:
    longer = ibkr.extract(CSV.replace("September 1, 2026 -", "July 1, 2026 -"), "year.csv")
    assert longer.period == (date(2026, 7, 1), date(2026, 9, 30))
    check_balances(longer)


def test_a_period_that_starts_and_ends_inside_a_month_is_kept_exactly() -> None:
    odd = CSV.replace("September 1, 2026 - September 30, 2026", "August 20, 2026 - October 3, 2026")
    assert odd != CSV
    part = ibkr.extract(odd, "part.csv")
    assert part.period == (date(2026, 8, 20), date(2026, 10, 3))
    assert part.month == date(2026, 8, 1)
    check_balances(part)


def test_month_is_the_first_day_of_the_statement_period(result: Extraction) -> None:
    assert result.month == date(2026, 9, 1)


def test_balances_come_from_the_cash_report(result: Extraction) -> None:
    assert result.balances == (
        CashBalance("IBKR CHF", "CHF", Decimal(1200), Decimal("1696.64875")),
        CashBalance("IBKR EUR", "EUR", Decimal(500), Decimal("249.2")),
        CashBalance("IBKR USD", "USD", Decimal(12500), Decimal("10261.84")),
    )


def test_every_account_reconciles_to_the_closing_cash(result: Extraction) -> None:
    check_balances(result)


# Known totals below are the statement's own Cash Report lines, per currency.


def test_commission_totals_match_cash_report(result: Extraction) -> None:
    assert _totals(result, Kind.COMMISSION) == {
        "CHF": Decimal("-3.35125"),  # Commissions -2 + Other Fees -1.25 + Sales Tax -0.10125
        "EUR": Decimal("-6.8"),
        "USD": Decimal("-12.73"),  # Commissions -13.2 + CAT fee refund 0.47 (under Deposits)
    }


def test_dividends_include_payments_in_lieu(result: Extraction) -> None:
    # Cash Report: Dividends 10 + Payment In Lieu of Dividends 4.2
    assert _totals(result, Kind.DIVIDEND) == {"USD": Decimal("14.2")}


def test_withholding_tax_and_interest_match_cash_report(result: Extraction) -> None:
    assert _totals(result, Kind.TAX) == {"USD": Decimal("-2.13")}
    # Broker interest 12.9 + T-bill maturity interest 120.4 (inside Trades (Sales))
    assert _totals(result, Kind.INTEREST) == {"USD": Decimal("133.3")}


def test_trade_cash_flows_match_cash_report(result: Extraction) -> None:
    # Cash Report: Trades (Sales) + Trades (Purchase), all segments, + Cash Settling MTM 50
    assert _totals(result, Kind.TRADING) == {"USD": Decimal(955), "EUR": Decimal(156)}
    assert _totals(result, Kind.SECURITY_TRADE) == {"USD": Decimal("-11555.5")}
    assert _totals(result, Kind.FX) == {"CHF": Decimal(-2500), "USD": Decimal(3125)}


def test_transfers_match_cash_report(result: Extraction) -> None:
    assert _totals(result, Kind.TRANSFER) == {
        "CHF": Decimal(3000),
        "EUR": Decimal(-400),
        "USD": Decimal("25.1"),  # Deposits 0.1 + Account Transfers 25
    }


def test_income_is_t_bill_principal_and_the_referral_credit(result: Extraction) -> None:
    assert _totals(result, Kind.INCOME) == {"USD": Decimal("5079.6")}  # 4879.6 + 200


def test_security_trades_carry_asset_tab_fields(result: Extraction) -> None:
    trades = [
        t
        for t in result.transactions
        if isinstance(t, SecurityTrade) and t.kind is Kind.SECURITY_TRADE
    ]
    assert [(t.ticker, t.date, t.quantity, t.price, t.cost, t.currency) for t in trades] == [
        ("ACME", date(2026, 9, 15), Decimal(20), Decimal("112.4"), Decimal(2248), "USD"),
        ("WIDG", date(2026, 9, 23), Decimal(-10), Decimal("54.25"), Decimal("-542.5"), "USD"),
        ("912797ZZ1", date(2026, 9, 9), Decimal(10000), Decimal("98.5"), Decimal(9850), "USD"),
    ]
    assert {t.account for t in trades} == {"IBKR USD"}
    # D12: the tab comes from the instrument's type, not from IBKR's "Stocks" asset class
    assert [t.asset_tab for t in trades] == ["Stocks", "ETFs", "Bonds"]
    assert {t.category for t in trades} == {"Investimenti"}


def test_long_asset_category_labels_are_read_as_their_short_form(result: Extraction) -> None:
    # 2023-24 statements append " - Held with Interactive Brokers (U.K.) Limited carried by ..."
    trades = {t.ticker: t for t in result.transactions if isinstance(t, SecurityTrade)}
    assert trades["WIDG"].kind is Kind.SECURITY_TRADE


@pytest.mark.parametrize(
    ("code", "described"),
    [
        ("O", "Buy to Open 20 ACME @ 112.4"),
        ("O;P", "Buy to Open 20 ACME @ 112.4"),  # a partial fill still opens
        ("A;C", "Buy to Close 20 ACME @ 112.4"),
        ("C;O", "Buy to Close and Open 20 ACME @ 112.4"),  # through zero: short to long
        ("", "Buy 20 ACME @ 112.4"),  # the statement does not say
    ],
)
def test_a_trade_says_whether_it_opens_or_closes(code: str, described: str) -> None:
    printed = ",-2248,-1,2249,0,0,0,O\n"
    assert CSV.count(printed) == 1
    content = CSV.replace(printed, f",-2248,-1,2249,0,0,0,{code}\n")
    trades = {
        t.ticker: t
        for t in ibkr.extract(content, source_file="a.csv").transactions
        if isinstance(t, SecurityTrade)
    }
    assert trades["ACME"].description == described


def test_t_bill_purchase_is_a_security_trade_with_its_maturity(result: Extraction) -> None:
    bill = next(t for t in result.transactions if isinstance(t, SecurityTrade) and t.maturity)
    assert (bill.ticker, bill.maturity, bill.amount) == (
        "912797ZZ1",
        date(2026, 12, 24),
        Decimal(-9850),
    )
    assert bill.description == "Buy to Open 10000 912797ZZ1 @ 98.5"
    assert bill.category == "Investimenti"


def test_maturity_splits_into_principal_and_interest_on_the_ibkr_basis(
    result: Extraction,
) -> None:
    # D13: principal = proceeds - realized P/L (IBKR's basis, buy commission included)
    rows = [t for t in result.transactions if t.source_ref.startswith("Corporate Actions:")]
    assert [(t.date, t.amount, t.kind, t.category, t.counterparty) for t in rows] == [
        (date(2026, 9, 17), Decimal("4879.6"), Kind.INCOME, "Investimenti", "912797YY2"),
        (date(2026, 9, 17), Decimal("120.4"), Kind.INTEREST, "Interessi", "912797YY2"),
    ]


def test_t_bill_maturity_ends_the_holding(result: Extraction) -> None:
    principal = next(
        t for t in result.transactions if t.source_ref == "Corporate Actions:1:principal"
    )
    assert isinstance(principal, SecurityTrade)
    # The face repaid; the bill is not among the statement's instruments, so its maturity
    # is the one its description prints.
    assert (principal.ticker, principal.quantity, principal.asset_tab, principal.maturity) == (
        "912797YY2",
        Decimal(-5000),
        "Bonds",
        date(2026, 9, 17),
    )
    assert principal.cost == Decimal("-4879.6")


def test_t_bill_maturity_takes_the_maturity_the_statement_lists() -> None:
    listed = CSV.replace(
        "Treasury Bills,912797ZZ1,B 12/24/26,1002,US912797ZZ11,BILL,,1,2026-12-24,",
        "Treasury Bills,912797ZZ1,B 12/24/26,1002,US912797ZZ11,BILL,,1,2026-12-24,\n"
        "Financial Instrument Information,Data,Treasury Bills,912797YY2,B 09/18/26,1005,"
        "US912797YY23,BILL,,1,2026-09-18,",
    )
    principal = next(
        t
        for t in ibkr.extract(listed, "activity.csv").transactions
        if t.source_ref == "Corporate Actions:1:principal"
    )
    assert isinstance(principal, SecurityTrade)
    assert principal.maturity == date(2026, 9, 18)


def test_t_bill_maturity_without_profit_has_no_interest_row() -> None:
    flat = CSV.replace('US912797YY23)",-5000,5000,0,120.4,', 'US912797YY23)",-5000,5000,0,0,')
    rows = [
        t
        for t in ibkr.extract(flat, "activity.csv").transactions
        if t.source_ref.startswith("Corporate Actions:")
    ]
    assert [(t.amount, t.kind) for t in rows] == [(Decimal(5000), Kind.INCOME)]


def test_zero_cash_corporate_actions_are_skipped(result: Extraction) -> None:
    assert not any("Split" in t.description for t in result.transactions)


def test_shares_moved_in_change_the_quantity_held_and_no_cash(result: Extraction) -> None:
    [moved] = [t for t in result.transactions if t.source_ref.startswith("Transfers:")]
    assert isinstance(moved, SecurityTrade)
    assert (moved.kind, moved.category, moved.amount) == (Kind.TRANSFER, "Giroconto", Decimal(0))
    assert (moved.date, moved.account, moved.ticker) == (date(2026, 9, 29), "IBKR USD", "GOOG")
    # What the shares were worth that day is their cost; the price follows from it.
    assert (moved.quantity, moved.cost, moved.price) == (
        Decimal(5),
        Decimal("1021.50"),
        Decimal("204.3"),
    )
    assert str(moved.price) == "204.3"
    assert moved.asset_tab == "Stocks"


def test_shares_moved_out_carry_a_negative_quantity() -> None:
    out = CSV.replace("2026-09-29,FOP,In,--,MS-XXXXXX-00,5,", "2026-09-29,Internal,Out,--,U2,-5,")
    out = out.replace('"1,021.50",0.00,0.00', '"-1,021.50",0.00,0.00')
    [moved] = [
        t
        for t in ibkr.extract(out, "activity.csv").transactions
        if t.source_ref.startswith("Transfers:")
    ]
    assert isinstance(moved, SecurityTrade)
    assert (moved.quantity, moved.cost, moved.price) == (
        Decimal(-5),
        Decimal("-1021.50"),
        Decimal("204.3"),
    )
    assert moved.description == "Transfer out: 5 GOOG"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("USD,GOOG,2026-09-29,FOP,In,--,MS-XXXXXX-00,5,", "USD,GOOG,2026-09-29,FOP,In,--,MS,0,"),
        (
            "carried by Interactive Brokers LLC,USD,GOOG,2026-09-29",
            "carried by Interactive Brokers LLC,USD,GOOG 16OCT26 200 C,2026-09-29",
        ),
    ],
)
def test_a_transfer_of_nothing_or_of_something_else_than_shares_is_refused(
    old: str, new: str
) -> None:
    changed = CSV.replace(old, new)
    if "16OCT26" in new:
        changed = changed.replace(
            "Transfers,Data,Stocks - Held with Interactive Brokers (U.K.) Limited carried by "
            "Interactive Brokers LLC",
            "Transfers,Data,Equity and Index Options",
        )
    with pytest.raises(UnsupportedStatementError, match="unsupported transfer"):
        ibkr.extract(changed, "activity.csv")


def test_holdings_are_the_shares_and_bills_of_open_positions(result: Extraction) -> None:
    # Options are not assets (D20), and a lot repeats its summary row.
    assert result.holdings == (
        Holding("IBKR USD", "ACME", "USD", Decimal(20), Decimal("113.1"), Decimal(2262), "Stocks"),
        Holding(
            "IBKR USD", "GOOG", "USD", Decimal(5), Decimal("204.3"), Decimal("1021.5"), "Stocks"
        ),
        Holding(
            "IBKR USD", "912797ZZ1", "USD", Decimal(10000), Decimal("98.71"), Decimal(9871), "Bonds"
        ),
    )


def test_a_statement_without_open_positions_holds_nothing() -> None:
    lines = [line for line in CSV.splitlines() if not line.startswith("Open Positions,")]
    assert ibkr.extract("\n".join(lines), "activity.csv").holdings == ()


def test_printed_values_are_the_lines_of_net_asset_value(result: Extraction) -> None:
    assert result.values == (
        PrintedValue("Cash", "CHF", Decimal("5986.2")),
        PrintedValue("Stock", "CHF", Decimal("2626.8")),
        PrintedValue("Options", "CHF", Decimal(-644)),
        PrintedValue("Bonds", "CHF", Decimal("7896.8")),
        PrintedValue("Total", "CHF", Decimal("15865.8")),
    )


def test_rates_are_the_closing_prices_of_the_other_currencies(result: Extraction) -> None:
    assert result.rates == (
        Rate("EUR", "CHF", Decimal("0.95")),
        Rate("USD", "CHF", Decimal("0.8")),
    )


def test_a_futures_round_trip_books_its_result_once_on_the_closing_trade(
    result: Extraction,
) -> None:
    # Sold at a notional 34000 and bought back at 33950: the 50 are the Cash Settling MTM.
    futures = [t for t in result.transactions if "MESZ6" in t.description]
    assert [(t.date, t.amount, t.kind) for t in futures] == [
        (date(2026, 9, 21), Decimal("-0.62"), Kind.COMMISSION),
        (date(2026, 9, 22), Decimal(50), Kind.TRADING),
        (date(2026, 9, 22), Decimal("-0.62"), Kind.COMMISSION),
    ]
    assert futures[1].description == "Buy to Close 1 MESZ6 @ 6790: position closed"


def test_a_futures_position_closed_in_parts_is_booked_when_the_last_part_goes() -> None:
    opened = 'Trades,Data,Order,Futures,USD,MESZ6,"2026-09-21, 15:00:00",-1,6800,6795,34000,'
    twice = CSV.replace(opened, opened.replace(",-1,6800,6795,34000,", ",-2,6800,6795,68000,"))
    closed = 'Trades,Data,Order,Futures,USD,MESZ6,"2026-09-22, 15:30:00",1,6790,6792,-33950,'
    rest = closed.replace("2026-09-22, 15:30:00", "2026-09-24, 10:00:00").replace(
        ",1,6790,6792,-33950,", ",1,6780,6792,-33900,"
    )
    line = next(row for row in CSV.splitlines() if row.startswith(closed))
    twice = twice.replace(line, f"{line}\n{line.replace(closed, rest)}")
    futures = [
        t
        for t in ibkr.extract(twice, "activity.csv").transactions
        if "MESZ6" in t.description and t.kind is Kind.TRADING
    ]
    assert [(t.date, t.amount) for t in futures] == [(date(2026, 9, 24), Decimal(150))]


def test_a_futures_position_the_trades_leave_open_is_refused() -> None:
    closed = 'Trades,Data,Order,Futures,USD,MESZ6,"2026-09-22, 15:30:00",1,6790,'
    lines = [row for row in CSV.splitlines() if not row.startswith(closed)]
    with pytest.raises(UnsupportedStatementError, match="futures MESZ6 are never closed"):
        ibkr.extract("\n".join(lines), "activity.csv")


def test_vat_on_market_data_is_a_commission(result: Extraction) -> None:
    vat = [t for t in result.transactions if t.source_ref.startswith("GST/VAT Details:")]
    assert sum(t.amount for t in vat) == Decimal("-0.10125")
    assert {(t.account, t.kind, t.category) for t in vat} == {
        ("IBKR CHF", Kind.COMMISSION, "Commissioni")
    }


def test_referral_credit_is_income_for_review_and_fee_reversals_stay_commissions(
    result: Extraction,
) -> None:
    credit = next(t for t in result.transactions if t.description.startswith("REFERRER"))
    assert (credit.amount, credit.kind, credit.category) == (Decimal(200), Kind.INCOME, "")
    reversal = next(t for t in result.transactions if t.description.startswith("cancel["))
    assert (reversal.kind, reversal.category) == (Kind.COMMISSION, "Commissioni")


def test_commission_refund_under_deposits_is_a_commission(result: Extraction) -> None:
    refund = next(t for t in result.transactions if t.description.startswith("Commission Adj"))
    assert (refund.amount, refund.kind, refund.category) == (
        Decimal("0.47"),
        Kind.COMMISSION,
        "Commissioni",
    )


def test_expired_options_produce_no_zero_amount_rows(result: Extraction) -> None:
    # only shares moved in kind are rows without cash
    assert all(txn.amount != 0 for txn in result.transactions if txn.kind is not Kind.TRANSFER)
    expired = ("NQ 11SEP26 21000 P", "DAX 18SEP26 23000 P")
    assert not any(e in t.description for e in expired for t in result.transactions)


def test_forex_trade_books_both_legs_and_its_commission_in_chf(result: Extraction) -> None:
    fx = [t for t in result.transactions if t.kind is Kind.FX]
    assert [(t.account, t.amount, t.currency, t.category) for t in fx] == [
        ("IBKR CHF", Decimal(-2500), "CHF", "Acquisto USD"),
        ("IBKR USD", Decimal(3125), "USD", "Acquisto USD"),
    ]
    commission = next(t for t in result.transactions if t.description.endswith("CHF.USD"))
    assert (commission.account, commission.currency) == ("IBKR CHF", "CHF")


def test_deterministic_categories(result: Extraction) -> None:
    expected = {
        Kind.TRADING: "Trading",
        Kind.COMMISSION: "Commissioni",
        Kind.DIVIDEND: "Dividendi",
        Kind.TAX: "Ritenute",
        Kind.INTEREST: "Interessi",
    }
    pairs = {(t.kind, t.category) for t in result.transactions if t.kind in expected}
    assert pairs == set(expected.items())  # every row, not just one per kind


def test_transfer_categories_follow_description_and_sign(result: Extraction) -> None:
    by_amount = {t.amount: t.category for t in result.transactions if t.kind is Kind.TRANSFER}
    assert by_amount[Decimal(3000)] == "Topup IBKR"
    assert by_amount[Decimal(-400)] == "Prelievi"
    # Not deterministic: left blank for user review
    assert by_amount[Decimal(25)] == ""
    assert by_amount[Decimal("0.1")] == ""


def test_dividend_counterparty_is_the_ticker(result: Extraction) -> None:
    tickers = {t.counterparty for t in result.transactions if t.kind in {Kind.DIVIDEND, Kind.TAX}}
    assert tickers == {"ACME", "WIDG"}


def test_every_row_has_a_unique_source_ref_and_the_source_file(result: Extraction) -> None:
    refs = [t.source_ref for t in result.transactions]
    assert len(refs) == len(set(refs))
    assert {t.source_file for t in result.transactions} == {"activity.csv"}


def test_all_dates_fall_inside_the_statement_month(result: Extraction) -> None:
    assert all(date(2026, 9, 1) <= t.date <= date(2026, 9, 30) for t in result.transactions)


def test_rerunning_gives_identical_output(result: Extraction) -> None:
    assert ibkr.extract(CSV, source_file="activity.csv") == result


def test_utf8_bom_is_ignored(result: Extraction) -> None:
    assert ibkr.extract("\ufeff" + CSV, source_file="activity.csv") == result


def test_blank_lines_are_ignored(result: Extraction) -> None:
    spaced = CSV.replace("Trades,Header", "\n\nTrades,Header", 1)
    assert ibkr.extract(spaced, source_file="activity.csv") == result


# ---------------------------------------------------------------- failures must be loud


def _without(prefix: str) -> str:
    return "\n".join(line for line in CSV.splitlines() if not line.startswith(prefix)) + "\n"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (CSV.replace("September 1, 2026", "October 2, 2026"), "ends before it starts"),
        (CSV.replace("September 1, 2026 - ", "September 1, 2026 to "), "period"),
        (CSV.replace("September 1, 2026", "Septembre 1, 2026"), "period"),
        (_without("Statement,Data,Period"), "period"),
        (_without("Cash Report,"), "cash report"),
        (_without("Cash Report,Data,Ending Cash,EUR"), "Ending Cash"),
        (_without("Cash Report,Data,Starting Cash,USD"), "Starting Cash"),
        (CSV.replace("2026-09-15, 10:31:07", "2026-10-15, 10:31:07"), "outside"),
        (CSV.replace(",-2248,", ",abc,"), "amount"),
        (CSV.replace(",Order,Stocks,", ",ClosedLot,Stocks,"), "discriminator"),
        (CSV.replace("T. Price", "Trade Price"), "no column 'T. Price'"),
        (CSV.replace("Comm/Fee", "Fee"), "no commission column"),
        (CSV.replace("CHF.USD", "CHF.EUR"), "quote currency"),
        (CSV.replace(",2026-09-25,ACME(", ",25.09.2026,ACME("), "date"),
        (_without("Financial Instrument Information,Data,Treasury"), "no maturity for 912797ZZ1"),
        (CSV.replace(",1,2026-12-24,", ",1,24.12.2026,"), "date"),
        (_without("Mark-to-Market Performance Summary,Data,Futures"), "no Mark-to-Market row"),
    ],
)
def test_malformed_statements_raise(content: str, message: str) -> None:
    with pytest.raises(ExtractionError, match=f"(?i){message}"):
        ibkr.extract(content, source_file="activity.csv")


def test_data_row_without_header_raises() -> None:
    with pytest.raises(ExtractionError, match="header"):
        ibkr.extract("Trades,Data,Order,Stocks\n", source_file="x.csv")


@pytest.mark.parametrize("category", ["Bonds", "Warrants", "CFDs"])
def test_unsupported_asset_categories_fail_instead_of_guessing(category: str) -> None:
    content = CSV.replace(",Order,Stocks,", f",Order,{category},")
    with pytest.raises(UnsupportedStatementError, match=category):
        ibkr.extract(content, source_file="activity.csv")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        # Daily MTM cash isn't itemised, so a position held across month end can't be booked.
        (CSV.replace("Futures,MESZ6,0,0,", "Futures,MESZ6,1,0,"), "open across"),
        (CSV.replace("Futures,MESZ6,0,0,", "Futures,MESZ6,0,1,"), "open across"),
        (
            CSV.replace(
                "Treasury Bills - Held with Interactive Brokers (U.K.) Limited carried by "
                "Interactive Brokers LLC,USD,2026-09-17",
                "Bonds,USD,2026-09-17",
            ),
            "unsupported corporate action",
        ),
        (CSV.replace("Treasury Bill Maturity", "Tender Offer"), "unsupported corporate action"),
        (CSV.replace(',--,"1,021.50",0.00,0.00,', ',--,"1,021.50",0.00,-12.00,'), "cash"),
    ],
)
def test_unsupported_cash_movements_fail_instead_of_guessing(content: str, message: str) -> None:
    assert content != CSV
    with pytest.raises(UnsupportedStatementError, match=f"(?i){message}"):
        ibkr.extract(content, source_file="activity.csv")


def test_sales_tax_comes_from_the_cash_report_without_a_vat_section(result: Extraction) -> None:
    content = _without("GST/VAT Details,")
    fallback = ibkr.extract(content, source_file="activity.csv")
    check_balances(fallback)
    tax = [t for t in fallback.transactions if t.source_ref.startswith("Cash Report:")]
    assert [(t.date, t.account, t.amount, t.kind, t.category) for t in tax] == [
        (date(2026, 9, 30), "IBKR CHF", Decimal("-0.10125"), Kind.COMMISSION, "Commissioni")
    ]
    assert not any(t.source_ref.startswith("GST/VAT") for t in fallback.transactions)
    assert not any(t.source_ref.startswith("Cash Report:") for t in result.transactions)


@pytest.mark.parametrize(
    ("old", "new", "ref", "original"),
    [
        # FX executed after IBKR's 17:00 ET roll belongs to the next trade date
        ('"2026-09-12, 09:42:07"', '"2026-08-31, 20:41:16"', "Trades:6:base", "2026-08-31"),
        # Withholding tax corrections keep the original payment date
        (
            "USD,2026-09-26,WIDG(US0000000002) Payment in Lieu of Dividend - US Tax",
            "USD,2026-08-14,WIDG(US0000000002) Payment in Lieu of Dividend - US Tax",
            "Withholding Tax:2",
            "2026-08-14",
        ),
    ],
)
def test_backdated_fx_and_withholding_tax_are_booked_on_the_first_day(
    old: str, new: str, ref: str, original: str
) -> None:
    content = CSV.replace(old, new)
    assert content != CSV
    shifted = ibkr.extract(content, source_file="activity.csv")
    check_balances(shifted)
    row = next(t for t in shifted.transactions if t.source_ref == ref)
    assert row.booking_date == date(2026, 9, 1)
    assert row.date == date.fromisoformat(original)  # D19: the day IBKR prints
    assert row.description.endswith(f"(dated {original})")


def test_backdated_rows_in_other_sections_still_raise() -> None:
    content = CSV.replace("2026-09-15, 10:31:07", "2026-08-15, 10:31:07")
    with pytest.raises(ExtractionError, match="outside"):
        ibkr.extract(content, source_file="activity.csv")


def test_stock_of_an_unlisted_type_gets_no_asset_tab() -> None:
    content = CSV.replace("WIDG,ARCA,1,ETF,", "WIDG,ARCA,1,WARRANT,")
    assert content != CSV
    trades = [
        t
        for t in ibkr.extract(content, source_file="activity.csv").transactions
        if isinstance(t, SecurityTrade)
    ]
    assert [(t.ticker, t.asset_tab) for t in trades[:2]] == [("ACME", "Stocks"), ("WIDG", "")]


def test_converting_back_into_francs_is_a_move_between_own_accounts() -> None:
    content = CSV.replace(
        'CHF.USD,"2026-09-12, 09:42:07","-2,500"', 'CHF.USD,"2026-09-12, 09:42:07","2,500"'
    )
    assert content != CSV
    fx = [t for t in ibkr.extract(content, "a.csv").transactions if t.kind is Kind.FX]
    assert {t.category for t in fx} == {"Giroconto"}


GRANTS = (
    "Grant Activity,Header,Symbol,Report Date,Description,Award Date,Vesting Date,Quantity,"
    "Price,Value\n"
    "Grant Activity,Data,ACME,2026-09-03,Stock Award Grant for Cash Deposit,2026-09-03,"
    "2027-09-03,0.5,100,50\n"
    "Grant Activity,Data,ACME,2026-09-04,Stock Award Return for Cash Withdrawal,2026-09-03,"
    "2027-09-03,-0.1,101,-10.1\n"
    "Grant Activity,Data,ACME,2026-09-10,Stock Award Vesting,2025-09-09,2026-09-09,1.25,"
    "110.5,138.125\n"
    "Grant Activity,Data,Total,,,,,1.65,,178.025\n"
    "Grant Activity,Data,Total in CHF,,,,,1.65,,142.42\n"
)


def test_awarded_shares_come_in_when_they_vest_at_what_they_were_worth(result: Extraction) -> None:
    with_grants = ibkr.extract(CSV + GRANTS, source_file="activity.csv")
    check_balances(with_grants)  # no cash moves
    extra = [t for t in with_grants.transactions if t not in result.transactions]
    assert len(extra) == 1
    vest = extra[0]
    assert isinstance(vest, SecurityTrade)
    assert (vest.ticker, vest.quantity, vest.price, vest.cost) == (
        "ACME",
        Decimal("1.25"),
        Decimal("110.5"),
        Decimal("138.125"),
    )
    assert (vest.date, vest.account, vest.amount, vest.asset_tab) == (
        date(2026, 9, 10),
        "IBKR USD",
        Decimal(0),
        "Stocks",
    )
    assert vest.description == "Stock Award Vesting: 1.25 ACME @ 110.5"
    assert (vest.kind, vest.category, vest.source_ref) == (
        Kind.SECURITY_TRADE,
        "Investimenti",
        "Grant Activity:3",
    )


def test_an_unknown_grant_activity_is_refused() -> None:
    content = CSV + GRANTS.replace("Stock Award Vesting", "Stock Award Forfeiture")
    with pytest.raises(UnsupportedStatementError, match="Stock Award Forfeiture"):
        ibkr.extract(content, source_file="activity.csv")
