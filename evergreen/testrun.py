"""P1: pytest + JUnit parsing.

run_tests(repo, venv) runs the full suite in `venv` and returns a TestRun. Each failure is triaged
by parse_failure into its exception, the last traceback frame inside src/, and a normalized
signature that is the same for the same breakage in every file ("AttributeError: DataFrame.append").

  uv run python -m evergreen.testrun ../sales-report ../sales-report/.venv-new
"""
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from evergreen.schema import Failure, TestRun

TIMEOUT_S = 300
MAX_MESSAGE = 200

_FRAME_PYTEST = re.compile(r"^(?P<path>.+?\.py):(?P<line>\d+):", re.M)
_FRAME_PYTHON = re.compile(r'File "(?P<path>.+?\.py)", line (?P<line>\d+)')
_E_LINE = re.compile(r"^E\s+(?P<type>[A-Za-z_][\w.]*): (?P<msg>.*)$", re.M)
_REDUCTIONS = r"mean|median|sum|prod|std|var|sem|min|max|quantile|skew|kurt|corr|cov"


def venv_python(venv) -> Path:
    """The venv's interpreter: Scripts\\python.exe on Windows, bin/python elsewhere."""
    if os.name == "nt":
        return Path(venv) / "Scripts" / "python.exe"
    return Path(venv) / "bin" / "python"


def run_tests(repo, venv) -> TestRun:
    repo = Path(repo).resolve()
    fd, xml_path = tempfile.mkstemp(suffix=".xml", prefix="evergreen-junit-")
    os.close(fd)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    cmd = [str(venv_python(Path(venv).resolve())), "-m", "pytest", "-q",
           f"--junitxml={xml_path}", "-p", "no:cacheprovider"]
    try:
        try:
            proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return _suite_failure("TimeoutExpired", f"test suite took longer than {TIMEOUT_S}s")
        if os.path.getsize(xml_path) == 0:
            tail = (proc.stdout + proc.stderr).decode("utf-8", "replace").strip()[-MAX_MESSAGE:]
            return _suite_failure("PytestError", f"exit {proc.returncode}, no JUnit report: {tail}")
        return parse_junit(xml_path, repo)
    finally:
        os.remove(xml_path)


def parse_junit(xml_path, repo=None) -> TestRun:
    root = ET.parse(xml_path).getroot()
    passing, failing = set(), []
    for tc in root.iter("testcase"):
        tid = junit_test_id(tc.get("classname", ""), tc.get("name", ""), repo)
        bad = tc.find("failure")
        if bad is None:                 # never use `or` on Elements; empty Elements are falsy
            bad = tc.find("error")
        if bad is not None:
            failing.append(parse_failure(tid, bad.get("message", ""), bad.text or "", repo=repo))
        elif tc.find("skipped") is None:
            passing.add(tid)
    return TestRun(passing, failing)


def junit_test_id(classname, name, repo=None) -> str:
    """JUnit classname "tests.test_clean[.TestX]" + name → "tests/test_clean.py[::TestX]::name"."""
    if not classname:                   # collection error: name is the dotted module path
        return name.replace(".", "/") + ".py"
    parts = classname.split(".")
    for i in range(len(parts), 0, -1):
        path = "/".join(parts[:i]) + ".py"
        if repo is None or (Path(repo) / path).is_file():
            return "::".join([path, *parts[i:], name])
    return "::".join(["/".join(parts) + ".py", name])



def src_for_test(tid) -> str:
    """tests/test_clean.py::x → src/clean.py."""
    stem = Path(tid.split("::")[0]).stem
    return f"src/{stem[5:] if stem.startswith('test_') else stem}.py"


def parse_failure(test_id, message, text, repo=None) -> Failure:
    exc_type, msg = _exception(message, text)
    src_file, line_no = _src_frame(text, repo)
    line = _line_text(repo, src_file, line_no, text) if line_no else None
    if src_file is None:
        src_file = src_for_test(test_id)
    return Failure(test_id=test_id, src_file=src_file, exc_type=exc_type,
                   message=_short(msg), line_no=line_no, line=line,
                   signature=signature(exc_type, msg, line))


def _exception(message, text):
    message = message.strip()
    m = re.match(r'failed on (?:setup|teardown) with "(.*)"$', message, re.S)
    if m:
        message = m.group(1)
    m = re.match(r"([A-Za-z_][\w.]*): (.*)$", message, re.S) or re.match(r"([A-Za-z_][\w.]*)$", message)
    if m:
        return m.group(1).rsplit(".", 1)[-1], (m.group(2) if m.lastindex == 2 else "")
    last = None
    for last in _E_LINE.finditer(text):
        pass
    if last:
        return last.group("type").rsplit(".", 1)[-1], last.group("msg")
    if message.startswith("assert"):
        return "AssertionError", message
    return "Error", message


