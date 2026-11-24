"""Change a rules file where it is, one rule at a time, for the settings page of the app.

The file stays the user's: its comments, the blank lines between groups of rules and their
order are kept. Only the text of the one rule changed is written again, as `append_rule`
writes one (`{match: ..., category: ...}`); a rule is found by where YAML says its text
begins and ends, never by reading the file and writing all of it out again. Moving a rule
swaps its lines with those of its neighbour, and the comments around them stay where they are.

A rule is named by its place in the file, counted from 1 as `load_rules` counts them in its
messages, and by a digest of its lines as the page showed them (`Entry.version`): a rule
changed since in any way is refused rather than the wrong rule changed.

What cannot be changed line by line without harm is said, not done: a rule whose dash stands
on a line of its own (taking its lines would leave the dash behind) is changed in the file by
hand, and so is the text of one with comments among its keys (written again, it would lose
them); such a rule is still removed or moved whole.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from scrooge import categories
from scrooge.classification import quoted
from scrooge.errors import ConfigError

# The keys a rule is written with when it is changed here, in their usual order.
_ORDER = ("match", "category", "kind", "account", "direction")


@dataclass(frozen=True, slots=True)
class Entry:
    """One rule of a file, and where its text is."""

    match: str
    category: str
    direction: str
    account: str
    kind: str
    span: tuple[int, int]  # the rule itself, `{...}` or its keys one per line
    lines: tuple[int, int]  # the whole lines it is on, the last newline included
    named: tuple[int, int]  # the category it gives
    # Each key, and `key: value` as it goes back between braces: as it is in a rule written
    # between them already, quoted again from one whose keys are on lines of their own.
    pairs: tuple[tuple[str, str], ...]
    version: str  # a digest of its lines: what the page sends back to say which rule it saw
    lone: bool = False  # its dash is on a line of its own: its lines are not all its own
    comments: bool = False  # comments stand among its keys, which writing it again would lose

    def refusal(self, *, editing: bool) -> str:
        """Why this rule cannot be changed here ("": it can), removed or moved, or `editing`."""
        if self.lone:
            return "its dash stands on a line of its own: write it as '  - {match: ...}' first"
        if editing and self.comments:
            return "it has comments among its keys, which would be lost: edit it in the file"
        return ""


class StaleRuleError(ConfigError):
    """The rule asked for is not where the page saw it: the file changed since."""


def entries(text: str, path: Path) -> tuple[Entry, ...]:
    """The rules of `text` (the file `path`), in the order they are tried."""
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        msg = f"{path}: {exc}"
        raise ConfigError(msg) from None
    listed = _value(root, "rules")
    if listed is None or (isinstance(listed, ScalarNode) and listed.value in {"", "null", "~"}):
        return ()
    if not isinstance(listed, SequenceNode):
        msg = f"{path}: expected a top-level 'rules' list"
        raise ConfigError(msg)
    if listed.flow_style and listed.value:
        msg = f"{path}: write the rules one per line, as '  - {{match: ...}}', to change them here"
        raise ConfigError(msg)
    return tuple(_entry(text, path, n, item) for n, item in enumerate(listed.value, start=1))


def _value(root: Node | None, key: str) -> Node | None:
    if not isinstance(root, MappingNode):
        return None
    return next(
        (value for name, value in root.value if isinstance(name, ScalarNode) and name.value == key),
        None,
    )


def _entry(text: str, path: Path, n: int, item: Node) -> Entry:
    where = f"{path} rule {n}"
    if not isinstance(item, MappingNode) or not item.value:
        msg = f"{where}: needs 'match' and 'category'"
        raise ConfigError(msg)
    values: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    named = (0, 0)
    for key, value in item.value:
        name = str(key.value)
        raw = text[key.start_mark.index : value.end_mark.index]
        if isinstance(value, ScalarNode):
            values[name] = str(value.value)
            if not item.flow_style:
                raw = f"{name}: {_written(name, values[name])}"
        if name == "category":
            named = (value.start_mark.index, value.end_mark.index)
        pairs.append((name, raw))
    if "match" not in values or "category" not in values:
        msg = f"{where}: needs 'match' and 'category'"
        raise ConfigError(msg)
    start = item.start_mark.index
    end = item.end_mark.index if item.flow_style else item.value[-1][1].end_mark.index
    first = text.rfind("\n", 0, start) + 1
    last = text.find("\n", end)
    lines = (first, len(text) if last == -1 else last + 1)
    # What lies between its keys, outside them: spaces, commas, colons, or comments.
    bounds = [(key.start_mark.index, value.end_mark.index) for key, value in item.value]
    ends = [start, *(after for _, after in bounds)]
    starts = [*(before for before, _ in bounds), end]
    gaps = [text[a:b] for a, b in zip(ends, starts, strict=True)]
    return Entry(
        match=values["match"],
        category=categories.key(values["category"]),
        direction=values.get("direction", ""),
        account=values.get("account", ""),
        kind=values.get("kind", ""),
        span=(start, end),
        lines=lines,
        named=named,
        pairs=tuple(pairs),
        version=hashlib.sha256(text[lines[0] : lines[1]].encode()).hexdigest()[:16],
        lone=text[first:start].strip() != "-",
        comments=any("#" in gap for gap in gaps),
    )


def read(path: Path) -> tuple[str, tuple[Entry, ...]]:
    """The text of `path`, ending in a newline, and its rules; none when there is no file."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    return text, entries(text, path)


