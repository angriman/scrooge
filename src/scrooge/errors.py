"""Errors shared by all extractors, and by the files the user edits."""


class ExtractionError(Exception):
    """A statement could not be parsed. Never swallowed: it blocks the write."""


class UnsupportedStatementError(ExtractionError):
    """The statement contains something no extractor rule covers yet (fail loudly, don't guess)."""


class OcrUnavailableError(ExtractionError):
    """A scanned statement needs Tesseract and the program is not installed."""


class ConfigError(Exception):
    """rules.yaml, categories.yaml or instruments.yaml is malformed."""


def not_yaml(path: object, exc: Exception) -> str:
    """What is wrong with a file YAML could not read, in one line: where, and what."""
    mark = getattr(exc, "problem_mark", None)
    where = f"line {mark.line + 1}" if mark is not None else "not valid YAML"
    problem = getattr(exc, "problem", None) or "not valid YAML"
    return f"{path}: {where}: {problem}"
