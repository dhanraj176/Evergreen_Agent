"""P1: zero-token rule application."""
from evergreen.schema import Failure, Rule


def apply_instant(source: str, failure: Failure, rule: Rule) -> str | None:
    # Stub: never applies instantly.
    return None
