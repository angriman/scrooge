from datetime import date
from decimal import Decimal

import pytest

from scrooge import categories
from scrooge.cashflow import Grouping
from scrooge.models import Kind
from scrooge.query import Filter
from scrooge.web import forms

NOT_A_DAY = "is not a day, a month or a year like 2025-05-17 or 2025-05."


def test_nothing_asked_is_the_empty_filter() -> None:
    assert forms.transactions_filter({}) == forms.Parsed(Filter(), ())
    blank = dict.fromkeys(["from", "to", "owner", "account", "category", "kind", "min"], " ")
    assert forms.transactions_filter(blank) == forms.Parsed(Filter(), ())


def test_every_field_is_read() -> None:
    params = {
        "from": "2025-05",
        "to": "2025-05",
        "booking": "1",
        "owner": "Lukas",
        "account": "Migros",
        "category": categories.EATING_OUT,
        "kind": "expense",
        "min": "10",
        "max": "1'250.50",
        "text": " konsum ",
        "merchant": " Trattoria Ölberg ",
    }
    assert forms.transactions_filter(params) == forms.Parsed(
        Filter(
            first=date(2025, 5, 1),
            last=date(2025, 5, 31),
            booking=True,
            owner="Lukas",
            account="Migros",
            category=categories.EATING_OUT,
            kind=Kind.EXPENSE,
            at_least=Decimal(10),
            at_most=Decimal("1250.50"),
            text="konsum",
            merchant="Trattoria Ölberg",
        ),
        (),
    )


def test_several_kinds_are_read_apart_by_commas() -> None:
    parsed = forms.transactions_filter({"kind": "dividend, tax,interest"})
    assert parsed == forms.Parsed(Filter(kinds=(Kind.DIVIDEND, Kind.TAX, Kind.INTEREST)), ())
    refused = forms.transactions_filter({"kind": "dividend,gift"})
    assert refused.wanted == Filter(kind=Kind.DIVIDEND)
    assert refused.errors == ("'gift' is not a kind of transaction.",)


def test_a_kind_of_account_is_read_by_its_name() -> None:
    parsed = forms.transactions_filter({"accounts": "broker"})
    assert parsed == forms.Parsed(Filter(account_kinds=("broker_cash", "broker_securities")), ())
    refused = forms.transactions_filter({"accounts": "savings"})
    assert refused.wanted == Filter()
    assert refused.errors == ("'savings' is not broker, bank or card accounts.",)


def test_a_year_or_a_day_works_as_either_end_and_a_size_has_no_sign() -> None:
    parsed = forms.transactions_filter({"from": "2025", "to": "2026-02-03", "min": "-1,000"})
    assert parsed.wanted == Filter(date(2025, 1, 1), date(2026, 2, 3), at_least=Decimal(1000))


@pytest.mark.parametrize(
    ("params", "error"),
    [
        ({"from": "May"}, f"'May' {NOT_A_DAY}"),
        ({"to": "2025-13"}, f"'2025-13' {NOT_A_DAY}"),
        ({"kind": "gift"}, "'gift' is not a kind of transaction."),
        ({"min": "a lot"}, "'a lot' is not an amount like 1234.56."),
        ({"max": "NaN"}, "'NaN' is not an amount like 1234.56."),
        ({"max": "Infinity"}, "'Infinity' is not an amount like 1234.56."),
        ({"min": "1e30"}, "'1e30' is not an amount like 1234.56."),
        ({"max": "1E+400000"}, "'1E+400000' is not an amount like 1234.56."),
        ({"min": "-9223372036.9"}, "'-9223372036.9' is not an amount like 1234.56."),
    ],
)
def test_a_field_that_cannot_be_read_is_reported_and_left_unset(
    params: dict[str, str], error: str
) -> None:
    assert forms.transactions_filter(params) == forms.Parsed(Filter(), (error,))


def test_the_largest_amount_the_database_holds_is_still_an_amount() -> None:
    parsed = forms.transactions_filter({"min": "9223372036.854775807"})
    assert parsed == forms.Parsed(Filter(at_least=Decimal("9223372036.854775807")), ())


def test_a_cash_flow_range_is_by_month_unless_asked() -> None:
    everything = forms.CashFlowRange(None, None, None, Grouping.MONTH, ())
    assert forms.cashflow_range({}) == everything
    assert forms.cashflow_range({"by": " ", "from": ""}) == everything
    asked = {"from": "2025", "to": "2026-03", "owner": "Lukas", "by": "quarter"}
    assert forms.cashflow_range(asked) == forms.CashFlowRange(
        date(2025, 1, 1), date(2026, 3, 31), "Lukas", Grouping.QUARTER, ()
    )


def test_a_cash_flow_range_that_cannot_be_read_is_reported() -> None:
    parsed = forms.cashflow_range({"from": "May", "by": "decade"})
    assert parsed == forms.CashFlowRange(
        None,
        None,
        None,
        Grouping.MONTH,
        ("'decade' is not week, month, quarter or year.", f"'May' {NOT_A_DAY}"),
    )


def test_holdings_are_shown_in_euros_unless_asked() -> None:
    assert forms.holdings_view({}) == forms.HoldingsView(None, "EUR", ())
    asked = {"owner": " Lukas ", "currency": "chf", "by": "asset"}
    assert forms.holdings_view(asked) == forms.HoldingsView("Lukas", "CHF", ())


def test_a_currency_holdings_cannot_be_shown_in_is_reported() -> None:
    assert forms.holdings_view({"currency": "xau"}) == forms.HoldingsView(
        None, "EUR", ("'XAU' is not one of EUR, CHF, USD, GBP, JPY.",)
    )


def test_net_worth_is_in_euros_and_by_asset_type_unless_asked() -> None:
    assert forms.networth_view({}) == forms.NetWorthView(None, "EUR", "asset", ())
    asked = {"owner": " Lukas ", "currency": "chf", "by": "total"}
    assert forms.networth_view(asked) == forms.NetWorthView("Lukas", "CHF", "total", ())
    assert forms.networth_view({"by": " "}).by == "asset"


def test_a_breakdown_that_cannot_be_read_is_reported_and_asset_type_shown() -> None:
    assert forms.networth_view({"by": "shape"}) == forms.NetWorthView(
        None, "EUR", "asset", ("'shape' is not total, asset, owner or liquidity.",)
    )


def test_a_context_is_of_exactly_one_category_merchant_or_account() -> None:
    asked = forms.context_view({"merchant": " Trattoria ", "owner": "Anna", "text": "x"})
    assert asked == forms.ContextView("Anna", "EUR", "merchant", "Trattoria", ())
    assert forms.context_view({"account": "Migros", "currency": "chf"}).currency == "CHF"
    for params in ({}, {"category": " "}, {"category": categories.GROUP_HOME, "account": "Migros"}):
        refused = forms.context_view(params)
        assert (refused.kind, refused.name) == ("", "")
        assert refused.errors == ("Ask for exactly one of category, merchant and account.",)


def test_performance_is_over_the_year_to_date_unless_another_range_is_asked() -> None:
    assert forms.performance_view({}) == forms.PerformanceView(None, "EUR", "ytd", ())
    asked = forms.performance_view({"owner": "Anna", "currency": "usd", "range": "3Y"})
    assert asked == forms.PerformanceView("Anna", "USD", "3y", ())
    wrong = forms.performance_view({"range": "5y"})
    assert (wrong.range, wrong.errors) == ("ytd", ("'5y' is not one of 1m, 3m, ytd, 1y, 3y, all.",))
