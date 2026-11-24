from collections import defaultdict
from datetime import date
from decimal import Decimal

import pytest

from scrooge.errors import ExtractionError, UnsupportedStatementError
from scrooge.extractors import tastytrade
from scrooge.models import ControlTotal, Extraction, Kind, SecurityTrade
from scrooge.validation import check_balances
from tests.extractors.golden import FIXTURES, assert_matches_golden

CSV = (FIXTURES / "tastytrade" / "transactions.csv").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def result() -> Extraction:
    return tastytrade.extract(CSV, source_file="transactions.csv")


def _by_category(result: Extraction) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for txn in result.transactions:
        totals[txn.category] += txn.amount
    return dict(totals)


def test_matches_golden_file(result: Extraction) -> None:
    assert_matches_golden(result, "tastytrade")


def test_month_and_account(result: Extraction) -> None:
    assert result.month == date(2026, 9, 1)
    assert {(t.account, t.currency) for t in result.transactions} == {("Tastytrade", "USD")}


def test_control_total_is_the_sum_of_the_total_column(result: Extraction) -> None:
    assert result.controls == (ControlTotal("Tastytrade", "USD", Decimal("975.61")),)
    check_balances(result)


def test_category_totals_follow_d1(result: Extraction) -> None:
    assert _by_category(result) == {
        # option premiums 234.38 - 125 + 150, futures mark-to-market -2000 and close 1550
        "Trading": Decimal("-190.62"),
        # commissions + fees of every row, + 0.01 regulatory fee adjustment
        "Fees": Decimal("-11.26"),
        # T-bill buy -9851 and maturity 10000, ETF buy -2010.88
        "Investments": Decimal("-1861.88"),
        "Interest": Decimal("0.04"),
        "Dividends": Decimal("39.33"),
        "": Decimal("3000.00"),  # wire deposit, left for review
    }


def test_share_trade_amount_comes_from_total_not_the_rounded_value(result: Extraction) -> None:
    # Value -2,010.89 and Fees -0.02, but Total -2,010.90: the cash is what counts.
    buy = next(t for t in result.transactions if t.source_ref == "18")
    assert (buy.amount, buy.kind) == (Decimal("-2010.88"), Kind.SECURITY_TRADE)


def test_a_trade_says_whether_it_opens_or_closes(result: Extraction) -> None:
    described = {t.source_ref: t.description for t in result.transactions}
    assert described["1"] == "Sell to Open 1 /ZBZ6 OZBX6 10/23/26 Call 109'00 @ 0.234375"
    assert described["1:fees"] == described["1"]
    assert described["2"] == "Buy to Close 1 /ESZ6 E3AV6 10/02/26 Put 6000.00 @ 2.5"
    assert described["18"] == "Buy to Open 23 WIDG @ 87.43"
    # a futures fill is a plain SELL: the export does not say which, and neither do we
    assert described["8"] == "Sold 1 /GCZ6 @ 4195.5"
    # rows that are not fills keep the export's words
    assert described["4:fees"] == "Removal of option due to expiration"


def test_a_sale_that_closes_says_so() -> None:
    content = CSV.replace("Sell to Open,SELL_TO_OPEN,ACME", "Sell to Close,SELL_TO_CLOSE,ACME")
    assert content != CSV
    described = {t.description for t in tastytrade.extract(content, "t.csv").transactions}
    assert "Sell to Close 1 ACME 10/16/26 Put 100.00 @ 1.5" in described


def test_shares_and_bills_are_security_trades_for_the_asset_tabs(result: Extraction) -> None:
    trades = [
        t
        for t in result.transactions
        if isinstance(t, SecurityTrade) and t.kind is Kind.SECURITY_TRADE
    ]
    assert [(t.ticker, t.quantity, t.price, t.cost, t.asset_tab) for t in trades] == [
        # "Bought 10 912797ZZ1 @ 985.10": face 10,000 at 98.51% of face, as IBKR reports bills
        ("912797ZZ1", Decimal(10000), Decimal("98.51"), Decimal("9851.00"), "Bonds"),
        # the export does not say WIDG is an ETF: instruments.yaml decides
        ("WIDG", Decimal(23), Decimal("87.43"), Decimal("2010.88"), ""),
    ]
    assert {t.category for t in trades} == {"Investments"}
    assert {t.maturity for t in trades} == {None}  # not in the export either


def test_sales_have_negative_quantities() -> None:
    content = CSV.replace(
        'Buy to Open,BUY_TO_OPEN,WIDG,Equity,Bought 23 WIDG @ 87.43,"-2,010.89",23,-87.43,'
        '0.00,-0.02,,,,,,,900000006,"-2,010.90"',
        'Sell to Close,SELL_TO_CLOSE,WIDG,Equity,Sold 23 WIDG @ 87.43,"2,010.89",23,87.43,'
        '0.00,-0.02,,,,,,,900000006,"2,010.87"',
    )
    assert content != CSV
    sale = tastytrade.extract(content, source_file="t.csv").transactions[-2]
    assert isinstance(sale, SecurityTrade)
    assert (sale.quantity, sale.price, sale.amount, sale.cost) == (
        Decimal(-23),
        Decimal("87.43"),
        Decimal("2010.89"),
        Decimal("-2010.89"),
    )


