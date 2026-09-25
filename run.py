"""Evergreen CLI.

  uv run python run.py --repo ../sales-report --venv ../sales-report/.venv-new --run-id r1             # rules on
  uv run python run.py --repo ../sales-report --venv ../sales-report/.venv-new --run-id r2 --no-rules  # ablation
"""
import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description="Evergreen: fix a repo one file at a time with the ratchet "
                                             "and write verified rules into AGENTS.md.")
    ap.add_argument("--repo", required=True, help="target repo, e.g. ../sales-report")
    ap.add_argument("--venv", required=True, help="venv the target's tests run in, e.g. ../sales-report/.venv-new")
    ap.add_argument("--run-id", required=True, help="label for this run in the logs, e.g. r1")
    ap.add_argument("--no-rules", action="store_true", help="ablation: treat every error as new, reuse no rules")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(encoding="utf-8")

    from evergreen.loop import run
    return run(args.repo, args.venv, args.run_id, use_rules=not args.no_rules)


if __name__ == "__main__":
    sys.exit(main())
