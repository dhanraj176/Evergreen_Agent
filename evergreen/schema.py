"""Shared dataclasses (brief section 20). Changes need the team's approval."""
from dataclasses import dataclass, field


@dataclass
class Failure:
    test_id: str            # "tests/test_clean.py::test_merge_batches"
    src_file: str           # "src/clean.py"
    exc_type: str           # "AttributeError"
    message: str            # "'DataFrame' object has no attribute 'append'"
    line_no: int | None     # offending line in src_file, from the traceback
    line: str | None        # that line's text
    signature: str          # normalized: "AttributeError: DataFrame.append"


@dataclass
class TestRun:
    __test__ = False        # keep pytest from collecting this as a test class

    passing: set[str]
    failing: list[Failure]


@dataclass
class Rule:
    rule_id: str
    signature: str
    pattern: str
    replacement: str
    regex_find: str | None = None
    regex_replace: str | None = None
    source_url: str | None = None
    proven_on: list[str] = field(default_factory=list)   # ["pandas 2.2.3"]
    proof: list[str] = field(default_factory=list)       # test IDs
    applied: int = 1
    succeeded: int = 1
    status: str = "verified"   # verified | trusted | demoted | retired


@dataclass
class Evidence:
    url: str
    snippet: str
    latency_s: float


@dataclass
class PatchResult:
    new_source: str
    explanation: str
    new_rule: dict | None
    prompt_tokens: int
    output_tokens: int
    new_rules: list[dict] = field(default_factory=list)   # one per signature; new_rule is the first
