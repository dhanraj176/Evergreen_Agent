"""P3: the live screen (brief 15.4), laid out for a 1080p screen recording.

    with Dashboard("r1", repo="sales-report", mode="rules on", files=["clean.py", ...]) as dash:
        dash.update(tests_passing=4, tests_total=20)    # set counters
        dash.file("clean.py", "working", mode="think", attempt=1)
        dash.nimble("https://pandas.pydata.org/...")    # +1 web lookup, URL in the log
        dash.learned("R1", "AttributeError: DataFrame.append", source_url)
        dash.applied("R1", "AttributeError: DataFrame.append", "exact, 0 ms")
        dash.file("clean.py", "fixed")
        dash.finish(pr_url="https://github.com/...")

On a terminal it redraws in place at a fixed height (no flicker, file timers tick). Otherwise
(piped, recorded to a file) log lines print as they happen and the final state prints at the end.
"""
import time
from collections import deque

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

COUNTERS = ("tests_passing", "tests_total", "rules_learned", "rules_applied", "instant_fixes",
            "web_lookups", "rollbacks", "guard_rejections", "human_interventions",
            "prompt_tokens", "naive_prompt_tokens")
STATUS = {"waiting": ("·", "dim"), "working": ("▶", "bold yellow"), "fixed": ("✓", "bold green"),
          "needs a human": ("✗", "bold red")}
GLYPHS = {  # 3x5 block digits for the big tests counter
    "0": ["███", "█ █", "█ █", "█ █", "███"], "1": ["██ ", " █ ", " █ ", " █ ", "███"],
    "2": ["███", "  █", "███", "█  ", "███"], "3": ["███", "  █", "███", "  █", "███"],
    "4": ["█ █", "█ █", "███", "  █", "  █"], "5": ["███", "█  ", "███", "  █", "███"],
    "6": ["███", "█  ", "███", "█ █", "███"], "7": ["███", "  █", "  █", "  █", "  █"],
    "8": ["███", "█ █", "███", "█ █", "███"], "9": ["███", "█ █", "███", "  █", "███"],
    "/": ["  █", "  █", " █ ", "█  ", "█  "]}


def _never_fail_encoding(stream):
    """Never die on a stream that can't encode a URL or log text (e.g. cp1252 when piped)."""
    enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
    if enc != "utf8" and hasattr(stream, "reconfigure"):
        stream.reconfigure(errors="replace")


