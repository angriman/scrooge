from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from scrooge.storage.bills import Event, fifo_costs


def test_the_oldest_lots_are_closed_first() -> None:
    events = [
        Event("buy-1", Decimal(5000), 4_950_000_000),
        Event("buy-2", Decimal(5000), 4_960_000_000),
        Event("sale", Decimal(-3000)),
        Event("maturity", Decimal(-7000)),
    ]
    assert fifo_costs(events) == {"sale": 2_970_000_000, "maturity": 6_940_000_000}


def test_a_disposal_without_enough_lots_has_no_cost() -> None:
    events = [Event("buy", Decimal(5000), 4_950_000_000), Event("maturity", Decimal(-10000))]
    assert fifo_costs(events) == {"maturity": None}
    assert fifo_costs([Event("sale", Decimal(-1))]) == {"sale": None}


def test_a_lot_closed_in_parts_never_gives_up_more_or_less_than_it_cost() -> None:
    events = [Event("buy", Decimal(3), 1_000_000)] + [Event(f"s{n}", Decimal(-1)) for n in range(3)]
    costs = fifo_costs(events)
    assert costs == {"s0": 333_333, "s1": 333_334, "s2": 333_333}  # half-even, then the rest


LOTS = st.lists(
    st.tuples(st.integers(1, 50), st.integers(1, 10_000_000_000)), min_size=1, max_size=6
)


@given(LOTS, st.data())
def test_costs_handed_out_plus_what_is_left_equal_what_was_paid(
    lots: list[tuple[int, int]], data: st.DataObject
) -> None:
    held = sum(quantity for quantity, _ in lots)
    sales: list[int] = []
    while held and len(sales) < 8:
        sales.append(data.draw(st.integers(1, held)))
        held -= sales[-1]
    events = [Event(f"buy{n}", Decimal(q), cost) for n, (q, cost) in enumerate(lots)]
    events += [Event(f"sale{n}", Decimal(-q)) for n, q in enumerate(sales)]
    events.append(Event("rest", Decimal(-held)))
    costs = fifo_costs(events)
    assert all(cost is not None and cost >= 0 for cost in costs.values())
    assert sum(cost or 0 for cost in costs.values()) == sum(cost for _, cost in lots)
