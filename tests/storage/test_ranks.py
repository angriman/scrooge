"""The taxonomy's order, which lists of categories follow: never the names, which change with
the language."""

import sqlite3

from scrooge import categories
from scrooge.categories import Extra
from scrooge.storage import store, taxonomy

_EXPENSES = [leaf for leaves in categories.USCITE.values() for leaf in leaves]


def _tops(ranks: store.Ranks, side: str) -> list[str]:
    return sorted((n for s, n in ranks.tops if s == side), key=lambda n: ranks.top(n, side))


def _leaves(ranks: store.Ranks, side: str) -> list[str]:
    return sorted((n for s, n in ranks.leaves if s == side), key=lambda n: ranks.leaf(n, side))


def test_the_taxonomy_ranks_as_categories_py_lists_it(conn: sqlite3.Connection) -> None:
    store.seed_categories(conn)
    ranks = store.category_ranks(conn)
    assert _tops(ranks, "uscite") == list(categories.USCITE)
    assert _leaves(ranks, "uscite") == list(dict.fromkeys(_EXPENSES))
    assert _leaves(ranks, "entrate") == list(categories.ENTRATE)
    assert _leaves(ranks, "transfer") == list(categories.TRANSFERS)
    # Not by name.
    assert ranks.top(categories.GROUP_FOOD, "uscite") < ranks.top(
        categories.GROUP_EATING_OUT, "uscite"
    )
    assert ranks.top(categories.UNSORTED, "uscite") < ranks.top(categories.SALARY, "entrate")


def test_a_name_on_both_sides_is_ranked_on_the_side_asked_first(conn: sqlite3.Connection) -> None:
    store.seed_categories(conn)
    ranks = store.category_ranks(conn)
    assert ranks.leaf(categories.TRADING, "uscite") < ranks.leaf(categories.INVESTMENTS, "uscite")
    assert ranks.leaf(categories.TRADING, "entrate") < ranks.leaf(categories.INVESTMENTS, "entrate")
    assert ranks.leaf(categories.TRADING, "entrate") > ranks.leaf(categories.SALARY, "entrate")
    # Expenses first.
    assert ranks.leaf(categories.TRADING) == ranks.leaf(categories.TRADING, "uscite")
    # No group of that name.
    assert ranks.top(categories.TRADING) == ranks.top(categories.TRADING, "entrate")


def test_a_name_the_taxonomy_lacks_comes_after_every_category(conn: sqlite3.Connection) -> None:
    store.seed_categories(conn)
    ranks = store.category_ranks(conn)
    assert ranks.top("Transfers") == len(ranks.tops)
    assert ranks.leaf(categories.SALARY, "uscite") == len(ranks.leaves)
    assert ranks.leaf("", "") == len(ranks.leaves)


def test_what_categories_yaml_adds_comes_after_the_taxonomy_it_joins(
    conn: sqlite3.Connection,
) -> None:
    store.seed_categories(conn)  # a database of before the additions
    store.seed_categories(conn, (Extra("Loans", "uscite", categories.GROUP_HOME),))
    store.seed_categories(
        conn,
        (
            Extra("Loans", "uscite", categories.GROUP_HOME),
            Extra("Vet", "uscite", categories.GROUP_HEALTH),
            Extra("Pets food", "uscite", "Pets"),
            Extra("Rental income", "entrate"),
        ),
    )
    ranks = store.category_ranks(conn)
    leaves = _leaves(ranks, "uscite")
    assert leaves[:8] == [
        categories.RENT,
        categories.UTILITIES,
        categories.HOUSEHOLD,
        "Loans",
        categories.HEALTH_INSURANCE,
        categories.DENTIST,
        categories.DOCTOR,
        "Vet",  # last of its group, though its sort_order was given before Groceries had one
    ]
    assert leaves[-1] == "Pets food"
    assert _tops(ranks, "uscite") == [*categories.USCITE, "Pets"]
    assert _leaves(ranks, "entrate") == [*categories.ENTRATE, "Rental income"]


def test_a_group_made_by_moving_a_category_is_the_last_of_the_expenses(
    conn: sqlite3.Connection,
) -> None:
    store.seed_categories(conn, (Extra("Loans", "uscite", categories.GROUP_HOME),))
    taxonomy.move(conn, "Loans", "Debts")
    ranks = store.category_ranks(conn)
    assert _tops(ranks, "uscite") == [*categories.USCITE, "Debts"]
    assert ranks.top("Debts", "uscite") < ranks.top(categories.SALARY, "entrate")
