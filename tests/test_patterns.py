"""Patterns run away from the server, and stopped when their time is up."""

import time

from scrooge import patterns

KONSUM = ("Migros", False, "KONSUM", "")
SALARY = ("Migros", True, "ACME", "ACME SWITZERLAND")
SLOW = ("Migros", False, "a" * 40 + "b", "")  # `(a+)+$` takes for ever on it


def test_matching_hands_back_what_it_decided_a_batch_at_a_time() -> None:
    got: list[object] = []
    texts = [KONSUM, SALARY] * 300
    patterns.matching(got.append, "konsum", "out", texts)
    assert got == [(500, list(range(0, 500, 2))), (600, list(range(500, 600, 2)))]


def test_counting_hands_back_each_rule_in_its_order() -> None:
    got: list[object] = []
    texts = [("Lukas", KONSUM, 3), ("Anna", KONSUM, 1), ("Lukas", SALARY, 2)]
    specs = [
        ("konsum", "", "", None),
        ("konsum", "", "", "Anna"),
        ("acme", "Revolut", "", None),  # another account
        ("acme", "", "in", None),
        ("(", "", "", None),
    ]
    patterns.counting(got.append, specs, texts)
    assert got == [(0, 4), (1, 1), (2, 0), (3, 2), (4, None)]


def test_a_pattern_runs_in_a_process_of_its_own() -> None:
    got, complete = patterns.bounded(patterns.matching, ("acme", "", [KONSUM, SALARY]), 10)
    assert (got, complete) == ([(2, [1])], True)


def test_a_pattern_that_takes_for_ever_is_stopped() -> None:
    started = time.monotonic()
    got, complete = patterns.bounded(patterns.matching, ("(a+)+$", "", [SLOW]), 0.5)
    assert (got, complete) == ([], False)
    assert time.monotonic() - started < 5


def test_work_that_fails_is_not_complete() -> None:
    got, complete = patterns.bounded(patterns.matching, ("(", "", [KONSUM]), 10)
    assert (got, complete) == ([], False)
