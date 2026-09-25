"""P1: line-edit patches. LLM_PROVIDER picks the model: liquid (default, LFM2.5-8B-A1B on
PATCH_SERVER_URL, JSON-schema constrained) | openai | anthropic. Edits are applied here; patch()
returns the full new source.

The model sees the file with 1-based line numbers and answers {explanation, edits: [{line_no, new_text}]}.
line_no refers to the original numbering; new_text replaces that whole line and may hold several
lines; an empty new_text deletes the line. Duplicate, out-of-range, missing or unparseable edits
return the source unchanged with an explanation starting "invalid edits:".

Rules are built here, not proposed by the model: each edit on a failure's line gives a rule with
pattern = the original line, replacement = the edited line, signature = that failure's signature.
regex_find/regex_replace come from the smallest span that differs between the two lines, widened to
identifier boundaries, and are kept only if re.sub on the original line reproduces the edited line.
new_rule is the first such rule; new_rules lists all of them (one per signature).

think=True lets the model reason before answering (slow); think=False constrains from token 1 (fast).
"""
import json
import os
import re
from pathlib import Path

import requests

from evergreen.schema import Evidence, PatchResult, Rule

SAMPLING = {"temperature": 0.2, "top_k": 80, "repeat_penalty": 1.05}
MAX_OUTPUT_TOKENS = 4096     # thinking mode reasons first; 2048 cut answers off mid-JSON

PATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "explanation": {"type": "string"},
        "edits": {"type": "array", "items": {
            "type": "object",
            "properties": {"line_no": {"type": "integer"}, "new_text": {"type": "string"}},
            "required": ["line_no", "new_text"], "additionalProperties": False}},
    },
    "required": ["explanation", "edits"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You are upgrading a Python repo from pandas 1.5 to pandas 2.2 by editing one file. "
    "Fix only what the listed failures require and preserve pandas 1.5 behavior exactly. "
    "Never edit tests. Never add try/except, pytest.skip, or xfail. Never delete or rename functions. "
    "Only cite URLs from the evidence provided.\n"
    "The file is shown as 'N| code'. Answer in JSON:\n"
    "- explanation: one or two sentences on the fix.\n"
    "- edits: only the lines you change, each {line_no, new_text}. new_text replaces line line_no "
    "of the file as shown, including its indentation and without the 'N| ' prefix. It may hold "
    "several lines separated by \\n. An empty new_text deletes the line.")

def patch(path, source, failures, hints, feedback, think=True) -> PatchResult:
    hints = list(hints or []) + [None] * (len(failures) - len(hints or []))
    user = build_prompt(path, source, failures, hints, feedback)
    out, prompt_tokens, output_tokens = _call(SYSTEM_PROMPT, user, PATCH_SCHEMA, think=think)
    try:
        answer = json.loads(out) if isinstance(out, str) else out
        edits = answer["edits"]
        explanation = str(answer.get("explanation") or "")
    except (ValueError, KeyError, TypeError) as e:
        return PatchResult(source, f"invalid edits: unparseable model output ({type(e).__name__})",
                           None, prompt_tokens, output_tokens)
    mapped, error = _edit_map(source, edits)
    if error:
        return PatchResult(source, f"invalid edits: {error}", None, prompt_tokens, output_tokens)
    rules = build_rules(source, mapped, failures, hints)
    return PatchResult(_join(source, mapped), explanation, rules[0] if rules else None,
                       prompt_tokens, output_tokens, new_rules=rules)


def numbered(source: str) -> str:
    return "\n".join(f"{i}| {line}" for i, line in enumerate(_split(source)[0], 1))