def test_bill_maturity_is_investment_income_that_ends_the_holding(result: Extraction) -> None:
    row = next(t for t in result.transactions if t.source_ref == "11")
    assert (row.amount, row.kind, row.category) == (Decimal(10000), Kind.INCOME, "Investments")
    # "Maturity 10.0 912797YY2 @ 1000": the whole face value leaves at 100%, on that day
    assert isinstance(row, SecurityTrade)
    assert (row.ticker, row.quantity, row.price, row.asset_tab, row.maturity) == (
        "912797YY2",
        Decimal(-10000),
        Decimal(100),
        "Bonds",
        date(2026, 9, 16),
    )


def test_rows_without_cash_produce_no_transaction(result: Extraction) -> None:
    assert all(t.amount != 0 for t in result.transactions)
    assert "3" not in {t.source_ref for t in result.transactions}  # worthless expiration
    assert {"4:fees", "5:fees", "6:fees"} <= {t.source_ref for t in result.transactions}


_SPLIT = (
    "2026-09-03T23:00:00+0200,Receive Deliver,{sub},{action},WIDG,Equity,{text},0.00,{qty},"
    "0.00,--,0.00,,,,,,,,0.00,USD\n"
)


@pytest.mark.parametrize("sub", ["Forward Split", "Reverse Split"])
def test_a_split_moves_no_cash_and_is_left_out(sub: str) -> None:
    row = _SPLIT.format(sub=sub, action="BUY_TO_OPEN", text="Split of WIDG", qty=23)
    result = tastytrade.extract(CSV + row, source_file="transactions.csv")
    assert len(result.transactions) == len(tastytrade.extract(CSV, "t.csv").transactions)
    check_balances(result)


@pytest.mark.parametrize(
    ("sub", "action", "qty"),
    [("ACAT", "BUY_TO_OPEN", 50), ("ACAT", "SELL_TO_CLOSE", 23), ("Symbol Change", "", 23)],
)
def test_shares_moved_in_kind_are_refused_not_dropped(sub: str, action: str, qty: int) -> None:
    row = _SPLIT.format(sub=sub, action=action, text="Transfer of WIDG", qty=qty)
    with pytest.raises(UnsupportedStatementError, match=rf"row 19: WIDG moved without cash.*{sub}"):
        tastytrade.extract(CSV + row, source_file="transactions.csv")


def test_fund_distribution_booked_as_deposit_is_a_dividend(result: Extraction) -> None:
    row = next(t for t in result.transactions if t.source_ref == "16")
    assert (row.kind, row.category, row.counterparty) == (Kind.DIVIDEND, "Dividends", "WIDG")


def test_dates_are_local_trade_dates_and_refs_unique(result: Extraction) -> None:
    assert result.transactions[0].date == date(2026, 9, 30)
    refs = [t.source_ref for t in result.transactions]
    assert len(refs) == len(set(refs))


def test_rerunning_gives_identical_output(result: Extraction) -> None:
    assert tastytrade.extract("\ufeff" + CSV, source_file="transactions.csv") == result


def test_a_partial_month_covers_exactly_the_days_of_its_rows() -> None:
    lines = CSV.splitlines()
    part = tastytrade.extract("\n".join([lines[0], *lines[2:5]]) + "\n", source_file="p.csv")
    assert part.period == (date(2026, 9, 25), date(2026, 9, 28))
    assert part.month == date(2026, 9, 1)
    check_balances(part)


def test_an_export_of_several_months_covers_them_all(result: Extraction) -> None:
    assert result.period == (date(2026, 9, 4), date(2026, 9, 30))
    longer = tastytrade.extract(
        CSV.replace("2026-09-04T21", "2026-07-31T21"), source_file="year.csv"
    )
    assert longer.period == (date(2026, 7, 31), date(2026, 9, 30))
    assert longer.controls == result.controls
    check_balances(longer)


@pytest.mark.parametrize(
    ("content", "error", "message"),
    [
        (CSV.splitlines()[0] + "\n", ExtractionError, "no rows"),
        (CSV.replace(",Total,", ",Net,"), ExtractionError, "missing column"),
        (CSV.replace(",231.97,USD", ",232.97,USD"), ExtractionError, "does not match Total"),
        (CSV.replace(",0.22,USD", ",abc,USD"), ExtractionError, "amount"),
        (CSV.replace("2026-09-30T20:42", "30.09.2026 20:42"), ExtractionError, "date"),
        (CSV.replace(",231.97,USD", ",231.97,EUR"), UnsupportedStatementError, "currency"),
        (CSV.replace("Balance Adjustment", "ACAT"), UnsupportedStatementError, "ACAT"),
        (CSV.replace("BUY_TO_OPEN,WIDG", "JOURNAL,WIDG"), ExtractionError, "buy from sell"),
        (CSV.replace("Trade,Sell,SELL", "Journal,Sell,SELL"), UnsupportedStatementError, "Journal"),
    ],
)
def test_malformed_exports_raise(content: str, error: type[Exception], message: str) -> None:
    assert content != CSV
    with pytest.raises(error, match=message):
        tastytrade.extract(content, source_file="transactions.csv")
