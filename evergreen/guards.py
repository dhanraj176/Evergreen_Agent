"""P1: patch guard + tests hash (brief section 9)."""
import ast
import difflib
import hashlib
import re
from pathlib import Path

BANNED = [r"pytest\.(skip|xfail)", r"except\s*:", r"except\s+Exception"]
PROTECTED = ("tests", "golden")          # read-only for the agent


def names(src):
    return {n.name for n in ast.parse(src).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


def patch_ok(old, new, max_ratio=0.4) -> tuple[bool, str]:
    try:
        ast.parse(new)
    except SyntaxError:
        return False, "does not parse"
    if names(old) - names(new):
        return False, "removed a function or class"
    for pat in BANNED:
        if len(re.findall(pat, new)) > len(re.findall(pat, old)):
            return False, f"added {pat}"
    changed = sum(1 for l in difflib.ndiff(old.splitlines(), new.splitlines()) if l[:1] in "+-")
    if changed > max_ratio * 2 * max(len(old.splitlines()), 1):
        return False, "patch too large"
    return True, "ok"


def tests_hash(repo) -> str:
    """sha256 over every file under tests/ and golden/ (path + bytes), skipping __pycache__."""
    root = Path(repo)
    files = sorted((p.relative_to(root).as_posix(), p)
                   for d in PROTECTED for p in (root / d).rglob("*")
                   if p.is_file() and "__pycache__" not in p.relative_to(root).parts)
    h = hashlib.sha256()
    for rel, p in files:
        data = p.read_bytes()
        h.update(f"{rel}\0{len(data)}\0".encode("utf-8"))
        h.update(data)
    return h.hexdigest()
