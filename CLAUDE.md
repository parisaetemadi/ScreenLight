# Working agreements

Standing instructions from the owner. Read this first; don't re-confirm them in chat.

## Git
- **Commit straight to `main` and push.** No branches, no pull requests, no merges — the owner is the only committer here.
- **Never watch a PR.** Don't call `subscribe_pr_activity`; every wake event replays the whole conversation.
- **One commit per session**, not per fix. Batch related work.
- Risky change you want a record of? Say so in one line and ask — don't open a PR unprompted.

## Test before you commit
- **Run the thing yourself**, read the real output, fix what's broken. Find the bug before the owner does.
- Never report "it works" without having run it.
- For anything with judgement in it (search, scoring, matching), use a harness measuring real results against what a right answer looks like — not one hand-picked case.

## Saving tokens
- Bullets, not prose. Tables over paragraphs. No preamble, no recap, no "I'm about to…".
- Read only the files the task names. Don't survey the repo.
- `grep` and `sed -n` ranges over whole-file reads; targeted edits over rewrites.
- Batch independent tool calls into one turn.
- Don't re-read a file you just edited; don't re-verify what a tool already confirmed.
- **Don't ask permission** for deploys, settings, schema changes, or deleting what's been replaced. Decide, do it, report in one line. Ask only about decisions only the owner can make.

## Comments in code
- **No comment per chunk of code.** Don't narrate the code as you write it — it costs tokens and goes stale.
- Instead: **one short bullet list at the top of each file or section** saying what it does and why. Update it when the code changes.
- Inline comments only where the code can't say it itself: a non-obvious reason, a trap, a decision someone would otherwise undo.
- Delete dead code; never comment it out.

## This repo
- One Python file, standard library only, Python 3.9+. macOS display backend via CoreGraphics/`ctypes`.
- The daemon owns the gamma table; the CLI and the menu-bar app ask it. Never put a second copy of the schedule in the app.
- `python3 -m unittest discover -s tests` before pushing. No f-string backslashes — it's a SyntaxError before 3.12.
