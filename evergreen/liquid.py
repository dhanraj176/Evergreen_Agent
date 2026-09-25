"""P2: llama-server (LFM2.5) client, per-call grammar, exact-match fallback."""
from evergreen.schema import Failure, Rule


def match_rule(failure: Failure, rules: list[Rule]) -> Rule | None:
    # Stub: exact signature match only.
    return next((r for r in rules
                 if r.status in ("verified", "trusted") and r.signature == failure.signature), None)