def build_prompt(path, source, failures, hints, feedback) -> str:
    groups = {}                                # one entry per (signature, line): tests share a fix
    for f, h in zip(failures, hints):
        g = groups.setdefault((f.signature, f.line_no), {"f": f, "tests": [], "hint": None})
        g["tests"].append(f.test_id)
        g["hint"] = g["hint"] or h
    parts = [f"File: {Path(path).as_posix()}", numbered(source), "", "Failures:"]
    for n, g in enumerate(groups.values(), 1):
        f = g["f"]
        where = f"line {f.line_no}: {(f.line or '').strip()}" if f.line_no else "line unknown"
        parts += [f"{n}. {f.signature}", f"   tests: {', '.join(g['tests'])}",
                  f"   {f.exc_type}: {f.message}", f"   at {where}"]
        parts += [f"   {line}" for line in _hint_lines(g["hint"])]
    if feedback:
        parts += ["", "The previous attempt was rejected:", str(feedback).strip(),
                  "Make a different fix."]
    return "\n".join(parts)


def _hint_lines(hint) -> list[str]:
    if isinstance(hint, Rule):
        return [f"Known fix {hint.rule_id}: replace `{hint.pattern}` with `{hint.replacement}`"]
    if isinstance(hint, Evidence):
        return [f"Evidence from {hint.url}:", " ".join(hint.snippet.split())]
    return []


def _split(source: str) -> tuple[list[str], str, bool]:
    eol = "\r\n" if "\r\n" in source else "\n"
    body = source.replace("\r\n", "\n")
    trailing = body.endswith("\n")
    return (body[:-1] if trailing else body).split("\n"), eol, trailing


def _edit_map(source: str, edits) -> tuple[dict[int, list[str]], str | None]:
    """{line_no: replacement lines} ([] deletes), or ({}, reason) when the edits are invalid."""
    lines = _split(source)[0]
    if not edits:
        return {}, "no edits returned"
    mapped = {}
    for e in edits:
        n = e.get("line_no") if isinstance(e, dict) else None
        if not isinstance(n, int) or not isinstance(e.get("new_text"), str):
            return {}, f"malformed edit {e!r:.80}"
        if not 1 <= n <= len(lines):
            return {}, f"line {n} is out of range 1-{len(lines)}"
        if n in mapped:
            return {}, f"line {n} is edited twice"
        text = e["new_text"].replace("\r", "").rstrip("\n")
        new = [re.sub(rf"^ *{n}\| ", "", t, count=1) if i == 0 else t
               for i, t in enumerate(text.split("\n"))] if text.strip() else []
        mapped[n] = _reindent(lines[n - 1], new)
    return mapped, None


def _reindent(original: str, new: list[str]) -> list[str]:
    """Keep the replaced line's indentation. The model often drops or shifts it (r2: "expected an
    indented block"); later lines of a multi-line edit keep their indentation relative to the first."""
    if not new or not original.strip():
        return new
    indent = original[:len(original) - len(original.lstrip())]
    shift = len(new[0]) - len(new[0].lstrip())
    if new[0][:shift] == indent:
        return new
    return [indent + t[min(shift, len(t) - len(t.lstrip())):] if t.strip() else t for t in new]


def _join(source: str, mapped: dict[int, list[str]]) -> str:
    lines, eol, trailing = _split(source)
    out = []
    for n, line in enumerate(lines, 1):
        out.extend(mapped.get(n, [line]))
    return eol.join(out) + (eol if trailing else "")


def apply_edits(source: str, edits) -> tuple[str, str | None]:
    """Returns (new_source, None), or (source, reason) when the edits are invalid."""
    mapped, error = _edit_map(source, edits)
    return (source, error) if error else (_join(source, mapped), None)


def build_rules(source, mapped, failures, hints) -> list[dict]:
    """One rule per signature from the edits on failing lines; regexes kept only if they validate."""
    lines = _split(source)[0]
    rules, seen = [], set()
    for f, h in zip(failures, hints):
        new = mapped.get(f.line_no) if f.line_no else None
        before, after = (lines[f.line_no - 1], "\n".join(new)) if new else (None, None)
        if not new or f.signature in seen or after == before:
            continue
        seen.add(f.signature)
        find, repl = span_regex(before, after)
        rules.append({"signature": f.signature, "pattern": before.strip(), "replacement": after.strip(),
                      "regex_find": find, "regex_replace": repl, "source_url": _source_url(h)})
    return rules


