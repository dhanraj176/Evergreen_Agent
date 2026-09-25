"""What a finished run leaves behind: the terminal summary table, runs/<run_id>/summary.json for a
results page, and the pull request body (brief 23.9)."""
import json
from dataclasses import asdict

from rich.console import Console
from rich.table import Table

from evergreen import memory
from evergreen.memory import confidence

OUTCOME = {"accepted": "ok", "rolled back": "rollback", "guard": "guard", "error": "error"}


def _mode(a: dict) -> str:
    return "thinking" if a["think"] else "fast" if a.get("llm", 1) else "instant"


def _modes(attempts: list[dict]) -> str:
    return ", ".join(_mode(a) for a in attempts) or "-"


def print_table(st, console: Console) -> None:
    table = Table(title=f"Evergreen run {st.run_id} ({st.mode})")
    for col, just in [("file", "left"), ("result", "left"), ("attempts", "left"), ("s", "right"),
                      ("web", "right"), ("used", "left"), ("learned", "left")]:
        table.add_column(col, justify=just, no_wrap=col in ("file", "attempts", "s", "web"))
    for e in st.files:
        tries = "\n".join(f"{_mode(a)} {OUTCOME.get(a['outcome'], a['outcome'])} {a['duration_s']:.0f}s"
                          for a in e["attempts"]) or "-"
        table.add_row(e["file"], e["result"], tries, f"{e['seconds']:.0f}", str(e["web"]),
                      ", ".join(sorted(e["used"])) or "-", ", ".join(e["learned"]) or "-")
    console.print(table)
    console.print(totals_line(st))
    for item in st.needs_human:
        console.print(f"needs a human: {item}")


def totals_line(st) -> str:
    attempts = [a for e in st.files for a in e["attempts"]]
    return (f"tests {st.start_passing}/{st.start_total} -> {len(st.tests.passing)}/{st.total} passing, "
            f"{len(attempts)} attempts ({sum(a['think'] for a in attempts)} thinking), "
            f"{sum(e['web'] for e in st.files)} web lookups, {len(st.rules)} rules, "
            f"{sum(e['commits'] for e in st.files)} fix commits, {st.seconds:.0f}s total")


def _rule(r) -> dict:
    return {**asdict(r), "confidence": round(confidence(r), 2)}


def write_summary(st) -> str:
    """runs/<run_id>/summary.json: everything the results page needs, in one file."""
    attempts = [a for e in st.files for a in e["attempts"]]
    data = {
        "run_id": st.run_id, "mode": st.mode, "repo": st.repo.name, "branch": st.branch,
        "library": st.proven_on, "started_at": int(st.started), "duration_s": round(st.seconds, 1),
        "pr_url": st.pr_url, "notes": st.notes,
        "tests": {"before": {"passing": st.start_passing, "total": st.start_total},
                  "after": {"passing": len(st.tests.passing), "total": st.total}},
        "counters": dict(st.dash.counts),
        "files": [{"file": e["file"], "result": e["result"], "seconds": round(e["seconds"], 1),
                   "web_lookups": e["web"], "rules_used": sorted(e["used"]), "rules_learned": e["learned"],
                   "commits": e["commits"],
                   "attempts": [{"n": a["file_attempt"], "mode": _mode(a), "outcome": a["outcome"],
                                 "seconds": a["duration_s"], "web_lookups": a["web_lookups"],
                                 "rule_ids": a["rule_ids"], "reason": a["reason"]} for a in e["attempts"]]}
                  for e in st.files],
        "tokens": [{"attempt": a["attempt"], "file": a["file"], "mode": _mode(a),
                    "prompt_tokens": a["prompt_tokens"], "naive_prompt_tokens": a["naive_prompt_tokens"],
                    "output_tokens": a["output_tokens"]} for a in attempts],
        "tests_over_time": st.timeline,
        "rules": [_rule(r) for r in st.rules],
        "needs_human": st.needs_human,
    }
    path = memory.RUNS / st.run_id / "summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return str(path)


def _cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def pr_body(st) -> str:
    c = st.dash.counts
    attempts = [a for e in st.files for a in e["attempts"]]
    lines = [
        f"Evergreen upgraded this repo to **{st.proven_on}** one file at a time. A fix was kept only if every "
        "previously passing test still passed, at least one more test passed, the tests were untouched, and "
        "the outputs still matched pandas 1.5 (golden check). Each kept fix is its own commit.",
        "",
        f"**Tests:** {st.start_passing}/{st.start_total} → {len(st.tests.passing)}/{st.total} passing · "
        f"**Attempts:** {len(attempts)} ({sum(_mode(a) == 'instant' for a in attempts)} instant, "
        f"{sum(_mode(a) == 'fast' for a in attempts)} fast, {sum(a['think'] for a in attempts)} thinking) · "
        f"**Rules learned:** {c['rules_learned']} · **Rules applied:** {c['rules_applied']} · "
        f"**Instant fixes (0 tokens):** {c['instant_fixes']} · **Web lookups:** {c['web_lookups']} · "
        f"**Rollbacks:** {c['rollbacks']} · **Guard rejections:** {c['guard_rejections']} · "
        f"**Human interventions:** {c['human_interventions']} · **Time:** {st.seconds:.0f}s",
        "",
        "### Fixes",
        "",
        "| File | Rules | Attempts | Fast / thinking | Seconds | Result |",
        "|---|---|---|---|---|---|",
    ]
    for e in st.files:
        rules = [f"{r} (reused)" for r in sorted(e["used"])] + [f"{r} (learned)" for r in e["learned"]]
        lines.append(f"| `{e['file']}` | {', '.join(rules) or '-'} | {len(e['attempts'])} | "
                     f"{_modes(e['attempts'])} | {e['seconds']:.0f} | {e['result']} |")
    lines += ["", "### Rules learned", ""]
    if st.rules:
        lines += ["| Rule | Signature | Fix | Confidence | Status | Source |", "|---|---|---|---|---|---|"]
        for r in st.rules:
            source = f"[{r.source_url}]({r.source_url})" if r.source_url else "unsourced"
            lines.append(f"| {r.rule_id} | `{_cell(r.signature)}` | `{_cell(r.pattern)}` → `{_cell(r.replacement)}` | "
                         f"{confidence(r):.2f} | {r.status} | {source} |")
    else:
        lines.append("None.")
    rejected = [(e["file"], a) for e in st.files for a in e["attempts"] if a["outcome"] != "accepted"]
    lines += ["", "### Guard rejections and rollbacks", ""]
    lines += [f"- `{f}` attempt {a['file_attempt']} ({_mode(a)}): "
              f"{'rejected by the guard' if a['outcome'] == 'guard' else a['outcome']}: "
              f"{_cell(a['reason'].splitlines()[0] if a['reason'] else '')}" for f, a in rejected] or ["None."]
    lines += ["", "### Needs a human", ""]
    lines += [f"- {item}" for item in st.needs_human] or ["None."]
    lines += ["", "### AGENTS.md", "",
              "`AGENTS.md` was updated: Evergreen's block (between `<!-- evergreen:start -->` and "
              "`<!-- evergreen:end -->`) lists every verified rule with its confidence, the version it was "
              "proven on and its source, so the next coding session starts with them. Text outside the block "
              "is unchanged.",
              "", f"_Opened by Evergreen (run `{st.run_id}`, {st.mode.replace('_', ' ')}). "
                  "Evergreen never merges: review each commit._"]
    return "\n".join(lines) + "\n"
