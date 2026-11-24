"""The taxonomy's order, which lists of categories follow: never the names, which change with
the language."""

import sqlite3

from scrooge import categories
from scrooge.categories import Extra
from scrooge.storage import store, taxonomy

_EXPENSES = [leaf for leaves in categories.EXPENSE_GROUPS.values() for leaf in leaves]


def _tops(ranks: store.Ranks, side: str) -> list[str]:
    return sorted((n for s, n in ranks.tops if s == side), key=lambda n: ranks.top(n, side))


def _leaves(ranks: store.Ranks, side: str) -> list[str]:
    return sorted((n for s, n in ranks.leaves if s == side), key=lambda n: ranks.leaf(n, side))


def test_the_taxonomy_ranks_as_categories_py_lists_it(conn: sqlite3.Connection) -> None:
    store.seed_categories(conn)
    ranks = store.category_ranks(conn)
    assert _tops(ranks, "expenses") == list(categories.EXPENSE_GROUPS)
    assert _leaves(ranks, "expenses") == list(dict.fromkeys(_EXPENSES))
    assert _leaves(ranks, "income") == list(categories.INCOME_LEAVES)
    assert _leaves(ranks, "transfer") == list(categories.TRANSFERS)
    # Not by name.
    assert ranks.top(categories.GROUP_FOOD, "expenses") < ranks.top(
        categories.GROUP_EATING_OUT, "expenses"
    )
    assert ranks.top(categories.UNSORTED, "expenses") < ranks.top(categories.SALARY, "income")


def test_a_name_on_both_sides_is_ranked_on_the_side_asked_first(conn: sqlite3.Connection) -> None:
    store.seed_categories(conn)
    ranks = store.category_ranks(conn)
    assert ranks.leaf(categories.TRADING, "expenses") < ranks.leaf(
        categories.INVESTMENTS, "expenses"
    )
    assert ranks.leaf(categories.TRADING, "income") < ranks.leaf(categories.INVESTMENTS, "income")
    assert ranks.leaf(categories.TRADING, "income") > ranks.leaf(categories.SALARY, "income")
    # Expenses first.
    assert ranks.leaf(categories.TRADING) == ranks.leaf(categories.TRADING, "expenses")
    # No group of that name.
    assert ranks.top(categories.TRADING) == ranks.top(categories.TRADING, "income")


def test_a_name_the_taxonomy_lacks_comes_after_every_category(conn: sqlite3.Connection) -> None:
    store.seed_categories(conn)
    ranks = store.category_ranks(conn)
    assert ranks.top("Transfers") == len(ranks.tops)
    assert ranks.leaf(categories.SALARY, "expenses") == len(ranks.leaves)
    assert ranks.leaf("", "") == len(ranks.leaves)


def test_what_categories_yaml_adds_comes_after_the_taxonomy_it_joins(
    conn: sqlite3.Connection,
) -> None:
    store.seed_categories(conn)  # a database of before the additions
    store.seed_categories(conn, (Extra("Loans", "expenses", categories.GROUP_HOME),))
    store.seed_categories(
        conn,
        (
            Extra("Loans", "expenses", categories.GROUP_HOME),
            Extra("Vet", "expenses", categories.GROUP_HEALTH),
            Extra("Pets food", "expenses", "Pets"),
            Extra("Rental income", "income"),
        ),
    )
    ranks = store.category_ranks(conn)
    leaves = _leaves(ranks, "expenses")
    assert leaves[:8] == [
        categories.RENT,
        categories.UTILITIES,
        categories.HOUSEHOLD,
        "loans",
        categories.HEALTH_INSURANCE,
        categories.DENTIST,
        categories.DOCTOR,
        "vet",  # last of its group, though its sort_order was given before Groceries had one
    ]
    assert leaves[-1] == "pets_food"
    assert _tops(ranks, "expenses") == [*categories.EXPENSE_GROUPS, "pets"]
    assert _leaves(ranks, "income") == [*categories.INCOME_LEAVES, "rental_income"]


def test_a_group_made_by_moving_a_category_is_the_last_of_the_expenses(
    conn: sqlite3.Connection,
) -> None:
    store.seed_categories(conn, (Extra("Loans", "expenses", categories.GROUP_HOME),))
    taxonomy.move(conn, "loans", "Debts")
    ranks = store.category_ranks(conn)
    assert _tops(ranks, "expenses") == [*categories.EXPENSE_GROUPS, "debts"]
    assert ranks.top("debts", "expenses") < ranks.top(categories.SALARY, "income")
