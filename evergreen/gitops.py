"""P1: snapshot, rollback, commit, push, PR (brief 23.9). All git/gh calls run against `repo`."""
import os
import subprocess
import tempfile
from pathlib import Path

BRANCH = "evergreen/pandas-2"


def snapshot(path) -> str:
    """The file's exact text (UTF-8, line endings kept) for restore()."""
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def restore(path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _run(cmd, cwd) -> str:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed ({proc.returncode}): "
                           f"{(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def git(repo, *args) -> str:
    return _run(["git", *args], cwd=repo)


def _rel(repo, path) -> str:
    p = Path(path)
    return (p.resolve().relative_to(Path(repo).resolve()) if p.is_absolute() else p).as_posix()


def commit_file(repo, path, message: str) -> str:
    """Commit only `path` (other staged or dirty files are left alone). Returns the commit hash."""
    rel = _rel(repo, path)
    git(repo, "add", "--", rel)
    git(repo, "commit", "-q", "-m", message, "--", rel)
    return git(repo, "rev-parse", "HEAD")


def current_branch(repo) -> str:
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD")


def create_branch(repo, name: str = BRANCH) -> str:
    """Check out `name`, creating it from the current HEAD if it doesn't exist yet."""
    exists = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"],
                            cwd=repo, capture_output=True).returncode == 0
    git(repo, "checkout", "-q", *([] if exists else ["-b"]), name)
    return name


def push(repo, branch: str = BRANCH, remote: str = "origin") -> None:
    git(repo, "push", "-q", "-u", remote, branch)


def pr_for_branch(repo, branch: str = BRANCH) -> str | None:
    """URL of the open PR from `branch`, or None."""
    try:
        return _run(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "url",
                     "--jq", ".[0].url // empty"], cwd=repo) or None
    except RuntimeError:
        return None


def open_pr(repo, title: str, body: str, base: str = "main", head: str | None = None) -> str:
    """`gh pr create` from `head` (default: current branch) into `base`. Returns the PR URL."""
    fd, body_file = tempfile.mkstemp(suffix=".md", prefix="evergreen-pr-")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    try:
        return _run(["gh", "pr", "create", "--base", base, "--head", head or current_branch(repo),
                     "--title", title, "--body-file", body_file], cwd=repo)
    finally:
        os.remove(body_file)
