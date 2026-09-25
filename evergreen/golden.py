"""P3: golden-output ratchet. Runs the target repo's golden/golden.py check in `venv`."""
import json
import os
import subprocess
from pathlib import Path

from evergreen.testrun import venv_python

TIMEOUT_S = 120


def golden_ok(repo, venv) -> dict[str, str]:
    """Per-case status from `golden.py check`: {"clean.merge_batches": "match" | "mismatch" | "error"}.

    Empty if the script couldn't run or printed no JSON, so no case counts as matching.
    """
    repo = Path(repo).resolve()
    cmd = [str(venv_python(Path(venv).resolve())), str(repo / "golden" / "golden.py"), "check"]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError):
        return {}
    for line in reversed(proc.stdout.splitlines()):
        try:
            out = json.loads(line)
        except ValueError:
            continue
        if isinstance(out, dict):
            return {str(k): str(v) for k, v in out.items()}
    return {}


def golden_broken(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Cases that break the ratchet: matched before but not now, or now give wrong output ("mismatch").
    A case that still errors (another breakage in the same function is unfixed) is allowed."""
    return [case for case in sorted(set(before) | set(after))
            if (before.get(case) == "match" and after.get(case) != "match") or after.get(case) == "mismatch"]
