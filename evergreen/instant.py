"""P1: zero-token rule application (brief 12.3).

A rule's regex (validated at birth by patcher.span_regex) is applied only to the line the traceback
points at, never file-wide, so a Python list's .append() elsewhere is never touched. The result
still goes through the guards, the full suite and the golden ratchet like any other patch.
"""
import re

from evergreen.schema import Failure, Rule


def apply_instant(source: str, failure: Failure, rule: Rule) -> str | None:
    """`source` with the rule's regex applied to the failure's line, or None if it doesn't apply."""
    if not rule.regex_find or rule.regex_replace is None or not failure.line_no:
        return None
    eol = "\r\n" if "\r\n" in source else "\n"
    lines = source.split(eol)
    i = failure.line_no - 1
    if not 0 <= i < len(lines):
        return None
    try:
        new = re.sub(rule.regex_find, rule.regex_replace, lines[i])
    except (re.error, IndexError):
        return None
    if new == lines[i]:
        return None
    lines[i] = new.replace("\r\n", "\n").replace("\n", eol)
    return eol.join(lines)
