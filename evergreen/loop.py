"""P1: main loop (the ratchet). Skeleton: one pass over the stubs, no patching yet."""
from evergreen.memory import load_rules, log
from evergreen.testrun import run_tests


def run(repo: str, venv: str, run_id: str, use_rules: bool = True) -> int:
    rules = load_rules() if use_rules else []
    before = run_tests(repo, venv)
    mode = "rules_on" if use_rules else "rules_off"
    total = len(before.passing) + len(before.failing)
    log("test_runs", {"run_id": run_id, "mode": mode, "passing": len(before.passing),
                      "failing": len(before.failing), "total": total})
    print(f"[{run_id}] {mode}: {len(rules)} rules loaded, "
          f"{len(before.passing)}/{total} passing (skeleton: loop not implemented)")
    return 0
