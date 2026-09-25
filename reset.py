"""Put the demo target back for a rehearsal: close the Evergreen PR, delete evergreen/pandas-2 on
GitHub and locally, and leave the repo on main in its red state (pandas 2.2 bumped, nothing fixed).

  uv run python reset.py [--repo ../sales-report]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from evergreen.gitops import BRANCH


def sh(repo, *cmd) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent / "sales-report"))
    repo = Path(ap.parse_args().repo).resolve()

    prs = sh(repo, "gh", "pr", "list", "--head", BRANCH, "--state", "open", "--json", "number,url")
    for pr in json.loads(prs.stdout or "[]") if prs.returncode == 0 else []:
        done = sh(repo, "gh", "pr", "close", str(pr["number"]), "--comment", "Closed by reset.py for a rehearsal.")
        print(f"closed PR {pr['url']}" if done.returncode == 0 else f"could not close {pr['url']}: {done.stderr.strip()}")
    if prs.returncode != 0:
        print(f"could not list PRs: {prs.stderr.strip()}")

    if sh(repo, "git", "ls-remote", "--exit-code", "--heads", "origin", BRANCH).returncode == 0:
        out = sh(repo, "git", "push", "-q", "origin", "--delete", BRANCH)
        print(f"deleted origin/{BRANCH}" if out.returncode == 0 else f"could not delete origin/{BRANCH}: {out.stderr.strip()}")

    if sh(repo, "git", "status", "--porcelain", "--untracked-files=no").stdout.strip():
        sh(repo, "git", "reset", "-q", "--hard")
        print("discarded uncommitted changes to tracked files")
    out = sh(repo, "git", "checkout", "-q", "main")
    if out.returncode != 0:
        print(f"could not check out main: {out.stderr.strip()}")
        return 1
    if sh(repo, "git", "rev-parse", "--verify", "--quiet", f"refs/heads/{BRANCH}").returncode == 0:
        sh(repo, "git", "branch", "-q", "-D", BRANCH)
        print(f"deleted local {BRANCH}")
    sh(repo, "git", "fetch", "-q", "--prune", "origin")

    head = sh(repo, "git", "log", "-1", "--format=%h %s").stdout.strip()
    ahead = sh(repo, "git", "rev-list", "--count", "origin/main..main").stdout.strip()
    dirty = sh(repo, "git", "status", "--porcelain").stdout.strip()
    print(f"{repo.name}: on main at {head}" + (f", {ahead} commit(s) ahead of origin/main" if ahead not in ("", "0") else "")
          + (f"\nuntracked files left alone:\n{dirty}" if dirty else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
