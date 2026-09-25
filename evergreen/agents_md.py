"""P3: AGENTS.md writer. Owns only the block between the markers; the rest is left untouched."""
from pathlib import Path

START, END = "<!-- evergreen:start -->", "<!-- evergreen:end -->"


def write_agents_md(repo, rules, retired, needs_human) -> None:
    # Stub: a minimal block listing rule IDs and needs-a-human items.
    path = Path(repo) / "AGENTS.md"
    text = ""
    if path.exists():
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
    eol = "\r\n" if "\r\n" in text else "\n"

    lines = [START, "## Library rules (verified by Evergreen)"]
    lines += [f"- **{r.rule_id}** `{r.signature}`: {r.replacement}" for r in rules]
    lines += ["", "## Retired rules"] + [f"- ~~{r.rule_id}~~ `{r.signature}`" for r in retired]
    lines += ["", "## Needs a human"] + [f"- {item}" for item in needs_human]
    lines.append(END)
    block = eol.join(lines)

    if START in text and END in text:
        head, rest = text.split(START, 1)
        text = head + block + rest.split(END, 1)[1]
    else:
        text = (text.rstrip("\r\n") + eol + eol if text else "") + block + eol
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
