"""Who may see the pages once they are served beyond this machine (D18): one password for
the household, kept as a salted scrypt digest in `scrooge.yaml`, and the sessions of the
browsers that gave it.

Nothing here knows about HTTP. Sessions live in memory: starting the app again signs
everyone out.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

SHORTEST = 12  # characters a password must have
LIFETIME = timedelta(days=30)  # how long a browser stays signed in
LONGEST_WAIT = timedelta(minutes=5)  # between two attempts, however many were wrong
_SCHEME = "scrypt"
_N, _R, _P = 2**14, 8, 1  # the cost: about 16 MB and a few hundredths of a second
_FIELDS = 6
_SALT = 16
_LENGTH = 32


class MalformedDigestError(ValueError):
    """What stands for the password in the config was not written by `scrooge password`."""


class TooSoonError(Exception):
    """A wrong password was just given: the next attempt has to wait."""

    def __init__(self, seconds: int) -> None:
        super().__init__(f"wait {seconds} second{'' if seconds == 1 else 's'} and try again")
        self.seconds = seconds


def digest(password: str, salt: bytes | None = None) -> str:
    """`scrypt$n$r$p$salt$key`: what to keep instead of the password."""
    salt = secrets.token_bytes(_SALT) if salt is None else salt
    key = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_LENGTH)
    return f"{_SCHEME}${_N}${_R}${_P}${salt.hex()}${key.hex()}"


def _read(stored: str) -> tuple[int, int, int, bytes, bytes]:
    parts = stored.split("$")
    if len(parts) != _FIELDS or parts[0] != _SCHEME:
        msg = "not a digest written by 'scrooge password'"
        raise MalformedDigestError(msg)
    try:
        n, r, p = (int(part) for part in parts[1:4])
        salt, key = bytes.fromhex(parts[4]), bytes.fromhex(parts[5])
    except ValueError:
        msg = "not a digest written by 'scrooge password'"
        raise MalformedDigestError(msg) from None
    if (n, r, p) != (_N, _R, _P) or len(salt) != _SALT or len(key) != _LENGTH:
        # Never work at a cost the file names: a weak one would be accepted as it is.
        msg = "a digest of another cost: write it again with 'scrooge password'"
        raise MalformedDigestError(msg)
    return n, r, p, salt, key


def check(stored: str) -> None:
    """Raise `MalformedDigestError` unless `stored` is a digest this module wrote."""
    _read(stored)


def matches(password: str, stored: str) -> bool:
    n, r, p, salt, key = _read(stored)
    given = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=len(key))
    return secrets.compare_digest(given, key)


@dataclass(slots=True)
class Access:
    """The password's digest, the browsers signed in and the wait after a wrong attempt."""

    stored: str
    _sessions: dict[str, datetime] = field(default_factory=dict)  # session -> its last moment
    _wrong: int = 0  # wrong passwords in a row
    _closed_until: datetime | None = None
    _checking: bool = False  # a password is being checked: the digest is slow
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        check(self.stored)

    def enter(self, password: str, now: datetime) -> str | None:
        """A new session for the right password, None for a wrong one.

        Each wrong password in a row doubles the wait before the next attempt, whoever
        makes it; an attempt during the wait is not looked at (`TooSoonError`), nor one
        made while another is still being checked, so guesses sent at once do not all
        get in before the first wrong one closes the door.
        """
        with self._lock:
            if self._closed_until is not None and now < self._closed_until:
                left = self._closed_until - now
                raise TooSoonError(-(-left // timedelta(seconds=1)))
            if self._checking:
                raise TooSoonError(1)
            self._checking = True
        right = False
        try:
            right = matches(password, self.stored)  # slow: not under the lock
        finally:
            with self._lock:  # the door is never open between the check and its record
                self._checking = False
                session = self._record(right, now)
        return session

    def _record(self, right: bool, now: datetime) -> str | None:
        if not right:
            self._wrong += 1
            wait = min(timedelta(seconds=2 ** min(self._wrong - 1, 16)), LONGEST_WAIT)
            self._closed_until = now + wait
            return None
        self._wrong, self._closed_until = 0, None
        self._sessions = {known: end for known, end in self._sessions.items() if now < end}
        session = secrets.token_urlsafe(32)
        self._sessions[session] = now + LIFETIME
        return session

    def known(self, session: str, now: datetime) -> bool:
        end = self._sessions.get(session)
        return end is not None and now < end

    def leave(self, session: str) -> None:
        self._sessions.pop(session, None)
