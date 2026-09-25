"""P3: the agent's memory. RawTree over its HTTP API, mirrored to local JSONL, plus the local rule cache.

log(table, row) appends the row to runs/<run_id>.jsonl at once and queues it for RawTree; a daemon
thread inserts queued rows in batches every ~2 s. flush() forces that and runs at exit.
Each JSONL line is {"_table": <table>, **row}; rows without a run_id go to runs/_misc.jsonl.
query(sql) asks RawTree, and falls back to running the SQL over the JSONL (in-memory sqlite)
when there is no key or RawTree fails. Nothing here raises on RawTree trouble.
"""
import atexit
import json
import os
import queue
import re
import sqlite3
import threading
import time
from dataclasses import asdict, fields
from pathlib import Path

import requests

from evergreen.schema import Rule

API = "https://api.rawtree.com"
RUNS = Path(__file__).resolve().parent.parent / "runs"
MISC_RUN = "_misc"
stats = {"inserted": 0, "insert_errors": 0, "last_error": None}

_q = queue.Queue()
_pending: dict[str, list[dict]] = {}     # rows whose insert failed; retried on the next flush
_flush_lock = threading.Lock()
_file_lock = threading.Lock()
_flusher = None


def confidence(rule: Rule) -> float:       # Laplace-smoothed success rate (12.2)
    return (rule.succeeded + 1) / (rule.applied + 2)


def trusted(rule: Rule) -> bool:           # eligible for zero-token application
    return rule.succeeded >= 2 and confidence(rule) >= 0.75


def _headers():
    return {"Authorization": f"Bearer {os.environ.get('RAWTREE_API_KEY', '')}",
            "x-rawtree-database": os.environ.get("RAWTREE_DATABASE") or "evergreen",
            "Content-Type": "application/json"}


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False,
                      default=lambda o: sorted(o) if isinstance(o, (set, frozenset)) else str(o))


def _run_file(run_id) -> Path:
    return RUNS / (re.sub(r"[^\w.-]", "_", str(run_id)) + ".jsonl")


def log(table: str, row: dict) -> None:
    row = {**row}
    row.setdefault("at", int(time.time()))
    RUNS.mkdir(parents=True, exist_ok=True)
    with _file_lock, open(_run_file(row.get("run_id") or MISC_RUN), "a",
                          encoding="utf-8", newline="\n") as fh:
        fh.write(_dumps({"_table": table, **row}) + "\n")
    if os.environ.get("RAWTREE_API_KEY"):
        _q.put((table, json.loads(_dumps(row))))
        _start_flusher()


def _start_flusher():
    global _flusher
    if _flusher is None:
        _flusher = threading.Thread(target=_flush_forever, daemon=True, name="rawtree-flush")
        _flusher.start()


def _flush_forever():
    while True:
        time.sleep(2)
        try:
            flush()
        except Exception as e:           # the thread must never die
            stats["last_error"] = f"{type(e).__name__}: {e}"


def flush(timeout: float = 10) -> None:
    """Insert every queued row into RawTree now. Failed batches are kept for the next flush."""
    with _flush_lock:
        batch = _pending
        while True:
            try:
                table, row = _q.get_nowait()
            except queue.Empty:
                break
            batch.setdefault(table, []).append(row)
        if not batch or not os.environ.get("RAWTREE_API_KEY"):
            return
        for table in list(batch):
            rows = batch[table]
            try:
                r = requests.post(f"{API}/v1/tables/{table}", headers=_headers(),
                                  data=_dumps(rows).encode("utf-8"), timeout=timeout)
                r.raise_for_status()
                stats["inserted"] += len(rows)
                del batch[table]
            except requests.RequestException as e:
                stats["insert_errors"] += 1
                stats["last_error"] = f"insert {table}: {e}"
                batch[table] = rows[-10_000:]


atexit.register(flush, 5)


def query(sql: str) -> list[dict]:
    if os.environ.get("RAWTREE_API_KEY"):
        flush()
        try:
            r = requests.post(f"{API}/v1/query", headers=_headers(),
                              data=_dumps({"sql": sql}).encode("utf-8"), timeout=15)
            r.raise_for_status()
            return r.json().get("data", [])
        except (requests.RequestException, ValueError) as e:
            stats["last_error"] = f"query: {e}"
    return query_local(sql)


def _local_rows() -> dict[str, list[dict]]:
    tables: dict[str, list[dict]] = {}
    for path in sorted(RUNS.glob("*.jsonl")):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:               # blank or half-written line
                    continue
                tables.setdefault(row.pop("_table", "unknown"), []).append(row)
    return tables


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _to_sqlite(sql: str) -> str:                  # the ClickHouse-isms our charts use
    sql = re.sub(r"\bcount\(\s*\)", "count(*)", sql, flags=re.I)
    return re.sub(r"\buniq(?:Exact)?\(", "count(DISTINCT ", sql, flags=re.I)


def query_local(sql: str) -> list[dict]:
    """Run `sql` over runs/*.jsonl in an in-memory sqlite. Lists and dicts are stored as JSON text."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    try:
        for table, rows in _local_rows().items():
            cols = list(dict.fromkeys(k for r in rows for k in r))
            db.execute(f"CREATE TABLE {_ident(table)} ({', '.join(map(_ident, cols))})")
            db.executemany(f"INSERT INTO {_ident(table)} VALUES ({', '.join('?' * len(cols))})",
                           [[_cell(r.get(c)) for c in cols] for r in rows])
        return [dict(r) for r in db.execute(_to_sqlite(sql))]
    except sqlite3.Error as e:
        stats["last_error"] = f"local query: {e}"
        return []
    finally:
        db.close()


def _cell(v):
    if isinstance(v, (list, dict)):
        return _dumps(v)
    return int(v) if isinstance(v, bool) else v


def _rules_file() -> Path:
    return RUNS / "rules.json"


def load_rules() -> list[Rule]:
    path = _rules_file()
    if not path.exists():
        return []
    names = {f.name for f in fields(Rule)}
    return [Rule(**{k: v for k, v in r.items() if k in names})
            for r in json.loads(path.read_text(encoding="utf-8"))]


def save_rule_event(run_id: str, rule: Rule, event: str) -> None:
    """event: proposed | verified | applied | succeeded | failed | demoted | retired."""
    log("rule_events", {"run_id": run_id, "rule_id": rule.rule_id, "event": event,
                        **{k: v for k, v in asdict(rule).items() if k != "rule_id"},
                        "confidence": round(confidence(rule), 2)})
    rules = [r for r in load_rules() if r.rule_id != rule.rule_id] + [rule]
    RUNS.mkdir(parents=True, exist_ok=True)
    with open(_rules_file(), "w", encoding="utf-8", newline="\n") as fh:
        json.dump([asdict(r) for r in rules], fh, indent=2, ensure_ascii=False)
        fh.write("\n")
