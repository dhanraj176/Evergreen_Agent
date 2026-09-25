"""P1: line-edit patches. LLM_PROVIDER picks the model: liquid (default, LFM2.5-8B-A1B on
PATCH_SERVER_URL, JSON-schema constrained) | openai | anthropic. Edits are applied here; patch()
returns the full new source."""
from evergreen.schema import PatchResult


def patch(path, source, failures, hints, feedback) -> PatchResult:
    # Stub: returns the file unchanged.
    return PatchResult(new_source=source, explanation="stub: no change",
                       new_rule=None, prompt_tokens=0, output_tokens=0)
