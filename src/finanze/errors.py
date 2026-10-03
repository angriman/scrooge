"""Errors shared by all extractors."""


class ExtractionError(Exception):
    """A statement could not be parsed. Never swallowed: it blocks the write."""


class UnsupportedStatementError(ExtractionError):
    """The statement contains something no extractor rule covers yet (fail loudly, don't guess)."""
