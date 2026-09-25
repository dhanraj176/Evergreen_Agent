"""P3: RawTree log/query (over its HTTP API) + local rule cache.

Stub: events go to runs/<table>.jsonl and rules to runs/rules.json.
"""
import json
import time
from dataclasses import asdict
from pathlib import Path

from evergreen.schema import Rule

RUNS = Path(__file__).resolve().parent.parent / "runs"
RULES_FILE = RUNS / "rules.json"


def log(table: str, row: dict) -> None:
    RUNS.mkdir(exist_ok=True)
    with open(RUNS / f"{table}.jsonl", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def query(sql: str) -> list[dict]:
    # Stub: no SQL engine locally.
    return []


def load_rules() -> list[Rule]:
    if not RULES_FILE.exists():
        return []
    return [Rule(**r) for r in json.loads(RULES_FILE.read_text(encoding="utf-8"))]


def save_rule_event(run_id: str, rule: Rule, event: str) -> None:
    """event: proposed | verified | applied | succeeded | failed | demoted | retired."""
    log("rule_events", {"run_id": run_id, "event": event, **asdict(rule), "at": int(time.time())})
    rules = [r for r in load_rules() if r.rule_id != rule.rule_id] + [rule]
    RUNS.mkdir(exist_ok=True)
    with open(RULES_FILE, "w", encoding="utf-8", newline="\n") as fh:
        json.dump([asdict(r) for r in rules], fh, indent=2, ensure_ascii=False)
        fh.write("\n")
