"""P3: rich live counters (brief 15.4) and a scrolling log that shows every Nimble URL.

    with Dashboard("r1") as dash:
        dash.update(tests_passing=4, tests_total=20)   # set counters
        dash.bump("rollbacks")                          # add to a counter
        dash.log("clean.py: patch accepted")            # a log line
        dash.nimble("https://pandas.pydata.org/...")   # +1 web lookup, URL in the log

On a terminal this is a live view. Otherwise (piped, recorded) log lines print as they happen
and the counters print once at the end.
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


def _never_fail_encoding(stream):
    """Never die on a stream that can't encode a URL or log text (e.g. cp1252 when piped)."""
    enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
    if enc != "utf8" and hasattr(stream, "reconfigure"):
        stream.reconfigure(errors="replace")


class Dashboard:
    def __init__(self, run_id: str, log_lines: int = 12, console: Console | None = None):
        self.console = console or Console()
        _never_fail_encoding(self.console.file)
        self.run_id = run_id
        self.counts = dict.fromkeys(COUNTERS, 0)
        self.lines = deque(maxlen=log_lines)
        self.started = time.time()
        self._live = None

    def __enter__(self):
        if self.console.is_terminal:
            self._live = Live(self._render(), console=self.console, refresh_per_second=4)
            self._live.start()
        return self

    def __exit__(self, *exc):
        if self._live:
            self._live.update(self._render(), refresh=True)
            self._live.stop()
        else:
            self.console.print(self._counters())
        return False

    def update(self, **counters) -> None:
        unknown = set(counters) - set(COUNTERS)
        if unknown:
            raise ValueError(f"unknown counters: {sorted(unknown)}")
        self.counts.update(counters)
        self._refresh()

    def bump(self, name: str, n: int = 1) -> None:
        self.update(**{name: self.counts[name] + n})

    def log(self, msg: str, style: str = "") -> None:
        line = Text(f"{time.time() - self.started:6.1f}s  ", style="dim") + Text(msg, style=style)
        self.lines.append(line)
        if self._live:
            self._refresh()
        else:
            self.console.print(line, soft_wrap=True)

    def nimble(self, url: str, query: str | None = None) -> None:
        self.counts["web_lookups"] += 1
        self.log(f"Nimble: {url}" + (f"  ({query})" if query else ""), style="cyan")

    def _refresh(self):
        if self._live:
            self._live.update(self._render())

    def _counters(self) -> Panel:
        c = self.counts
        grid = Table.grid(padding=(0, 3))
        grid.add_column(justify="right", style="bold")
        grid.add_column()
        grid.add_column(justify="right", style="bold")
        grid.add_column()
        cells = [(f"{c['tests_passing']}/{c['tests_total']}", "tests passing"),
                 (c["rules_learned"], "rules learned"),
                 (c["rules_applied"], "rules applied"),
                 (c["instant_fixes"], "instant fixes (0 tokens)"),
                 (c["web_lookups"], "web lookups"),
                 (c["rollbacks"], "rollbacks"),
                 (c["guard_rejections"], "patches rejected by guard"),
                 (c["human_interventions"], "human interventions"),
                 (f"{c['prompt_tokens']:,}", f"prompt tokens now (naive: {c['naive_prompt_tokens']:,})")]
        cells.append(("", ""))
        for (v1, l1), (v2, l2) in zip(cells[0::2], cells[1::2]):
            grid.add_row(str(v1), l1, str(v2), l2)
        return Panel(grid, title=f"Evergreen - run {self.run_id}", title_align="left")

    def _render(self):
        return Group(self._counters(), Panel(Group(*self.lines), title="log", title_align="left"))


def demo(seconds: float = 5) -> None:
    events = [
        ("update", {"tests_total": 20, "tests_passing": 4}),
        ("log", "clean.py: 2 failures, both unknown"),
        ("nimble", "https://pandas.pydata.org/docs/whatsnew/v2.0.0.html"),
        ("nimble", "https://github.com/pandas-dev/pandas/issues/35407"),
        ("update", {"prompt_tokens": 1840, "naive_prompt_tokens": 1840}),
        ("log", "clean.py: patch accepted, R1 and R2 learned"),
        ("update", {"tests_passing": 7, "rules_learned": 2}),
        ("log", "metrics.py: guard rejected patch (added except Exception)"),
        ("bump", "guard_rejections"),
        ("nimble", "https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.mean.html"),
        ("update", {"tests_passing": 10, "rules_learned": 3, "prompt_tokens": 1710, "naive_prompt_tokens": 6900}),
        ("log", "report.py: R1, R2 matched by Liquid, no web lookup"),
        ("update", {"tests_passing": 13, "rules_applied": 2}),
        ("log", "summary.py: R1 instant fix (0 tokens), R3 hint"),
        ("update", {"tests_passing": 16, "rules_applied": 4, "instant_fixes": 1}),
        ("log", "all green: 20/20"),
        ("update", {"tests_passing": 20}),
    ]
    with Dashboard("demo") as dash:
        for kind, arg in events:
            if kind == "update":
                dash.update(**arg)
            elif kind == "bump":
                dash.bump(arg)
            else:
                getattr(dash, kind)(arg)
            time.sleep(seconds / len(events))


if __name__ == "__main__":
    demo()
