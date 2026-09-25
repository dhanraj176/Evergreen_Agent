"""P2: rule matcher. Exact signature first, then the local Liquid model (LFM2.5-1.2B on llama-server).

match_rule takes a usable rule with exactly the failure's normalized signature when there is one
(instant, deterministic). Otherwise it asks the 1.2B model at LLAMA_SERVER_URL which usable rule is
the same error. The grammar allows one `<signature> => <rule ID>` line per usable rule, or "unknown",
so the only possible answers are the current rule IDs or unknown (the small model copies a signature
far more reliably than it recalls a bare ID). Deterministic checks can veto the model's pick: the
rule's exception type must equal the failure's, and the rule's API name must appear in the failure's
signature or line. If llama-server is unreachable, the answer is None.

Every call appends a record to `matches`; `last_match` is the latest:
  {"signature", "path", "rule_id", "answer", "rejected", "fallback", "latency_s"}
  path: exact | liquid | fallback (no exact match, llama-server unreachable) | no_rules (no call made)
"""
import json
import os
import re
import time

import requests

from evergreen.schema import Failure, Rule

TIMEOUT_S = 10
RETRY_AFTER_S = 30          # after a refused connection, skip llama-server for this long
PROMPT = ("You match a new Python error to a list of known errors. Copy the known error that is the same "
          "error as the new one (same exception and same attribute, function or argument). "
          "If none is the same error, reply unknown.")

last_match: dict = {}
matches: list[dict] = []
_http = requests.Session()
_down_until = 0.0


def match_rule(failure: Failure, rules: list[Rule]) -> Rule | None:
    global _down_until
    t0 = time.perf_counter()
    usable = [r for r in rules if r.status in ("verified", "trusted")]
    if not usable:
        return _record(failure, t0, "no_rules")
    exact = next((r for r in usable if r.signature == failure.signature and not rejected(r, failure)), None)
    if exact:
        return _record(failure, t0, "exact", exact)
    if time.monotonic() < _down_until:
        return _record(failure, t0, "fallback")
    try:
        answer = _ask(failure, usable)
    except (requests.RequestException, ValueError, KeyError) as e:
        if isinstance(e, requests.ConnectionError):
            _down_until = time.monotonic() + RETRY_AFTER_S
        return _record(failure, t0, "fallback")
    picked = next((r for r in usable if r.rule_id == _answer_id(answer)), None)
    veto = rejected(picked, failure) if picked else None
    return _record(failure, t0, "liquid", None if veto else picked, answer, veto)


def rejected(rule: Rule, failure: Failure) -> str | None:
    """The deterministic double-check: why `rule` can't be the fix for `failure`, or None."""
    if rule.signature.split(":")[0].strip() != failure.exc_type:
        return "exc_type"
    name = api_name(rule.signature)
    if name and name not in failure.signature and name not in (failure.line or ""):
        return "api_name"
    return None


def api_name(signature: str) -> str | None:
    """"AttributeError: DataFrame.append" -> "append"; "TypeError: to_csv(line_terminator)" -> "line_terminator"."""
    names = re.findall(r"[A-Za-z_]\w*", signature.split(":", 1)[-1])
    return names[-1] if names else None


def _server() -> str:
    url = (os.environ.get("LLAMA_SERVER_URL") or "http://127.0.0.1:8080").rstrip("/")
    # On Windows "localhost" tries IPv6 first and costs ~2 s per request; llama-server listens on IPv4.
    return url.replace("://localhost", "://127.0.0.1")


def _ask(failure: Failure, usable: list[Rule]) -> str:
    rows = "\n".join(f"{r.signature} => {r.rule_id}" for r in usable)
    line = f"Line: {failure.line}\n" if failure.line else ""
    prompt = (f"{PROMPT}\n\nNew error: {failure.signature}\n{line}\nKnown errors:\n{rows}\n\n"
              f"Known error that is the same as `{failure.signature}`:")
    choices = " | ".join(json.dumps(f"{r.signature} => {r.rule_id}") for r in usable)
    r = _http.post(f"{_server()}/completion", timeout=TIMEOUT_S, json={
        "prompt": prompt, "grammar": f'root ::= " "? ({choices} | "unknown")',
        "n_predict": 96, "temperature": 0, "cache_prompt": True})
    r.raise_for_status()
    return r.json()["content"].strip()


def _answer_id(answer: str) -> str | None:
    m = re.search(r"=> (\S+)$", answer)
    return m.group(1) if m else None


def _record(failure, t0, path, rule=None, answer=None, veto=None):
    global last_match
    last_match = {"signature": failure.signature, "path": path, "rule_id": rule.rule_id if rule else None,
                  "answer": answer, "rejected": veto, "fallback": path == "fallback",
                  "latency_s": round(time.perf_counter() - t0, 4)}
    matches.append(last_match)
    return rule
