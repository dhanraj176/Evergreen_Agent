"""P1: pytest + JUnit parsing."""
import os
from pathlib import Path

from evergreen.schema import TestRun


def venv_python(venv) -> Path:
    """The venv's interpreter: Scripts\\python.exe on Windows, bin/python elsewhere."""
    if os.name == "nt":
        return Path(venv) / "Scripts" / "python.exe"
    return Path(venv) / "bin" / "python"


def run_tests(repo, venv) -> TestRun:
    # Stub: the real version runs pytest --junitxml in `venv` and parses the XML.
    return TestRun(passing=set(), failing=[])
