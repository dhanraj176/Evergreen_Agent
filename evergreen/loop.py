"""P1: the ratchet loop (brief sections 7, 8, 10, 12.1, 12.2, 23.9).

run() fixes the target repo one src file at a time, in the 13.3 order, on branch evergreen/pandas-2:
triage -> match a rule (exact, then Liquid) or look up evidence (Nimble) -> patch (Liquid 8B) -> guards
-> full suite + golden ratchet -> commit, or roll back and retry with the failure as feedback
(3 attempts), else "needs a human". At the end it writes the AGENTS.md block, commits it, pushes the
branch and opens the PR. Every attempt, match, lookup, suite run and rule event is logged through
memory.py; display.py shows the run live; results.py writes runs/<run_id>/summary.json.
"""
import ast
import difflib
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from evergreen import evidence, liquid, results
from evergreen.agents_md import write_agents_md
from evergreen.display import Dashboard
from evergreen.gitops import BRANCH, commit_file, create_branch, git, open_pr, pr_for_branch, push, restore, snapshot
from evergreen.golden import golden_broken, golden_ok
from evergreen.guards import patch_ok, tests_hash
from evergreen.instant import apply_instant
from evergreen.memory import flush, log, log_patch, log_tests, save_rule_event, trusted
from evergreen.patcher import patch
from evergreen.schema import Failure, PatchResult, Rule, TestRun
from evergreen.testrun import run_tests, venv_python

ORDER = ["clean.py", "metrics.py", "report.py", "export.py", "summary.py", "index_utils.py"]
MAX_ATTEMPTS = 3          # per round
MAX_ROUNDS = 4            # accepted patches per file (instant-first adds one) before it counts as stuck
DEMOTE_AFTER = 2          # failures in a row
MAX_FEEDBACK = 8          # failure lines fed back on a retry
FIXER = {"instant": "liquid_quick", "fast": "liquid_fast", "think": "liquid_think"}   # attempts.fixer, as the dashboard names it


@dataclass
class State:
    repo: Path
    venv: str
    run_id: str
    use_rules: bool
    dash: Dashboard
    proven_on: str
    baseline_hash: str
    tests: TestRun
    golden: dict[str, str]
    start_passing: int
    start_total: int
    branch: str = BRANCH
    started: float = field(default_factory=time.time)
    seconds: float = 0.0
    rules: list[Rule] = field(default_factory=list)
    fails_in_a_row: dict[str, int] = field(default_factory=dict)
    attempt_no: int = 0
    history_tokens: int = 0       # prompt + output of every earlier attempt (section 10)
    suite: int = 0                # full test-suite runs so far (test_results.suite)
    needs_human: list[str] = field(default_factory=list)
    files: list[dict] = field(default_factory=list)
    timeline: list[dict] = field(default_factory=list)
    pr_url: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def mode(self) -> str:
        return "rules_on" if self.use_rules else "rules_off"

    @property
    def total(self) -> int:
        return _total(self.tests)


def run(repo: str, venv: str, run_id: str, use_rules: bool = True, files: list[str] | None = None,
        pr: bool = True) -> int:
    repo = Path(repo).resolve()
    if git(repo, "status", "--porcelain"):
        print(f"{repo} has uncommitted changes; restore them (or run reset.py) before a run.")
        return 1
    create_branch(repo, BRANCH)
    names = files or ORDER
    with Dashboard(run_id, repo=repo.name, mode="rules on" if use_rules else "rules off", files=names) as dash:
        dash.log(f"branch {BRANCH} in {repo.name}; running the test suite")
        tests = run_tests(repo, venv)
        st = State(repo, venv, run_id, use_rules, dash, _library_version(venv), tests_hash(repo),
                   tests, golden_ok(repo, venv), len(tests.passing), _total(tests))
        _log_suite(st, None)
        _log_tests(st, tests, None, None, kept=True)
        dash.update(tests_passing=len(tests.passing), tests_total=_total(tests))
        dash.log(f"baseline: {len(tests.passing)}/{_total(tests)} passing on {st.proven_on}")
        for src in _file_order(st, files):
            fix_file(st, src)
        finish(st, pr)
    results.print_table(st, dash.console)
    return 0 if not st.tests.failing else 1


