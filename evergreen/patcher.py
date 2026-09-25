"""P1: line-edit patches. LLM_PROVIDER picks the model: liquid (default, LFM2.5-8B-A1B on
PATCH_SERVER_URL, JSON-schema constrained) | openai | anthropic. Edits are applied here; patch()
returns the full new source.

The model sees the file with 1-based line numbers and answers {explanation, edits: [{line_no, new_text}]}.
line_no refers to the original numbering; new_text replaces that whole line and may hold several
lines; an empty new_text deletes the line. Duplicate, out-of-range, missing or unparseable edits
return the source unchanged with an explanation starting "invalid edits:".

Rules are built here, not proposed by the model: each edit on a failure's line gives a rule with
pattern = the original line, replacement = the edited line, signature = that failure's signature.
One short call (thinking off) proposes regex_find/regex_replace; the regex is kept only if applying
it to the original line reproduces the edited line exactly. new_rule is the first such rule;
result.new_rules lists all of them (one per signature).
"""
import json
import os
import re
from pathlib import Path

import requests

from evergreen.schema import Evidence, PatchResult, Rule

SAMPLING = {"temperature": 0.2, "top_k": 80, "repeat_penalty": 1.05}
MAX_OUTPUT_TOKENS = 2048

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

REGEX_SYSTEM = (
    "You write Python regular expressions for one-line code fixes. For each item give regex_find "
    "and regex_replace so that re.sub(regex_find, regex_replace, before) == after exactly, matching "
    "only the changed code so the same fix works on other lines. Escape regex metacharacters such as "
    "( ) . [ ] ? * +. Use groups like \\1 for parts that vary. Answer in JSON.")


def patch(path, source, failures, hints, feedback) -> PatchResult:
    hints = list(hints or []) + [None] * (len(failures) - len(hints or []))
    user = build_prompt(path, source, failures, hints, feedback)
    out, prompt_tokens, output_tokens = _call(SYSTEM_PROMPT, user, PATCH_SCHEMA, think=True)
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
    rules, rule_tokens = build_rules(source, mapped, failures, hints)
    result = PatchResult(_join(source, mapped), explanation, rules[0] if rules else None,
                         prompt_tokens + rule_tokens[0], output_tokens + rule_tokens[1])
    result.new_rules = rules
    return result


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
        indent = lines[n - 1][:len(lines[n - 1]) - len(lines[n - 1].lstrip())]
        if new and indent and not new[0][:1].isspace():   # model dropped the indentation: restore it
            new = [indent + t if t.strip() else t for t in new]
        mapped[n] = new
    return mapped, None


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


def build_rules(source, mapped, failures, hints) -> tuple[list[dict], tuple[int, int]]:
    """One rule per signature from the edits on failing lines; regexes kept only if they validate."""
    lines = _split(source)[0]
    rules, seen = [], set()
    for f, h in zip(failures, hints):
        new = mapped.get(f.line_no) if f.line_no else None
        before, after = (lines[f.line_no - 1], "\n".join(new)) if new else (None, None)
        if not new or f.signature in seen or after == before:
            continue
        seen.add(f.signature)
        rules.append({"signature": f.signature, "pattern": before.strip(), "replacement": after.strip(),
                      "regex_find": None, "regex_replace": None, "source_url": _source_url(h),
                      "_before": before, "_after": after})
    tokens = (0, 0)
    if rules:
        try:
            tokens = _add_regexes(rules)
        except (requests.RequestException, ValueError, KeyError, TypeError):
            pass                               # the rule stays a hint without an instant transform
    for r in rules:
        del r["_before"], r["_after"]
    return rules, tokens


def _source_url(hint):
    if isinstance(hint, Evidence):
        return hint.url
    return hint.source_url if isinstance(hint, Rule) else None


def _add_regexes(rules) -> tuple[int, int]:
    n = len(rules)
    schema = {"type": "object", "required": ["regexes"], "additionalProperties": False, "properties": {
        "regexes": {"type": "array", "minItems": n, "maxItems": n, "items": {
            "type": "object", "required": ["regex_find", "regex_replace"], "additionalProperties": False,
            "properties": {"regex_find": {"type": "string"}, "regex_replace": {"type": "string"}}}}}}
    user = "\n".join(f"{i}. {r['signature']}\n   before: {r['_before'].strip()}\n   after: {r['_after'].strip()}"
                     for i, r in enumerate(rules, 1))
    out, prompt_tokens, output_tokens = _call(REGEX_SYSTEM, user, schema, think=False)
    answer = json.loads(out) if isinstance(out, str) else out
    for r, rx in zip(rules, answer["regexes"]):
        if _regex_ok(rx.get("regex_find"), rx.get("regex_replace"), r["_before"], r["_after"]):
            r["regex_find"], r["regex_replace"] = rx["regex_find"], rx["regex_replace"]
    return prompt_tokens, output_tokens


def _regex_ok(find, repl, before, after) -> bool:
    if not find or not isinstance(repl, str):
        return False
    try:
        return re.sub(find, repl, before) == after
    except (re.error, IndexError):
        return False


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
        "max_tokens": MAX_OUTPUT_TOKENS if think else 512, **SAMPLING,
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
