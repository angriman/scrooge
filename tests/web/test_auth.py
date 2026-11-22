from datetime import UTC, datetime, timedelta

import pytest

from scrooge.web import auth

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
WORDS = "correct horse battery"
STORED = auth.digest(WORDS, salt=bytes(16))


def second(n: int) -> datetime:
    return NOW + timedelta(seconds=n)


def test_a_digest_matches_its_password_and_no_other() -> None:
    assert auth.matches(WORDS, STORED)
    assert not auth.matches(WORDS + " ", STORED)
    assert not auth.matches("", STORED)
    assert WORDS not in STORED


def test_two_digests_of_one_password_differ_by_their_salt() -> None:
    first, again = auth.digest(WORDS), auth.digest(WORDS)
    assert first != again
    assert auth.matches(WORDS, first)
    assert auth.matches(WORDS, again)
    assert auth.digest(WORDS, salt=bytes(16)) == STORED


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "hunter2",
        "md5$16384$8$1$00$00",
        STORED + "$more",
        STORED.replace("$16384$", "$many$"),
        STORED[:-1] + "z",  # not hexadecimal
    ],
)
def test_what_scrooge_password_did_not_write_is_no_digest(stored: str) -> None:
    with pytest.raises(auth.MalformedDigestError, match="not a digest written by"):
        auth.check(stored)
    with pytest.raises(auth.MalformedDigestError):
        auth.Access(stored)


@pytest.mark.parametrize(
    "stored",
    [
        STORED.replace("$16384$", "$2$"),  # a cost anyone could afford to try every word at
        STORED[:-2],  # a shorter key
        STORED.replace("$" + "00" * 16 + "$", "$00$"),  # hardly any salt
    ],
)
def test_a_digest_of_another_cost_is_refused_not_worked_at(stored: str) -> None:
    with pytest.raises(auth.MalformedDigestError, match="another cost"):
        auth.check(stored)


def test_the_right_password_opens_a_session_that_lasts_thirty_days() -> None:
    access = auth.Access(STORED)
    session = access.enter(WORDS, NOW)
    assert session is not None
    assert len(session) >= 32
    assert access.known(session, NOW)
    assert access.known(session, NOW + auth.LIFETIME - timedelta(seconds=1))
    assert not access.known(session, NOW + auth.LIFETIME)
    assert not access.known("", NOW)
    assert not access.known(session + "x", NOW)
    assert access.enter(WORDS, NOW) != session  # each browser has its own


def test_leaving_ends_one_session_only() -> None:
    access = auth.Access(STORED)
    phone, laptop = access.enter(WORDS, NOW), access.enter(WORDS, NOW)
    assert phone is not None
    assert laptop is not None
    access.leave(phone)
    access.leave(phone)  # twice is once
    assert not access.known(phone, NOW)
    assert access.known(laptop, NOW)


def test_sessions_that_ended_are_forgotten_at_the_next_sign_in() -> None:
    access = auth.Access(STORED)
    old = access.enter(WORDS, NOW)
    recent = access.enter(WORDS, NOW + timedelta(days=20))
    access.enter(WORDS, NOW + auth.LIFETIME)
    assert old is not None
    assert recent is not None
    assert not access.known(old, NOW)  # gone, not merely over
    assert access.known(recent, NOW + auth.LIFETIME)


def test_each_wrong_password_doubles_the_wait_up_to_five_minutes() -> None:
    access = auth.Access(STORED)
    at = 0
    for wait in (1, 2, 4, 8, 16, 32, 64, 128, 256, 300, 300):
        assert access.enter("wrong", second(at)) is None
        with pytest.raises(auth.TooSoonError) as raised:
            access.enter(WORDS, second(at + wait) - timedelta(milliseconds=1))
        assert raised.value.seconds == 1
        at += wait
    assert access.enter(WORDS, second(at)) is not None


def test_an_attempt_during_the_wait_says_how_long_is_left_and_does_not_count() -> None:
    access = auth.Access(STORED)
    access.enter("wrong", NOW)
    access.enter("wrong", second(1))
    with pytest.raises(auth.TooSoonError, match="wait 2 seconds and try again") as raised:
        access.enter(WORDS, second(1))
    assert raised.value.seconds == 2
    with pytest.raises(auth.TooSoonError, match="wait 1 second and try again"):
        access.enter(WORDS, second(2) + timedelta(milliseconds=400))
    assert access.enter(WORDS, second(3)) is not None  # the wait did not grow meanwhile


def test_the_right_password_ends_the_count_of_wrong_ones() -> None:
    access = auth.Access(STORED)
    access.enter("wrong", NOW)
    access.enter("wrong", second(1))
    assert access.enter(WORDS, second(3)) is not None
    assert access.enter("wrong", second(4)) is None
    assert access.enter(WORDS, second(5)) is not None  # one second again, not four