def finish(st: State, pr: bool) -> None:
    """AGENTS.md block -> commit -> push -> PR -> summary.json -> final panel."""
    dash = st.dash
    write_agents_md(st.repo, st.rules, [], st.needs_human)
    ids = ", ".join(r.rule_id for r in st.rules if r.status in ("verified", "trusted"))
    try:
        commit_file(st.repo, "AGENTS.md", "evergreen: update AGENTS.md" + (f" ({ids})" if ids else ""))
        dash.log("AGENTS.md block written and committed", style="green")
    except RuntimeError as e:
        st.notes.append(f"AGENTS.md not committed: {e}")
    st.seconds = time.time() - st.started
    if pr:
        try:
            push(st.repo, BRANCH)
            dash.log(f"pushed {BRANCH}")
            st.pr_url = open_pr(st.repo, f"Upgrade to {st.proven_on} (Evergreen)", results.pr_body(st))
            dash.log(f"PR opened: {st.pr_url}", style="bold green")
        except RuntimeError as e:
            st.pr_url = pr_for_branch(st.repo, BRANCH)
            if not st.pr_url:
                st.notes.append(f"{str(e)[:300]} (run reset.py, then try again)")
    flush()
    path = results.write_summary(st)
    dash.log(f"summary: {path}")
    dash.finish(st.pr_url, "\n".join(st.notes))


def fix_file(st: State, src: str) -> None:
    name = Path(src).name
    entry = {"file": name, "attempts": [], "web": 0, "used": set(), "learned": [], "commits": 0}
    st.files.append(entry)
    t0 = time.time()
    for _ in range(MAX_ROUNDS):
        failures = _failures_in(st.tests, src)
        if not failures or not _fix_round(st, src, failures, entry):
            break
    left = _failures_in(st.tests, src)
    entry["seconds"] = time.time() - t0
    entry["result"] = "needs a human" if left else "fixed" if entry["attempts"] else "already passing"
    notes = ([f"{', '.join(sorted(entry['used']))} applied"] if entry["used"] else []) + \
            ([f"{', '.join(entry['learned'])} learned"] if entry["learned"] else [])
    if left:
        sigs = ", ".join(sorted({f.signature for f in left}))
        st.needs_human.append(f"`{name}`: {len(left)} failing test(s) after "
                              f"{len(entry['attempts'])} attempt(s) ({sigs})")
        notes = [sigs]
    st.dash.file(name, "needs a human" if left else "fixed", note="; ".join(notes) or entry["result"])


