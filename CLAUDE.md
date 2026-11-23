# Working agreements

These are standing instructions from the repository owner. They apply to every
session, and they are not to be re-confirmed in chat.

## Shipping changes

- **Commit straight to `main`.** The owner has given explicit permission to push
  to the default branch. Don't open a pull request for ordinary work, and don't
  ask — a PR here costs more in usage than it adds in review.
- **If a PR does exist, merge it.** Once CI is green, mark it ready for review
  and merge. The answer is yes, every time.
- **Never watch a PR.** Do not call `subscribe_pr_activity`. No wake events, no
  polling, no safety-net check-ins: each one replays the whole conversation and
  burns usage for nothing. (The web harness may still subscribe on its own when
  a PR is created — one more reason not to create one.)
- Run the repo's tests before pushing. Pushing to `main` means nothing else will
  catch a mistake.

## Keeping sessions cheap

- Batch related fixes into one push rather than one per fix.
- Go straight to the file when the task names it; don't survey the repo first.
