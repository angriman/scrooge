"""Change a rules file where it is, one rule at a time, for the settings page of the app.

The file stays the user's: its comments, the blank lines between groups of rules and their
order are kept. Only the text of the one rule changed is written again, between braces
(`{match: ..., category: ...}`); a rule is found by where YAML says its text begins and ends,
never by reading the file and writing all of it out again. Moving a rule swaps its lines with
those of its neighbour, and the comments around them stay where they are.

A rule written again keeps the style of the file: a value given as it was keeps its text, and
one changed keeps the quotes it had, single or double. A value that had none, or a key added,
is written plain where YAML reads it back as it is, and between single quotes where it would
not (`yes`, `A, B`, `A #1`); double quotes are left for what single ones cannot hold.

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
    # between them already, written again in its style from one whose keys are on lines of
    # their own.
    pairs: tuple[tuple[str, str], ...]
    # Each key whose value is text, that text and how it is quoted ("", "'" or '"').
    scalars: tuple[tuple[str, str, str], ...]
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
    root = _compose(text, path)
    pair = _pair(root, "rules")
    listed = _listed(pair[1] if pair else None, path)
    if listed is None:
        return ()
    return tuple(_entry(text, path, n, item) for n, item in enumerate(listed.value, start=1))


def _compose(text: str, path: Path) -> Node | None:
    try:
        root: Node | None = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        msg = f"{path}: {exc}"
        raise ConfigError(msg) from None
    return root


def _pair(root: Node | None, key: str) -> tuple[ScalarNode, Node] | None:
    if not isinstance(root, MappingNode):
        return None
    return next(
        (
            (name, value)
            for name, value in root.value
            if isinstance(name, ScalarNode) and name.value == key
        ),
        None,
    )


def _listed(listed: Node | None, path: Path) -> SequenceNode | None:
    if listed is None or (isinstance(listed, ScalarNode) and listed.value in {"", "null", "~"}):
        return None
    if not isinstance(listed, SequenceNode):
        msg = f"{path}: expected a top-level 'rules' list"
        raise ConfigError(msg)
    if listed.flow_style and listed.value:
        msg = f"{path}: write the rules one per line, as '  - {{match: ...}}', to change them here"
        raise ConfigError(msg)
    return listed


def _entry(text: str, path: Path, n: int, item: Node) -> Entry:
    where = f"{path} rule {n}"
    if not isinstance(item, MappingNode) or not item.value:
        msg = f"{where}: needs 'match' and 'category'"
        raise ConfigError(msg)
    values: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    scalars: list[tuple[str, str, str]] = []
    named = (0, 0)
    for key, value in item.value:
        name = str(key.value)
        raw = text[key.start_mark.index : value.end_mark.index]
        if isinstance(value, ScalarNode):
            values[name] = str(value.value)
            style = _style(value)
            scalars.append((name, values[name], style))
            if not item.flow_style:
                was = text[value.start_mark.index : value.end_mark.index]
                raw = f"{name}: {_kept(was, values[name], style)}"
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
        scalars=tuple(scalars),
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
    is left out, one it did not have is added, every other key, and one given as it was, keeps
    its text."""
    was = {name: (value, style) for name, value, style in entry.scalars}
    parts: list[str] = []
    for name, kept in entry.pairs:
        if name not in changes or (name in was and changes[name] == was[name][0]):
            parts.append(kept)
        elif changes[name]:
            parts.append(f"{name}: {_written(changes[name], was.get(name, ('', ''))[1])}")
    had = {name for name, _ in entry.pairs}
    added = sorted(set(changes) - had, key=lambda name: (*_ORDER, name).index(name))
    parts.extend(f"{name}: {_written(changes[name], '')}" for name in added if changes[name])
    start, end = entry.span
    return f"{text[:start]}{{{', '.join(parts)}}}{text[end:]}"


def _style(node: ScalarNode) -> str:
    """How `node` is quoted: "" plain, "'" or '"'; a block of lines (`|`, `>`) as '"'."""
    return "" if node.style is None else node.style if node.style in {"'", '"'} else '"'


