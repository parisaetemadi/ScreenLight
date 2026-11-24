# Working agreements

**Read this file before doing anything else in this repo** — and any other
`.md` instructions it points to. Standing instructions from the owner; don't
re-confirm them in chat.

- **No pull requests. Commit straight to `main` and push.** Standing permission
  from the owner, who is the only committer here. PRs cost several full-context
  replays per session (wake events for CI, ready-for-review, merge) for no review
  value on a solo repo. Don't create a branch, don't open a PR, don't merge one.
- **Never watch a PR.** Don't call `subscribe_pr_activity`; every wake event replays
  the whole conversation and burns usage for nothing.
- **One commit per session, not per fix.** Batch related work.
- If a change is genuinely risky and you want a reviewable record, say so in one
  line and ask — don't open a PR unprompted.
- Answer briefly. Tables over prose. Don't survey the repo when the task names the file.
