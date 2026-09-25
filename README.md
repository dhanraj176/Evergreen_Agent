# Evergreen

Evergreen is an agent that keeps a repo's AGENTS.md rules file true. It upgrades a codebase one file at a time, keeps a fix only when the full test suite shows nothing that passed before has broken, and turns each verified fix into a rule with a confidence score, a source link and the library version it was proven on, so the rule can be re-tested and retired when the library changes.

Sponsor tools:

- **Liquid**: LFM2.5 models on a local llama-server match failures to known rules and write the patches.
- **Nimble**: live web search for migration notes, release notes and GitHub issues.
- **RawTree**: the agent's memory; every attempt, rule event and test run is stored and queried there.

Work in progress, full README at submission.
