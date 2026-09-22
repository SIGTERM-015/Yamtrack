# Fork branching and integration workflow

This is a personal fork: there are no pull requests and no merge commits.
Feature work lands on `dev` via rebase plus fast-forward merge, performed
locally by the maintainer.

Upstream sync is a separate procedure, documented in
[FORK-MAINTENANCE.md](FORK-MAINTENANCE.md). Do not mix the two.

## Branch hierarchy

| Ref | Meaning |
|---|---|
| `dev` | Fork working baseline. Default branch of `origin`. Pushing here triggers the full CI. |
| `integ/noche` | Integration branch where feature merges accumulated before going up to `dev`. |
| `docs/product-grill` | Product documentation branch. |
| `feat/eXX-description` | Feature branch, `XX` = epic/ticket number. |
| `fix/description` | Bugfix branch. |
| `spike/eXX-topic` | Research spike. Usually contributes only a doc under `docs/research/`. |
| `docs/description` | Documentation branch. |

In practice `dev`, `integ/noche`, and `docs/product-grill` end up pointing at
the same commit after each integration.

## Worktrees

Each working branch is developed in its own git worktree, so several can be
open in parallel. Observed convention:

```
/home/sigterm/.worktrees/yamtrack-<id>
```

**Warning: never create worktrees under `/tmp`.** They are lost on machine
reboot, including uncommitted work. This almost caused data loss once; keep
worktrees on persistent storage only.

## Integration procedure

Merge with rebase + `merge --ff-only`. Never merge commits, never PRs.

The working branch has a worktree attached, so it cannot be checked out from
the main repo. Rebase a throwaway copy instead:

```bash
# 1. From the main repo, branch a throwaway copy off the working branch tip.
git fetch origin
git checkout -b rebase/<id> <sha>

# 2. Rebase onto dev. Resolve conflicts keeping dev's content plus what the
#    feature adds.
git rebase dev

# 3. Abort and retry if the rebase goes wrong.
git rebase --abort

# 4. Validate (see below). Only when everything is green, fast-forward dev.
git checkout dev
git merge --ff-only rebase/<id>

# 5. Propagate to the other integration branches.
git checkout integ/noche && git merge --ff-only dev
git checkout docs/product-grill && git merge --ff-only dev

# 6. Push all three.
git push origin dev integ/noche docs/product-grill

# 7. Clean up: remove the worktree, delete the working and throwaway
#    branches, delete the remote branch.
git worktree remove /home/sigterm/.worktrees/yamtrack-<id>
git branch -d feat/eXX-description
git branch -d rebase/<id>
git push origin --delete feat/eXX-description
```

Replace `<id>`, `<sha>`, and `feat/eXX-description` with the actual worktree
name, tip commit, and branch.

## Validation gates

All of these must exit 0 before merging into `dev`:

```bash
# Lint.
./.venv/bin/ruff check --output-format=concise src

# Format.
./.venv/bin/ruff format --check src

# Tests for the touched app.
cd src && ../.venv/bin/python manage.py test <app> --parallel

# Full suite. The test count may go up, never down.
cd src && ../.venv/bin/python manage.py test --parallel
```

Environment note: `uv` and `ruff` are not always on `PATH`. Use the venv
binaries (`./.venv/bin/ruff`, `../.venv/bin/python`).
[FORK-MAINTENANCE.md](FORK-MAINTENANCE.md) assumes `uv run`, which may not
work here.

Pushing to `dev` runs the full CI: Lint, App Tests, CodeQL, Docker Image,
and build-docs. All five must stay green.

## Branch hygiene

Delete branches and worktrees once integrated. Before deleting, confirm the
work is safely on `dev`:

```bash
# The tip commit must be an ancestor of dev.
git merge-base --is-ancestor <sha> dev && echo integrated

# The worktree must have no uncommitted changes.
git -C /home/sigterm/.worktrees/yamtrack-<id> status --porcelain
# (empty output = clean)
```

Never delete `release`, `gh-pages`, `feat/add-api`, or dependabot branches.
