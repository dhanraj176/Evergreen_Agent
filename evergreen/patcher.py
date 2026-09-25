"""P1: whole-file patch via the LLM API set by LLM_PROVIDER / LLM_MODEL, forced submit_patch tool."""
from evergreen.schema import PatchResult


def patch(path, source, failures, hints, feedback) -> PatchResult:
    # Stub: returns the file unchanged.
    return PatchResult(new_source=source, explanation="stub: no change",
                       new_rule=None, prompt_tokens=0, output_tokens=0)
