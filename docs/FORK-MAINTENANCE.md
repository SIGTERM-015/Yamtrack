# Fork maintenance: syncing with upstream

This repository is a fork of [FuzzyGrim/Yamtrack](https://github.com/FuzzyGrim/Yamtrack).
The fork carries its own features on top of upstream `dev`, so it needs to be
regularly rebased to stay current with upstream fixes and releases.

This document is the procedure to bring upstream changes into the fork.
Day-to-day feature integration into `dev` is a separate workflow, documented
in [BRANCHING.md](BRANCHING.md).

## Branches and remotes

| Ref | Meaning |
|---|---|
| `origin` | The fork: `SIGTERM-015/Yamtrack`. Push targets live here only. |
| `upstream` | The original project: `FuzzyGrim/Yamtrack`. Read-only. |
| `dev` | Fork working baseline. |
| `feat/add-api` | Fork integration branch that carries the custom API (`src/api/`). This is the branch that is rebased onto upstream. |

Note: `feat/add-api` exists on `origin` only, not in local checkouts, and the
fork's real working baseline is `dev` (see [BRANCHING.md](BRANCHING.md)).
Treat the table entry above as the sync procedure's target, not as a
description of where daily work happens.

Never push to `upstream`, and never push `dev`/feature branches to it.

## One-time setup

```bash
git remote add upstream https://github.com/FuzzyGrim/Yamtrack.git
git fetch upstream
```

Verify:

```bash
git remote -v
# origin    git@github.com:SIGTERM-015/Yamtrack.git (fetch/push)
# upstream  https://github.com/FuzzyGrim/Yamtrack.git (fetch/push)
```

## Sync procedure

Always work on a throwaway copy first so the rebase can be abandoned without
touching `feat/add-api` directly.

```bash
# 1. Fetch both remotes.
git fetch origin
git fetch upstream

# 2. Branch a working copy off the integration branch.
git checkout -b sync/upstream-$(date +%F) feat/add-api

# 3. Rebase onto upstream dev.
git rebase upstream/dev

# 4. Resolve conflicts (see below), then continue.
git rebase --continue

# 5. Validate (see below). Only when green, fast-forward the integration branch.
git checkout feat/add-api
git merge --ff-only sync/upstream-$(date +%F)
git push origin feat/add-api
```

To abort a bad rebase: `git rebase --abort`.

## Expected conflict zones

The fork and upstream both touch the central model definitions, so conflicts
concentrate in two files:

- **`src/app/models.py`** — upstream adds media types, fields, and status
  handling here; the fork adds its own model fields. Conflicts show up as
  overlapping field blocks and `MediaTypes`/`Status` choice changes. Keep both
  sides: upstream additions plus fork fields. Watch for duplicated enum
  values and for migrations that assume a specific field order.
- **`src/lists/models.py`** — list and list-item models. The fork extends
  list behaviour; upstream refactors it periodically. Re-apply the fork's
  additions on top of the upstream structure rather than restoring the old
  file wholesale.

After resolving, regenerate or renumber migrations if upstream shipped
migrations that collide with the fork's:

```bash
cd src
uv run manage.py makemigrations
uv run manage.py migrate
```

Commit any new migrations as part of the sync.

## Validation

Run from `src/`. A rebase is not accepted until all of these pass.

```bash
cd src

# Full suite.
uv run manage.py test --parallel

# Or pytest, if the environment is set up for it.
uv run pytest

# Lint/format, if ruff is available.
uv run ruff check .
```

Then verify the fork-specific API surface still loads, since the API lives on
`feat/add-api` and is the part most likely to break:

- `GET /api/v1/` endpoint tree responds (no import errors on boot).
- `GET /api/schema/` returns the OpenAPI schema.
- `GET /api/docs/` renders the Swagger UI.
- Spot-check `src/api/tests/`:

```bash
cd src
uv run manage.py test api.tests --parallel
```

## Known limitation

This procedure has **not been dry-run in the current environment**. There is no
real `upstream` remote configured here, so it is not possible to run a test
merge/rebase against actual upstream history. The commands above are the
documented procedure, verified against the repository layout (branch names,
conflict files, test entry points), but the rebase itself must be executed by
someone with network access to both remotes before relying on the conflict
guidance.

Re-validate this document the first time a real sync is performed and correct
anything that differs.