def find(path: Path, position: int, version: str) -> tuple[str, tuple[Entry, ...], Entry]:
    """The text of `path`, its rules and the one at `position`, which must still be as the
    page showed it (`version`). The file is read once."""
    text, found = read(path)
    if not 1 <= position <= len(found):
        msg = f"{path.name} has no rule {position} any more: load the page again"
        raise StaleRuleError(msg)
    entry = found[position - 1]
    if entry.version != version:
        msg = f"rule {position} of {path.name} changed since the page was shown: load it again"
        raise StaleRuleError(msg)
    return text, found, entry


def rewritten(text: str, entry: Entry, changes: Mapping[str, str]) -> str:
    """`text` with `entry` written again with `changes` made to its keys: a key changed to ""
    is left out, one it did not have is added, every other key keeps its text."""
    parts: list[str] = []
    for name, kept in entry.pairs:
        if name not in changes:
            parts.append(kept)
        elif changes[name]:
            parts.append(f"{name}: {_written(name, changes[name])}")
    had = {name for name, _ in entry.pairs}
    added = sorted(set(changes) - had, key=lambda name: (*_ORDER, name).index(name))
    parts.extend(f"{name}: {_written(name, changes[name])}" for name in added if changes[name])
    start, end = entry.span
    return f"{text[:start]}{{{', '.join(parts)}}}{text[end:]}"


def _written(name: str, value: str) -> str:
    # The direction and the kind are words of their own, as `append_rule` writes them.
    return value if name in {"direction", "kind"} else quoted(value)


def removed(text: str, entry: Entry) -> str:
    """`text` without the lines of `entry`."""
    first, last = entry.lines
    return text[:first] + text[last:]


def swapped(text: str, upper: Entry, lower: Entry) -> str:
    """`text` with the lines of two rules swapped, `upper` being the one before; what lies
    between them (comments, blank lines) stays where it is."""
    (a, b), (c, d) = upper.lines, lower.lines
    return text[:a] + text[c:d] + text[b:c] + text[a:b] + text[d:]


def recategorized(text: str, found: tuple[Entry, ...], old: str, new: str) -> tuple[str, int]:
    """`text` with every rule that gives `old` giving `new` instead, and how many do."""
    changed = [entry for entry in found if entry.category == old]
    for entry in reversed(changed):  # from the end, so that the places before stay right
        start, end = entry.named
        text = text[:start] + quoted(new) + text[end:]
    return text, len(changed)