def _kept(was: str, value: str, style: str) -> str:
    """A value of a rule on lines of its own, `was` in the file, as it goes between braces:
    as it is, unless it spans lines or, plain, would not be read between braces as it is."""
    if "\n" in was or (not style and not _plain(value)):
        return _written(value, style)
    return was


def _written(value: str, style: str) -> str:
    """`value` in `style` ("" plain, "'" or '"'), as far as it can be written so."""
    if style == '"':
        return quoted(value)
    if not style and _plain(value):
        return value
    if value.isprintable():
        return "'" + value.replace("'", "''") + "'"
    return quoted(value)


def _plain(value: str) -> bool:
    """Whether YAML reads `value` unquoted as this very text, between braces and after a key."""
    if not value or value != value.strip() or not value.isprintable():
        return False
    try:
        loaded = (yaml.safe_load(f"{{x: {value}}}"), yaml.safe_load(f"x: {value}\n"))
    except yaml.YAMLError:
        return False
    return loaded == ({"x": value}, {"x": value})


def removed(text: str, entry: Entry) -> str:
    """`text` without the lines of `entry`."""
    first, last = entry.lines
    return text[:first] + text[last:]


def swapped(text: str, upper: Entry, lower: Entry) -> str:
    """`text` with the lines of two rules swapped, `upper` being the one before; what lies
    between them (comments, blank lines) stays where it is."""
    (a, b), (c, d) = upper.lines, lower.lines
    return text[:a] + text[c:d] + text[b:c] + text[a:b] + text[d:]


def inserted(text: str, path: Path, block: str, *, first: bool = False) -> str:
    """`text` (the file `path`) with the raw YAML lines `block` of a rule added to its `rules`
    list: right after `rules:` when `first`, or right after the last existing rule otherwise.
    Re-indents `block` to match the destination list (two spaces when none are there yet)."""
    if text and not text.endswith("\n"):
        text += "\n"
    if not block.endswith("\n"):
        block += "\n"
    root = _compose(text, path)
    if root is None:
        return text + "rules:\n" + _reindented(block, "  ")
    if not isinstance(root, MappingNode):
        msg = f"{path}: expected a mapping with a top-level 'rules' entry"
        raise ConfigError(msg)
    pair = _pair(root, "rules")
    if pair is None:
        return text + "rules:\n" + _reindented(block, "  ")
    key, value = pair
    listed = _listed(value, path)
    if listed is not None and listed.value:
        found = tuple(_entry(text, path, n, item) for n, item in enumerate(listed.value, start=1))
        indent = " " * listed.start_mark.column
        at = text.find("\n", key.end_mark.index) + 1 if first else found[-1].lines[1]
        return text[:at] + _reindented(block, indent) + text[at:]
    text, at = _cleared_empty(text, key, value)
    return text[:at] + _reindented(block, "  ") + text[at:]


def _cleared_empty(text: str, key: ScalarNode, value: Node) -> tuple[str, int]:
    """Remove an empty `rules` value (`null`, `~`, `[]`) so a block item can follow `rules:`,
    returning the cleaned text and the offset right after the `rules:` line."""
    colon = text.find(":", key.end_mark.index)
    line_end = text.find("\n", colon)
    start, end = value.start_mark.index, value.end_mark.index
    if start < end:
        if start <= line_end:
            after = text[end:]
            sep = " " if after.startswith("#") else ""
            text = text[: colon + 1] + sep + after
        else:
            text = text[:start] + text[end:]
    return text, text.find("\n", colon) + 1


def _indent(block: str) -> str:
    line = block.splitlines()[0]
    return line[: len(line) - len(line.lstrip(" "))]


def _reindented(block: str, indent: str) -> str:
    source = _indent(block)
    if source == indent:
        return block
    return "".join(
        f"{indent}{line.removeprefix(source)}" if line.strip() else line
        for line in block.splitlines(keepends=True)
    )


def recategorized(text: str, found: tuple[Entry, ...], old: str, new: str) -> tuple[str, int]:
    """`text` with every rule that gives `old` giving `new` instead, and how many do."""
    changed = [entry for entry in found if entry.category == old]
    for entry in reversed(changed):  # from the end, so that the places before stay right
        start, end = entry.named
        style = next(style for name, _, style in entry.scalars if name == "category")
        text = text[:start] + _written(new, style) + text[end:]
    return text, len(changed)