def _source_url(hint):
    if isinstance(hint, Evidence):
        return hint.url
    return hint.source_url if isinstance(hint, Rule) else None


def _word(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def span_regex(before: str, after: str) -> tuple[str | None, str | None]:
    """(regex_find, regex_replace) for the changed span, or (None, None) if it doesn't reproduce `after`.

    "df.iteritems()" -> "df.items()" gives (r"\\biteritems\\b", "items"). A pure insertion such as
    ".mean()" -> ".mean(numeric_only=True)" has no old text of its own, so the span takes in the
    identifier to its left: (r"\\bmean\\(", "mean(numeric_only=True").
    """
    n = min(len(before), len(after))
    p = 0
    while p < n and before[p] == after[p]:
        p += 1
    s = 0
    while s < n - p and before[-1 - s] == after[-1 - s]:
        s += 1
    while p > 0 and _word(before[p - 1]):                  # widen to identifier boundaries
        p -= 1
    while s > 0 and _word(before[len(before) - s]):
        s -= 1
    if p == len(before) - s:                               # nothing removed: anchor on the identifier to the left
        while p > 0 and not _word(before[p - 1]) and not before[p - 1].isspace():
            p -= 1
        while p > 0 and _word(before[p - 1]):
            p -= 1
    old, new = before[p:len(before) - s], after[p:len(after) - s]
    if not old.strip():
        return None, None
    find = (r"\b" if _word(old[0]) else "") + re.escape(old) + (r"\b" if _word(old[-1]) else "")
    repl = new.replace("\\", "\\\\")
    try:
        ok = re.sub(find, repl, before) == after
    except (re.error, IndexError):
        ok = False
    return (find, repl) if ok else (None, None)


def _call(system: str, user: str, schema: dict, think: bool):
    provider = (os.environ.get("LLM_PROVIDER") or "liquid").strip().lower()
    if provider == "liquid":
        return _call_liquid(system, user, schema, think)
    if provider == "openai":
        return _call_openai(system, user, schema)
    if provider == "anthropic":
        return _call_anthropic(system, user, schema)
    raise ValueError(f"unknown LLM_PROVIDER {provider!r} (liquid | openai | anthropic)")


def _call_liquid(system, user, schema, think):
    """Chat endpoint with the schema as response_format. With think=True the model reasons first
    (reasoning_content) and the grammar applies to the answer; think=False constrains from token 1."""
    url = (os.environ.get("PATCH_SERVER_URL") or "http://127.0.0.1:8081").rstrip("/")
    url = url.replace("://localhost", "://127.0.0.1")    # Windows tries IPv6 first for localhost
    r = requests.post(f"{url}/v1/chat/completions", timeout=300, json={
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "submit_patch", "schema": schema}},
        "max_tokens": MAX_OUTPUT_TOKENS, **SAMPLING,
        **({} if think else {"reasoning_format": "none"})})
    r.raise_for_status()
    j = r.json()
    usage = j.get("usage") or {}
    return (j["choices"][0]["message"].get("content") or "",
            usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))


def _call_openai(system, user, schema):
    from openai import OpenAI
    resp = OpenAI().chat.completions.create(
        model=os.environ["LLM_MODEL"], temperature=SAMPLING["temperature"], max_tokens=MAX_OUTPUT_TOKENS,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "submit_patch", "strict": True, "schema": schema}})
    return (resp.choices[0].message.content or "",
            resp.usage.prompt_tokens, resp.usage.completion_tokens)


def _call_anthropic(system, user, schema):
    import anthropic
    resp = anthropic.Anthropic().messages.create(
        model=os.environ["LLM_MODEL"], max_tokens=MAX_OUTPUT_TOKENS, temperature=SAMPLING["temperature"],
        system=system, messages=[{"role": "user", "content": user}],
        tools=[{"name": "submit_patch", "description": "Submit the answer in the required shape.",
                "input_schema": schema}],
        tool_choice={"type": "tool", "name": "submit_patch"})
    block = next(b for b in resp.content if b.type == "tool_use")
    return block.input, resp.usage.input_tokens, resp.usage.output_tokens
