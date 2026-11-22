"""Errors shared by all extractors, and by the files the user edits."""


class ExtractionError(Exception):
    """A statement could not be parsed. Never swallowed: it blocks the write."""


class UnsupportedStatementError(ExtractionError):
    """The statement contains something no extractor rule covers yet (fail loudly, don't guess)."""


class OcrUnavailableError(ExtractionError):
    """A scanned statement needs Tesseract and the program is not installed."""


class ConfigError(Exception):
    """rules.yaml, categories.yaml or instruments.yaml is malformed."""