def _clock(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


class Dashboard:
    def __init__(self, run_id: str, repo: str = "", mode: str = "", files=(), log_lines: int = 14,
                 console: Console | None = None):
        self.console = console or Console()
        _never_fail_encoding(self.console.file)
        self.unicode = (self.console.encoding or "").lower().startswith("utf")
        self.dot = " · " if self.unicode else " | "
        self.run_id, self.repo, self.mode = run_id, repo, mode
        self.counts = dict.fromkeys(COUNTERS, 0)
        self.files = {name: {"status": "waiting", "mode": None, "attempt": None, "started": None,
                             "seconds": None, "note": ""} for name in files}
        self.lines = deque(maxlen=log_lines)
        self.log_lines = log_lines
        self.started = time.time()
        self.final = None
        self.memory = ""               # e.g. "memory: 6 rules from live1", shown in the header
        self._live = None

    # ---- API -------------------------------------------------------------------------------
    def __enter__(self):
        if self.console.is_terminal:
            self._live = Live(self, console=self.console, refresh_per_second=4, transient=False)
            self._live.start()
        return self

    def __exit__(self, *exc):
        if self._live:
            self._live.refresh()
            self._live.stop()
        else:
            self.console.print(self._header(), self._files())
            if self.final:
                self.console.print(self.final)
        return False

    def update(self, **counters) -> None:
        unknown = set(counters) - set(COUNTERS)
        if unknown:
            raise ValueError(f"unknown counters: {sorted(unknown)}")
        self.counts.update(counters)

    def bump(self, name: str, n: int = 1) -> None:
        self.update(**{name: self.counts[name] + n})

    def log(self, msg, style: str = "") -> None:
        body = msg if isinstance(msg, Text) else Text(str(msg), style=style)
        line = Text(f"{_clock(time.time() - self.started):>5}  ", style="dim") + body
        self.lines.append(line)
        if not self._live:
            self.console.print(line, soft_wrap=True)

    def nimble(self, url: str, note: str | None = None) -> None:
        self.counts["web_lookups"] += 1
        self.log(self._tag("NIMBLE", "black on cyan") + Text(f" {url}", style="cyan")
                 + Text(f"  {note}" if note else "", style="dim"))

    def learned(self, rule_id: str, signature: str, source: str | None = None) -> None:
        self.counts["rules_learned"] += 1
        self.log(self._tag(f"LEARNED {rule_id}", "bold black on green") + Text(f" {signature}", style="bold green")
                 + Text(f"  {source or 'unsourced'}", style="dim"))

    def applied(self, rule_id: str, signature: str, how: str = "") -> None:
        self.counts["rules_applied"] += 1
        self.log(self._tag(f"RULE {rule_id}", "bold black on magenta") + Text(f" {signature}", style="magenta")
                 + Text(f"  {how}" if how else "", style="dim"))

    def instant(self, rule_id: str, file: str, line_nos=()) -> None:
        lines = ", ".join(str(n) for n in line_nos)
        self.log(self._tag(f"INSTANT {rule_id}", "bold black on bright_green")
                 + Text(f" {file}" + (f" line {lines}" if lines else "") + ", 0 tokens", style="bright_green"))

    def file(self, name: str, status: str, mode: str | None = None, attempt: int | None = None,
             note: str = "") -> None:
        f = self.files.setdefault(name, {"status": "waiting", "mode": None, "attempt": None,
                                         "started": None, "seconds": None, "note": ""})
        f["status"] = status
        f["mode"] = mode or f["mode"]
        f["attempt"] = attempt or f["attempt"]
        f["note"] = note or f["note"]
        if status == "working" and f["started"] is None:
            f["started"] = time.time()
        if status in ("fixed", "needs a human") and f["started"] is not None:
            f["seconds"] = time.time() - f["started"]
        if not self._live and status != "working":
            self.log(f"{name}: {status}" + (f" ({f['note']})" if f["note"] else ""))

    def finish(self, pr_url: str | None = None, note: str = "", seconds: float | None = None) -> None:
        c = self.counts
        green = c["tests_total"] and c["tests_passing"] == c["tests_total"]
        body = Text(justify="center")
        body.append(f"{c['tests_passing']}/{c['tests_total']} tests passing", style="bold green" if green else "bold red")
        if seconds is not None:
            body.append(f"   in {_clock(seconds)} ({seconds:.0f} s)", style="bold")
        body.append("   " + self.dot.join([f"{c['rules_learned']} rules learned", f"{c['rules_applied']} applied",
                                           f"{c['web_lookups']} web lookups",
                                           f"{c['human_interventions']} human interventions"]) + "\n")
        if pr_url:
            body.append("Pull request  ", style="bold")
            body.append(pr_url, style="bold underline cyan")
        if note:
            body.append(("\n" if pr_url else "") + note, style="yellow")
        self.final = Panel(body, title="done" if pr_url else "finished", border_style="green" if pr_url else "yellow",
                           padding=(1, 2))

    # ---- rendering -------------------------------------------------------------------------
    def __rich__(self):
        parts = [self._header(), self._files(), self._log()]
        return Group(*parts, self.final) if self.final else Group(*parts)

    def _tag(self, label: str, style: str) -> Text:
        return Text(f" {label} ", style=style)

    def _big(self, text: str) -> Text:
        rows = [" ".join(GLYPHS[ch][r] for ch in text) for r in range(5)]
        if not self.unicode:
            rows = [r.replace("█", "#") for r in rows]
        return Text("\n".join(rows))

    def _header(self) -> Panel:
        c = self.counts
        done = c["tests_total"] and c["tests_passing"] == c["tests_total"]
        colour = "green" if done else "red"
        big = self._big(f"{c['tests_passing']}/{c['tests_total']}")
        big.stylize(f"bold {colour}")
        tests = Group(big, Text("tests passing", style=f"bold {colour}"))

        grid = Table.grid(padding=(0, 2))
        grid.add_column(justify="right", style="bold")
        grid.add_column()
        grid.add_column(justify="right", style="bold")
        grid.add_column()
        cells = [(c["rules_learned"], "rules learned", "green"), (c["rules_applied"], "rules applied", "magenta"),
                 (c["instant_fixes"], "instant fixes (0 tokens)", ""), (c["web_lookups"], "web lookups", "cyan"),
                 (c["rollbacks"], "rollbacks", "yellow"), (c["guard_rejections"], "patches rejected by guard", "yellow"),
                 (c["human_interventions"], "human interventions", "bold"),
                 (f"{c['prompt_tokens']:,}", f"prompt tokens now (naive {c['naive_prompt_tokens']:,})", "")]
        for (v1, l1, s1), (v2, l2, s2) in zip(cells[0::2], cells[1::2]):
            grid.add_row(Text(str(v1), style=s1), l1, Text(str(v2), style=s2), l2)

        row = Table.grid(padding=(0, 4))
        row.add_column()
        row.add_column(vertical="middle")
        row.add_row(tests, grid)
        title = Text.assemble(("EVERGREEN", "bold green"),
                              "  " + self.dot.join([self.repo, f"run {self.run_id}", self.mode]
                                                   + ([self.memory] if self.memory else []) + [""]),
                              (_clock(time.time() - self.started), "bold"))
        return Panel(row, title=title, title_align="left", border_style=colour, padding=(0, 2))

    def _files(self) -> Panel:
        table = Table.grid(padding=(0, 3))
        for justify in ("left", "left", "left", "left", "right", "left"):
            table.add_column(justify=justify)
        for name, f in list(self.files.items()):     # the refresh thread reads while the loop writes
            icon, style = STATUS.get(f["status"], ("?", ""))
            if not self.unicode:
                icon = {"✓": "+", "▶": ">", "✗": "x"}.get(icon, icon)
            seconds = f["seconds"] if f["seconds"] is not None else (
                time.time() - f["started"] if f["started"] else None)
            status = f["status"] + (f" (attempt {f['attempt']})" if f["status"] == "working" and f["attempt"] else "")
            table.add_row(Text(icon, style=style), Text(name, style="bold" if f["status"] == "working" else ""),
                          Text(status, style=style), Text({"think": "thinking", "fast": "fast", "instant": "instant"}.get(f["mode"], ""),
                                                          style="dim"),
                          Text(f"{seconds:.0f}s" if seconds is not None else ""), Text(f["note"], style="dim"))
        return Panel(table, title="files", title_align="left", border_style="dim", padding=(0, 2))

    def _log(self) -> Panel:
        lines = list(self.lines) + [Text("")] * (self.log_lines - len(self.lines))
        for line in lines:
            line.no_wrap, line.overflow = True, "ellipsis"
        return Panel(Group(*lines), title="log", title_align="left", border_style="dim", padding=(0, 2))


def demo(seconds: float = 5) -> None:
    files = ["clean.py", "metrics.py", "report.py"]
    steps = [
        ("update", {"tests_total": 12, "tests_passing": 3}),
        ("file", ("clean.py", "working", "think", 1)),
        ("nimble", "https://pandas.pydata.org/docs/whatsnew/v2.0.0.html"),
        ("update", {"prompt_tokens": 1026, "naive_prompt_tokens": 1026}),
        ("learned", ("R1", "AttributeError: DataFrame.append", "https://pandas.pydata.org/docs/whatsnew/v2.0.0.html")),
        ("learned", ("R2", "AttributeError: DataFrame.iteritems", None)),
        ("update", {"tests_passing": 6}),
        ("file", ("clean.py", "fixed", None, None, "R1, R2 learned")),
        ("file", ("metrics.py", "working", "think", 1)),
        ("bump", "guard_rejections"),
        ("log", "metrics.py: guard rejected the patch: added except Exception"),
        ("nimble", "https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.mean.html"),
        ("learned", ("R3", "TypeError: mean(numeric_only)", "https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.mean.html")),
        ("update", {"tests_passing": 9, "prompt_tokens": 980, "naive_prompt_tokens": 2951}),
        ("file", ("metrics.py", "fixed", None, None, "R3 learned")),
        ("applied", ("R1", "AttributeError: DataFrame.append", "exact, 0 ms")),
        ("applied", ("R2", "AttributeError: DataFrame.iteritems", "exact, 0 ms")),
        ("file", ("report.py", "working", "fast", 1)),
        ("update", {"tests_passing": 12, "prompt_tokens": 1010, "naive_prompt_tokens": 4760}),
        ("file", ("report.py", "fixed", None, None, "R1, R2 applied")),
        ("finish", "https://github.com/dhanraj176/sales-report/pull/1"),
    ]
    with Dashboard("demo", repo="sales-report", mode="rules on", files=files) as dash:
        for kind, arg in steps:
            if kind == "update":
                dash.update(**arg)
            elif kind in ("file", "learned", "applied"):
                getattr(dash, kind)(*arg)
            elif kind == "finish":
                dash.finish(pr_url=arg)
            else:
                getattr(dash, kind)(arg)
            time.sleep(seconds / len(steps))


if __name__ == "__main__":
    demo()
