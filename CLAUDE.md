# Working agreements

These are standing instructions from the repository owner. They apply to every
session, and they are not to be re-confirmed in chat.

## Pull requests

- **Always merge.** Once CI is green, mark the PR ready for review and merge it.
  Do not ask first — the answer is yes, every time.
- **Never watch a PR.** Do not call `subscribe_pr_activity` after opening one.
  No wake events, no polling, no safety-net check-ins. Each of those replays the
  whole conversation and burns usage for nothing.

## Keeping sessions cheap

- Batch related fixes into one branch and one PR rather than a PR per fix.
- Go straight to the file when the task names it; don't survey the repo first.