def _src_frame(text, repo):
    frames = [(m.start(), m.group("path"), int(m.group("line")))
              for rx in (_FRAME_PYTEST, _FRAME_PYTHON) for m in rx.finditer(text)]
    for _, path, line_no in sorted(frames, reverse=True):
        rel = _src_path(path, repo)
        if rel:
            return rel, line_no
    return None, None


def _src_path(path, repo):
    p = path.strip().replace("\\", "/")
    if "site-packages" in p or "/.venv" in p or p.startswith(".venv"):
        return None
    if repo is not None:
        root = str(Path(repo).resolve()).replace("\\", "/").rstrip("/") + "/"
        if p.lower().startswith(root.lower()):
            p = p[len(root):]
    m = re.search(r"(?:^|/)(src/.+\.py)$", p)
    return m.group(1) if m else None


def _line_text(repo, src_file, line_no, text):
    if repo is not None:
        try:
            with open(Path(repo) / src_file, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
            if 0 < line_no <= len(lines):
                return lines[line_no - 1].strip()
        except OSError:
            pass
    m = re.search(rf"^.*{re.escape(src_file.split('/')[-1])}:{line_no}:.*\n {{4}}(\S.*)$", text, re.M)
    return m.group(1).strip() if m else None


def _short(msg):
    msg = " ".join(msg.split())
    return msg if len(msg) <= MAX_MESSAGE else msg[:MAX_MESSAGE - 3] + "..."


def signature(exc_type, msg, line=None) -> str:
    """Normalize a failure so the same breakage gets the same signature in every file."""
    m = re.search(r"'(\w+)' object has no attribute '(\w+)'", msg)
    if m:
        return f"{exc_type}: {m.group(1)}.{m.group(2)}"
    m = re.search(r"module '([\w.]+)' has no attribute '(\w+)'", msg)
    if m:
        return f"{exc_type}: {m.group(1)}.{m.group(2)}"
    m = re.search(r"(\w+)\(\) got an unexpected keyword argument '(\w+)'", msg)
    if m:
        return f"{exc_type}: {m.group(1)}({m.group(2)})"
    m = re.search(r"cannot import name '(\w+)' from '([\w.]+)'", msg)
    if m:
        return f"{exc_type}: {m.group(2)}.{m.group(1)}"
    m = re.search(r"No module named '([\w.]+)'", msg)
    if m:
        return f"{exc_type}: {m.group(1)}"
    m = re.search(rf"agg function failed \[how->({_REDUCTIONS})\b", msg)
    if m:                               # groupby().mean() on text columns
        return f"{exc_type}: {m.group(1)}(numeric_only)"
    if exc_type == "TypeError" and re.search(r"Could not convert .* to numeric", msg, re.S):
        m = re.search(rf"\.({_REDUCTIONS})\(", line or "")
        return f"{exc_type}: {m.group(1) if m else 'reduction'}(numeric_only)"
    return f"{exc_type}: {_generic(msg)}"


def _generic(msg):
    msg = " ".join(msg.split())
    msg = re.sub(r"0x[0-9a-fA-F]+", "0x...", msg)
    msg = re.sub(r"'[^']{24,}'|\"[^\"]{24,}\"", "'...'", msg)
    msg = re.sub(r"\b\d+(\.\d+)?\b", "N", msg)
    return msg[:80]


def _suite_failure(exc_type, msg) -> TestRun:
    return TestRun(passing=set(), failing=[Failure(test_id="<suite>", src_file="", exc_type=exc_type,
                                                   message=_short(msg), line_no=None, line=None,
                                                   signature=f"{exc_type}: test suite")])


def main(argv) -> int:
    from dotenv import load_dotenv
    load_dotenv(encoding="utf-8")
    sys.stdout.reconfigure(errors="replace")
    if len(argv) != 2:
        print("usage: python -m evergreen.testrun <repo> <venv>")
        return 2
    run = run_tests(argv[0], argv[1])
    total = len(run.passing) + len(run.failing)
    print(f"{len(run.passing)}/{total} passing, {len(run.failing)} failing")
    for f in run.failing:
        where = f"{f.src_file}:{f.line_no}" if f.line_no else f"{f.src_file} (no src frame)"
        print(f"\n{f.test_id}\n  {where}  {f.signature}\n  line: {f.line}")
    print("\nsignatures:")
    for sig, n in Counter(f.signature for f in run.failing).most_common():
        files = sorted({f.src_file for f in run.failing if f.signature == sig})
        print(f"  {n:3d}  {sig}  ({', '.join(files)})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