def _fix_round(st: State, src: str, failures: list[Failure], entry: dict) -> bool:
    """One accepted patch for the file's current failures, or False after MAX_ATTEMPTS LLM attempts.

    Instant-first (12.3): when matched rules have validated regexes, the first attempt applies only
    those (0 tokens, no LLM) and is ratchet-checked on its own; if it is kept, the next round sends only
    the remaining failures to Liquid. If it is rolled back, those rules fall back to LLM hints.
    """
    name, path, dash = Path(src).name, st.repo / src, st.dash
    original = snapshot(path)
    feedback, hints = None, None
    looked_up: dict[str, object] = {}          # signature -> Evidence | None, once per file
    no_instant: set[str] = set()               # rules whose instant fix failed here: LLM hint only
    sigs = sorted({f.signature for f in failures})
    dash.log(f"{name}: {len(failures)} failing test(s): {', '.join(sigs)}", style="bold")
    attempt = llm_tries = 0
    while llm_tries < MAX_ATTEMPTS:
        attempt += 1
        st.attempt_no += 1
        t0 = time.time()
        lookups = 0
        if hints is None or any(isinstance(h, Rule) and h.status == "demoted" for h in hints):
            hints, lookups = _hints(st, name, failures, looked_up)
        base, instant, instant_lines = _instant(original, failures, hints, no_instant)
        sent = sorted(instant) if instant else list(range(len(failures)))
        s_fail, s_hint = [failures[i] for i in sent], [hints[i] for i in sent]
        used = list({r.rule_id: r for r in s_hint if isinstance(r, Rule)}.values())
        entry["used"].update(r.rule_id for r in used)
        entry["web"] += lookups
        think = not instant and (feedback is not None or not all(isinstance(h, Rule) for h in s_hint))
        mode = "instant" if instant else "think" if think else "fast"
        llm_tries += 0 if instant else 1
        dash.file(name, "working", mode=mode, attempt=attempt)
        row = {"run_id": st.run_id, "mode": st.mode, "attempt": st.attempt_no, "file": name,
               "file_attempt": attempt, "signatures": sorted({f.signature for f in s_fail}),
               "rule_ids": [r.rule_id for r in used], "used_web": int(lookups > 0), "web_lookups": lookups,
               "think": int(think), "llm": int(not instant), "instant": int(bool(instant)),
               "instant_lines": instant_lines, "fixer": FIXER[mode], "accepted": 0,
               "rolled_back": 0, "rejected_by_guard": 0, "prompt_tokens": 0, "naive_prompt_tokens": 0,
               "output_tokens": 0}

        dash.log(f"{name}: attempt {attempt}, "
                 + ("instant rules only, no LLM call" if instant else "thinking" if think else "fast")
                 + (f", rules {', '.join(r.rule_id for r in used)}" if used else ""))
        for rid in sorted({r.rule_id for r in instant.values()}):
            dash.instant(rid, name, sorted({failures[i].line_no for i, r in instant.items() if r.rule_id == rid}))
        t_patch = time.time()
        try:
            if instant:
                res = PatchResult(base, f"instant rules {', '.join(r.rule_id for r in used)}", None, 0, 0)
            else:
                res = patch(src, original, s_fail, s_hint, feedback, think=think)
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            _end_attempt(st, entry, row, t0, "error", reason=f"patch call failed: {e}")
            dash.log(f"{name}: patch call failed: {e}", style="red")
            continue
        naive = st.history_tokens + res.prompt_tokens
        st.history_tokens += res.prompt_tokens + res.output_tokens
        row.update(prompt_tokens=res.prompt_tokens, output_tokens=res.output_tokens,
                   naive_prompt_tokens=naive, patch_s=round(time.time() - t_patch, 2),
                   explanation=res.explanation[:300], diff=_changes(original, res.new_source)[:1500])
        if not instant:
            dash.update(prompt_tokens=res.prompt_tokens, naive_prompt_tokens=naive)

        ok, why = _guard(original, res)
        if not ok:
            if instant:                            # an instant-only patch the guard refused
                no_instant.update(r.rule_id for r in used)
            dash.bump("guard_rejections")
            dash.log(f"{name}: guard rejected the patch: {why}", style="yellow")
            feedback = _feedback(f"The patch was rejected before testing: {why}", original, res.new_source)
            _end_attempt(st, entry, row, t0, "guard", rejected_by_guard=1, reason=why)
            continue

        accepted, after, gold = False, None, {}
        try:
            restore(path, res.new_source)
            after = run_tests(st.repo, st.venv)
            gold = golden_ok(st.repo, st.venv)
            reason = _ratchet(st, after, gold, src)
            accepted = reason is None
        finally:
            if not accepted:
                restore(path, original)            # roll back
        _rule_outcomes(st, s_fail, s_hint, after, accepted)
        _log_tests(st, after, name, st.attempt_no, kept=accepted)
        row["after_passing"] = len(after.passing)

        if accepted:
            learned = _learn(st, res, s_fail, after)
            ids = [r.rule_id for r in used] + [r.rule_id for r in learned]
            sha = commit_file(st.repo, src, f"ratchet: fix {name}" + (f" ({', '.join(ids)})" if ids else "")
                              + (" [instant, 0 tokens]" if instant else ""))
            log_patch(st.run_id, st.attempt_no, name, sha, {"think": "thinking"}.get(mode, mode), ids,
                      git(st.repo, "show", "--format=", "--no-color", sha))
            st.tests, st.golden = after, gold
            entry["learned"] += [r.rule_id for r in learned]
            entry["commits"] += 1
            if instant:
                dash.bump("instant_fixes", instant_lines)
            _log_suite(st, name)
            dash.update(tests_passing=len(after.passing), tests_total=_total(after))
            dash.log(f"{name}: accepted, {len(after.passing)}/{_total(after)} passing, committed",
                     style="green")
            _end_attempt(st, entry, row, t0, "accepted", accepted=1)
            return True

        dash.bump("rollbacks")
        dash.log(f"{name}: rolled back: {reason.splitlines()[0]}", style="yellow")
        if instant:
            no_instant.update(r.rule_id for r in used)
        feedback = _feedback(f"The patch was rolled back: {reason}", original, res.new_source,
                             _failures_in(after, src))
        _end_attempt(st, entry, row, t0, "rolled back", rolled_back=1, reason=reason)
    return False


