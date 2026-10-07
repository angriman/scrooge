import io
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from finanze import categories, review
from finanze.categories import Extra
from finanze.classification import load_rules
from finanze.errors import ConfigError
from finanze.models import Kind, Transaction

HEADER = "# Categories added to the taxonomy.\n# Second line.\n"
LENT = Transaction(
    date=date(2026, 9, 1),
    account="Revolut CHF",
    amount=Decimal("-50.00"),
    currency="CHF",
    description="To Jonas Example",
    kind=Kind.EXPENSE,
    source_file="Revolut CHF.tsv",
    source_ref="1",
)


def test_a_missing_or_empty_file_adds_nothing(tmp_path: Path) -> None:
    path = tmp_path / "categories.yaml"
    assert categories.load(path) == ()
    path.write_text(HEADER, encoding="utf-8")
    assert categories.load(path) == ()
    assert categories.names(()) == categories.ORDER
    assert categories.groups(()) == tuple(categories.USCITE)


def test_added_categories_are_saved_under_the_comments_and_read_back(tmp_path: Path) -> None:
    path = tmp_path / "categories.yaml"
    path.write_text(HEADER, encoding="utf-8")
    added = (
        Extra("Prestiti", "uscite", "Prestiti"),
        Extra("Giardino", "uscite", "Casa"),
        Extra("Prestiti", "entrate"),
    )
    for extra in (*added, added[0]):  # saving one twice changes nothing
        categories.add(path, extra)
    assert path.read_text(encoding="utf-8").startswith(HEADER + "categories:\n  uscite:\n")
    assert categories.load(path) == added
    assert categories.names(added) == (*categories.ORDER, "Prestiti", "Giardino")
    assert categories.groups(added) == (*categories.USCITE, "Prestiti")


def test_a_rule_may_name_an_added_category(tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text("rules:\n  - {match: Jonas, category: Prestiti}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown category 'Prestiti'"):
        load_rules([rules])
    known = categories.names((Extra("Prestiti", "entrate"),))
    assert load_rules([rules], known)[0].category == "Prestiti"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("- Prestiti\n", "top-level 'categories'"),
        ("categories: [Prestiti]\n", "top-level 'categories'"),
        ("categories: {transfer: [Giroconto]}\n", "top-level 'categories'"),
        ("categories: {uscite: [Prestiti]}\n", "'uscite' maps each top-level category"),
        ("categories: {uscite: {Casa: Giardino}}\n", "'uscite' maps each top-level category"),
        ("categories: {entrate: [1]}\n", "'entrate' is a list of names"),
        ("categories: {entrate: ['']}\n", "'entrate' is a list of names"),
    ],
)
def test_a_malformed_file_is_refused(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "categories.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        categories.load(path)


def test_review_adds_categories_on_the_side_the_money_went(tmp_path: Path) -> None:
    rules, added = tmp_path / "rules.local.yaml", tmp_path / "categories.yaml"
    pending = [
        LENT,
        replace(LENT, description="From Jonas Example", amount=Decimal("50.00")),
        replace(LENT, description="Garden Centre"),
        replace(LENT, description="Another Friend"),
        replace(LENT, description="Hardware Store"),
    ]
    answers = iter(["+", "+ casa: ", "+Prestiti", "+Other:Prestiti", "+casa:Giardino", "prestiti"])
    asked: list[str] = []

    def ask(question: str) -> str:
        asked.append(question)
        return next(answers, str(len(categories.ORDER) + 2))  # the number Giardino was given

    out = io.StringIO()
    review.ask_categories(pending, rules, added, out, ask)
    text = out.getvalue()
    assert "A new category: +Name, or +Group:Name" in text
    assert "unknown category '+'" in asked[1]
    assert "unknown category '+ casa:'" in asked[2]
    number = len(categories.ORDER) + 1
    assert f"new category {number}=Prestiti (uscite under Prestiti) in {added}" in text
    assert f"new category {number}=Prestiti (entrate) in {added}" in text  # a group means nothing
    assert f"new category {number + 1}=Giardino (uscite under Casa) in {added}" in text
    assert categories.load(added) == (
        Extra("Prestiti", "uscite", "Prestiti"),
        Extra("Giardino", "uscite", "Casa"),
        Extra("Prestiti", "entrate"),
    )
    saved = [line.partition("category: ")[2] for line in rules.read_text("utf-8").splitlines()[1:]]
    assert saved == [
        '"Prestiti", direction: out}',
        '"Prestiti", direction: in}',
        '"Giardino", direction: out}',
        '"Prestiti", direction: out}',
        '"Giardino", direction: out}',
    ]

    # the menu of the next session lists them
    out = io.StringIO()
    review.ask_categories([replace(LENT, description="New")], rules, added, out, lambda _: "q")
    assert f"{number}=Prestiti  {number + 1}=Giardino\n" in out.getvalue()
