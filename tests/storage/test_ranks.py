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
    assert ranks.top("Food", "uscite") < ranks.top("Eating out", "uscite")  # not by name
    assert ranks.top("Uncategorized", "uscite") < ranks.top("Salary", "entrate")


def test_a_name_on_both_sides_is_ranked_on_the_side_asked_first(conn: sqlite3.Connection) -> None:
    store.seed_categories(conn)
    ranks = store.category_ranks(conn)
    assert ranks.leaf("Trading", "uscite") < ranks.leaf("Investments", "uscite")
    assert ranks.leaf("Trading", "entrate") < ranks.leaf("Investments", "entrate")
    assert ranks.leaf("Trading", "entrate") > ranks.leaf("Salary", "entrate")
    assert ranks.leaf("Trading") == ranks.leaf("Trading", "uscite")  # expenses first
    assert ranks.top("Trading") == ranks.top("Trading", "entrate")  # no group of that name


def test_a_name_the_taxonomy_lacks_comes_after_every_category(conn: sqlite3.Connection) -> None:
    store.seed_categories(conn)
    ranks = store.category_ranks(conn)
    assert ranks.top("Transfers") == len(ranks.tops)
    assert ranks.leaf("Salary", "uscite") == len(ranks.leaves)
    assert ranks.leaf("", "") == len(ranks.leaves)


def test_what_categories_yaml_adds_comes_after_the_taxonomy_it_joins(
    conn: sqlite3.Connection,
) -> None:
    store.seed_categories(conn)  # a database of before the additions
    store.seed_categories(conn, (Extra("Loans", "uscite", "Home"),))
    store.seed_categories(
        conn,
        (
            Extra("Loans", "uscite", "Home"),
            Extra("Vet", "uscite", "Health"),
            Extra("Pets food", "uscite", "Pets"),
            Extra("Rental income", "entrate"),
        ),
    )
    ranks = store.category_ranks(conn)
    leaves = _leaves(ranks, "uscite")
    assert leaves[:8] == [
        "Rent",
        "Utilities",
        "Household",
        "Loans",
        "Health insurance",
        "Dentist",
        "Doctor/Pharmacy",
        "Vet",  # last of its group, though its sort_order was given before Groceries had one
    ]
    assert leaves[-1] == "Pets food"
    assert _tops(ranks, "uscite") == [*categories.USCITE, "Pets"]
    assert _leaves(ranks, "entrate") == [*categories.ENTRATE, "Rental income"]


def test_a_group_made_by_moving_a_category_is_the_last_of_the_expenses(
    conn: sqlite3.Connection,
) -> None:
    store.seed_categories(conn, (Extra("Loans", "uscite", "Home"),))
    taxonomy.move(conn, "Loans", "Debts")
    ranks = store.category_ranks(conn)
    assert _tops(ranks, "uscite") == [*categories.USCITE, "Debts"]
    assert ranks.top("Debts", "uscite") < ranks.top("Salary", "entrate")
