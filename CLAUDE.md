# Evergreen

Evergreen is an agent that upgrades a repo (demo: pandas 1.5 → 2.2) one file at a time with a
"ratchet": a fix is kept only if no passing test breaks. Every verified fix becomes a scored,
sourced, versioned rule, written into the target's AGENTS.md and opened as a PR.
The plan is docs/BRIEF.md (gitignored). Read it in full before a new phase.

## Decisions that override the brief
- No AWS Bedrock. Patches come from a local Liquid model: `LLM_PROVIDER=liquid` (default) is LFM2.5-8B-A1B on
  `PATCH_SERVER_URL` (http://127.0.0.1:8081); `openai`/`anthropic` (with `LLM_MODEL`) are optional behind the same
  switch. `LLAMA_SERVER_URL` (LFM2.5-1.2B, http://127.0.0.1:8080) matches rules. Use 127.0.0.1, not localhost.
- Patches are line edits: the model sees the numbered file and returns JSON `{explanation, edits: [{line_no,
  new_text}]}` (llama-server JSON-schema constraint; `new_text` may span lines); `patch()` returns the full
  `new_source`. Sampling: temperature 0.2, top_k 80, repeat penalty 1.05. Fast mode (`think=False`) only when every
  failure in the file has a matched rule and it isn't a retry. Rules come from the accepted patch's own edits; the
  regex is the changed span, kept only if it reproduces the edit. Matching: exact signature first, then Liquid.
- Ratchet keeps partial progress: reject only if a golden case that matched stops matching or any case gives wrong
  output ("mismatch"); still-erroring cases are fine; ≥1 new test must pass. Instant-first: validated rule regexes
  are tried alone (0 tokens) and committed if kept; the next round sends only the rest to Liquid.
- Sponsor tools: Liquid (rule matching and patches), Nimble, RawTree.
- The section 20 dataclasses live in `evergreen/schema.py`, not types.py (types.py shadows the stdlib module).
- Demo target is `../sales-report`, a separate repo; its golden script is `golden/golden.py` there.
- Must run on Windows and macOS:
  - venv interpreter is `Scripts\python.exe` on Windows and `bin/python` elsewhere (`testrun.venv_python`).
  - Run Python via `uv run` or the venv interpreter, never bare `python`. Helper scripts are Python, never bash.
  - Read and write all files as UTF-8; keep a file's existing line endings when rewriting it.
  - Traceback paths may use `\` or `/`. There is no rtree CLI on Windows: reach RawTree over its HTTP API.

## Layout
```
run.py                  CLI: --repo --venv --run-id [--no-rules] [--no-pr] [--memory-from RUN]; reset.py resets the target
evergreen/schema.py     shared dataclasses (Failure, TestRun, Rule, Evidence, PatchResult)
evergreen/loop.py       main ratchet loop            evergreen/testrun.py   pytest + JUnit parsing
evergreen/patcher.py    line-edit patches            evergreen/guards.py    patch guard + tests hash
evergreen/instant.py    zero-token rules             evergreen/gitops.py    snapshot/rollback/commit/PR
evergreen/liquid.py     llama-server rule matcher    evergreen/evidence.py  Nimble search + snippet
evergreen/changelog.py  release notes (stretch)      evergreen/memory.py    RawTree log/query + rule cache
evergreen/golden.py     golden-output check          evergreen/agents_md.py AGENTS.md marker block
evergreen/display.py    rich live screen             evergreen/results.py   summary table/json, PR body
bench/charts.py         charts from RawTree          runs/                  logs, rules.json, <run_id>/summary.json
```

## Rules
- `schema.py` and the section 20 function signatures change only with the user's approval. Approved so far:
  `save_rule_event(run_id, rule, event)`, `PatchResult.new_rules`, `patch(..., think=True)`, per-case `golden_ok`.
- Never commit `.env` or any key.
- Never hand-edit `../sales-report`. If a run leaves it dirty, restore it with git.
- Commit locally after each done-check; never push unless asked.
- docs/BRIEF.md is the plan. Setup: `uv venv --python 3.11 .venv`, `uv pip install --python .venv -r requirements.txt`.
