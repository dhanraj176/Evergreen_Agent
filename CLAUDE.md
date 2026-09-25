# Evergreen

Evergreen is an agent that upgrades a repo (demo: pandas 1.5 → 2.2) one file at a time with a
"ratchet": a fix is kept only if no passing test breaks. Every verified fix becomes a scored,
sourced, versioned rule, written into the target's AGENTS.md and opened as a PR.
The plan is docs/BRIEF.md (gitignored). Read it in full before a new phase.

## Decisions that override the brief
- No AWS Bedrock. Patches come from a local Liquid model: `LLM_PROVIDER=liquid` (default) is LFM2.5-8B-A1B on a
  second llama-server at `PATCH_SERVER_URL` (http://localhost:8081). `openai` and `anthropic` (with `LLM_MODEL`)
  are optional providers behind the same switch. `LLAMA_SERVER_URL` (LFM2.5-1.2B on 8080) is for rule matching.
- Patches are line edits, not whole-file rewrites: the model sees the file with line numbers and returns JSON
  `{edits: [{line_no, new_text}], explanation, new_rule}` (new_rule fields as in brief 23.3), enforced by
  llama-server's JSON-schema constraint; `new_text` may span several lines. `patch()` applies the edits and still
  returns the full `new_source`, so the section 20 interface is unchanged.
  Sampling: temperature 0.2, top_k 80, repeat penalty 1.05.
- Sponsor tools: Liquid (rule matching and patches), Nimble, RawTree.
- The section 20 dataclasses live in `evergreen/schema.py`, not types.py (types.py shadows the stdlib module).
- Demo target is `../sales-report`, a separate repo; its golden script is `golden/golden.py` there.
- Must run on Windows and macOS:
  - venv interpreter is `Scripts\python.exe` on Windows and `bin/python` elsewhere (`testrun.venv_python`).
  - Run Python via `uv run` or the venv interpreter, never bare `python`.
  - Traceback paths may use `\` or `/`.
  - Read and write all files as UTF-8; keep a file's existing line endings when rewriting it.
  - Helper scripts are Python, never bash.
  - There is no rtree CLI on Windows: reach RawTree over its HTTP API.

## Layout
```
run.py                  CLI: --repo --venv --run-id [--no-rules]
evergreen/schema.py     shared dataclasses (Failure, TestRun, Rule, Evidence, PatchResult)
evergreen/loop.py       main ratchet loop            evergreen/testrun.py   pytest + JUnit parsing
evergreen/patcher.py    line-edit patches            evergreen/guards.py    patch guard + tests hash
evergreen/instant.py    zero-token rules             evergreen/gitops.py    snapshot/rollback/commit/PR
evergreen/liquid.py     llama-server rule matcher    evergreen/evidence.py  Nimble search + snippet
evergreen/changelog.py  release notes (stretch)      evergreen/memory.py    RawTree log/query + rule cache
evergreen/golden.py     golden-output check          evergreen/agents_md.py AGENTS.md marker block
evergreen/display.py    rich live counters           bench/charts.py        charts from RawTree
runs/                   local logs + rules.json (gitignored)
```
Setup: `uv venv --python 3.11 .venv`, `uv pip install --python .venv -r requirements.txt`,
copy `.env.example` to `.env`. Check: `uv run python run.py --help`.

## Rules
- `schema.py` and the section 20 function signatures change only with the user's approval.
- Never commit `.env` or any key.
- Never hand-edit `../sales-report`. If a run leaves it dirty, restore it with git.
- Commit locally after each done-check; never push unless asked.
- docs/BRIEF.md is the plan.
