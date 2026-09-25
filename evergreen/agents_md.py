"""P3: AGENTS.md writer. Owns only the block between the markers; the rest stays byte-identical.

write_agents_md(repo, rules, retired, needs_human):
  rules        Rules to list as verified; only status verified/trusted are shown.
  retired      each item is a Rule or a (Rule, reason) tuple.
  needs_human  strings, one bullet each.
"""
import re
from pathlib import Path

from evergreen.memory import confidence

START, END = "<!-- evergreen:start -->", "<!-- evergreen:end -->"


def _natural(rule):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", rule.rule_id)]


def _library(rule):
    return rule.proven_on[0].split()[0] if rule.proven_on else "other"


def _proof(rule):
    if not rule.proof:
        return ""
    more = f" (+{len(rule.proof) - 1} more)" if len(rule.proof) > 1 else ""
    return f" by `{rule.proof[0]}`{more}"


def _rule_lines(rule):
    proven = ", ".join(rule.proven_on) or "an unrecorded version"
    return [f"- **{rule.rule_id} · `{rule.signature}`** ({rule.status}). "
            f"Replace `{rule.pattern}` with `{rule.replacement}`.",
            f"  Proven on {proven}{_proof(rule)} · confidence {confidence(rule):.2f} · "
            f"source: {rule.source_url or 'unsourced'}"]


def _retired_lines(item):
    rule, reason = item if isinstance(item, tuple) else (item, None)
    proven = ", ".join(rule.proven_on) or "an unrecorded version"
    return [f"- ~~{rule.rule_id} · `{rule.signature}`: replace `{rule.pattern}` with `{rule.replacement}`~~",
            f"  {reason or 'No longer holds'}. Was proven on {proven} · source: {rule.source_url or 'unsourced'}"]


def render_block(rules, retired, needs_human) -> list[str]:
    lines = [START, "## Library rules (verified by Evergreen)"]
    live = sorted((r for r in rules if r.status in ("verified", "trusted")), key=_natural)
    if not live:
        lines += ["", "_No verified rules yet._"]
    for lib in dict.fromkeys(_library(r) for r in live):
        lines += ["", f"### {lib}"]
        for r in (r for r in live if _library(r) == lib):
            lines += _rule_lines(r)
    if retired:
        lines += ["", "## Retired rules"]
        for item in retired:
            lines += _retired_lines(item)
    if needs_human:
        lines += ["", "## Needs a human"] + [f"- {item}" for item in needs_human]
    return lines + [END]


def write_agents_md(repo, rules, retired, needs_human) -> None:
    path = Path(repo) / "AGENTS.md"
    text = ""
    if path.exists():
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
    eol = "\r\n" if "\r\n" in text else "\n"
    block = eol.join(render_block(rules, retired, needs_human))

    if START in text and END in text:
        head, rest = text.split(START, 1)
        text = head + block + rest.split(END, 1)[1]
    else:
        if text and not text.endswith(eol):
            text += eol
        if text and not text.endswith(eol * 2):
            text += eol
        text += block + eol
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