def _hints(st: State, name: str, failures: list[Failure], looked_up: dict) -> tuple[list, int]:
    """One hint per failure: the matched rule, else Nimble evidence (or None). Returns (hints, lookups)."""
    usable = [r for r in st.rules if r.status in ("verified", "trusted")]
    matched: dict[str, Rule | None] = {}
    hints, lookups = [], 0
    for f in failures:
        if st.use_rules and f.signature not in matched:
            matched[f.signature] = rule = liquid.match_rule(f, usable)
            log("matches", {"run_id": st.run_id, "file": name, "test_id": f.test_id, **liquid.last_match})
            if rule:
                st.dash.applied(rule.rule_id, f.signature, f"{liquid.last_match['path']} match, "
                                                            f"{liquid.last_match['latency_s'] * 1000:.0f} ms")
        if matched.get(f.signature):
            hints.append(matched[f.signature])
            continue
        if f.signature not in looked_up:
            ev = looked_up[f.signature] = evidence.get_evidence(f)
            lookups += 1
            log("evidence", {"run_id": st.run_id, "file": name, "signature": f.signature, "cached": 0,
                             **evidence.last_lookup})
            if ev:
                st.dash.nimble(ev.url, f"{ev.latency_s:.1f}s")
            else:
                st.dash.bump("web_lookups")
                st.dash.log(f"Nimble: no evidence for {f.signature} "
                            f"({evidence.last_lookup.get('error')})", style="yellow")
        hints.append(looked_up[f.signature])
    return hints, lookups


def _instant(source: str, failures, hints, off: set[str]) -> tuple[str, dict[int, Rule], int]:
    """Apply each matched rule's regex to its failure's line (12.3).
    Returns (new source, {failure index: rule} for the failures it covers, lines changed)."""
    covered, done = {}, {}
    for i, (f, h) in enumerate(zip(failures, hints)):
        if not isinstance(h, Rule) or h.rule_id in off or not f.line_no:
            continue
        if f.line_no in done:                  # the line was already rewritten for an earlier failure
            if done[f.line_no] is h:
                covered[i] = h
            continue
        new = apply_instant(source, f, h)
        if new is not None:
            source, covered[i], done[f.line_no] = new, h, h
    return source, covered, len(done)


def _guard(original: str, res) -> tuple[bool, str]:
    if res.explanation.startswith("invalid edits:"):
        return False, res.explanation
    if res.new_source == original:
        return False, "the patch changed nothing"
    ok, why = patch_ok(original, res.new_source)
    if why == "does not parse":                # say where, so the retry can fix it
        try:
            ast.parse(res.new_source)
        except SyntaxError as e:
            why = f"does not parse: {e.msg} at line {e.lineno}: {(e.text or '').strip()}"
    return ok, why


def _ratchet(st: State, after: TestRun, gold: dict, src: str) -> str | None:
    """None if the patch may be kept, else the reason. Partial progress is kept: a golden case that
    still errors is fine, but one that stops matching or gives wrong output is not."""
    suite = next((f for f in after.failing if f.test_id == "<suite>"), None)
    if suite:
        return f"The test run itself failed: {suite.message}"
    if tests_hash(st.repo) != st.baseline_hash:
        return "Files under tests/ or golden/ changed."
    broke = sorted(st.tests.passing - after.passing)
    if broke:
        lost = [f for f in after.failing if f.test_id in broke]
        return "These tests passed before and fail now:\n" + _describe(lost)
    if len(after.passing) <= len(st.tests.passing):
        return "No new test passes."
    broken = golden_broken(st.golden, gold)
    if broken:
        return ("Outputs no longer match pandas 1.5 (golden check) for: "
                + ", ".join(f"{c} ({'wrong output' if gold.get(c) == 'mismatch' else gold.get(c, 'missing')})"
                            for c in broken))
    return None


def _changes(old: str, new: str) -> str:
    """The changed lines, numbered as in `old`: what a rejected patch did, for feedback and the log."""
    a, b = old.splitlines(), new.splitlines()
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag == "equal":
            continue
        out += [f"- line {i1 + k + 1}: {a[i1 + k].strip()}" for k in range(i2 - i1)]
        out += [f"+ {line.strip()}" for line in b[j1:j2]]
    return "\n".join(out[:MAX_FEEDBACK * 2])


def _feedback(reason: str, old: str, new: str, still: list[Failure] | None = None) -> str:
    """What the next attempt is told: why the last patch failed, what it changed, what still fails."""
    parts = [reason.strip()]
    changes = _changes(old, new)
    if changes:
        parts.append("That patch changed:\n" + changes)
    if still:
        parts.append("Still failing in this file:\n" + _describe(still))
    return "\n".join(parts)


def _describe(failures: list[Failure]) -> str:
    lines = [f"- {f.test_id}: {f.signature} ({f.message})"
             + (f" at line {f.line_no}: {f.line}" if f.line_no else "") for f in failures[:MAX_FEEDBACK]]
    more = len(failures) - MAX_FEEDBACK
    return "\n".join(lines + ([f"- ... and {more} more"] if more > 0 else []))


def _rule_outcomes(st: State, failures, hints, after: TestRun | None, accepted: bool) -> None:
    """Section 8 bookkeeping for every rule used as a hint. A rule succeeds when the patch is kept and
    its tests pass, fails when its tests still fail; a patch rejected for other reasons says nothing."""
    if after is None:
        return
    for rule in {r.rule_id: r for r in hints if isinstance(r, Rule)}.values():
        tests = [f.test_id for f, h in zip(failures, hints) if h is rule]
        fixed = all(t in after.passing for t in tests)
        if fixed and not accepted:
            continue
        rule.applied += 1
        if fixed:
            rule.succeeded += 1
            st.fails_in_a_row[rule.rule_id] = 0
            rule.proof += [t for t in tests if t not in rule.proof]
            if rule.status == "verified" and trusted(rule):
                rule.status = "trusted"
            save_rule_event(st.run_id, rule, "succeeded")
            continue
        st.fails_in_a_row[rule.rule_id] = st.fails_in_a_row.get(rule.rule_id, 0) + 1
        save_rule_event(st.run_id, rule, "failed")
        if st.fails_in_a_row[rule.rule_id] >= DEMOTE_AFTER:
            rule.status = "demoted"
            save_rule_event(st.run_id, rule, "demoted")
            st.dash.log(f"{rule.rule_id} demoted after {DEMOTE_AFTER} failures", style="yellow")


def _learn(st: State, res, failures: list[Failure], after: TestRun) -> list[Rule]:
    """New rules from an accepted patch: one per signature not already covered, verified by its tests."""
    covered = {r.signature for r in st.rules if r.status in ("verified", "trusted")}
    learned = []
    for cand in res.new_rules:
        tests = [f.test_id for f in failures if f.signature == cand["signature"]]
        if cand["signature"] in covered or not tests or not all(t in after.passing for t in tests):
            continue
        url = cand.get("source_url")
        rule = Rule(rule_id=f"R{len(st.rules) + 1}", signature=cand["signature"], pattern=cand["pattern"],
                    replacement=cand["replacement"], regex_find=cand.get("regex_find"),
                    regex_replace=cand.get("regex_replace"),
                    source_url=url if url in evidence.seen_urls else None,   # else "unsourced"
                    proven_on=[st.proven_on], proof=tests)
        st.rules.append(rule)
        covered.add(rule.signature)
        learned.append(rule)
        save_rule_event(st.run_id, rule, "verified")
        st.dash.learned(rule.rule_id, rule.signature, rule.source_url)
    return learned


def _end_attempt(st: State, entry: dict, row: dict, t0: float, outcome: str, reason: str = "", **flags):
    row.update(flags, duration_s=round(time.time() - t0, 2), outcome=outcome, reason=reason[:500])
    log("attempts", row)
    entry["attempts"].append(dict(row))


def _log_suite(st: State, name: str | None) -> None:
    """test_runs holds the branch's state (baseline, then after each kept patch): the staircase."""
    row = {"run_id": st.run_id, "mode": st.mode, "file": name, "passing": len(st.tests.passing),
           "failing": len(st.tests.failing), "total": st.total}
    log("test_runs", row)
    st.timeline.append({"t": round(time.time() - st.started, 1), "file": name,
                        "passing": row["passing"], "total": row["total"]})


def _log_tests(st: State, tests: TestRun, name: str | None, attempt: int | None, kept: bool) -> None:
    st.suite += 1
    log_tests(st.run_id, tests, st.suite, attempt, name, kept)


def _failures_in(tests: TestRun, src: str) -> list[Failure]:
    return [f for f in tests.failing if f.src_file == src]


def _total(tests: TestRun) -> int:
    return len(tests.passing) + len(tests.failing)


def _file_order(st: State, only: list[str] | None) -> list[str]:
    if only:
        return [f"src/{name}" for name in only]
    rest = sorted({f.src_file for f in st.tests.failing if f.src_file}
                  - {f"src/{name}" for name in ORDER})
    return [f"src/{name}" for name in ORDER] + rest


def _library_version(venv) -> str:
    proc = subprocess.run([str(venv_python(Path(venv).resolve())), "-c",
                           "import pandas; print(pandas.__version__)"], capture_output=True, text=True)
    return f"pandas {proc.stdout.strip() or 'unknown'}"
