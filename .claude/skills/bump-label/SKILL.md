---
name: bump-label
description: Auto-pick a SemVer bump level (major/minor/patch/none) from the branch diff and apply the matching `bump:<level>` label to the PR. The heuristic decides; no confirmation prompt. Caller passes the PR number and the diff context. Trigger when the user asks to "tag the PR with a bump level", "label the PR for release", or from a wrapper skill like `finish-branch`.
---

# Bump label — SemVer label flow for PRs

Records release intent on a PR via a `bump:<level>` label. The actual
git tag (`vX.Y.Z`) is created later by `.github/workflows/version.yml`
at merge time.

This skill ships inside the repo (`.claude/skills/bump-label/`) on
purpose: contributors who clone the project see it and can run the
same flow. The convention and heuristic are generic enough to copy
into another project — but the canonical copy lives with each project,
not in a user-global location.

## Convention

Three explicit labels:

- `bump:major` — breaking change (red, `B60205`)
- `bump:minor` — new feature, backwards-compatible (yellow, `FBCA04`)
- `bump:patch` — fix / perf / refactor (green, `0E8A16`)

**No label = no bump.** This is the default for a merge: `version.yml`
exits cleanly when no `bump:*` label is present on the PR.

## When to use

- Invoked from `finish-branch` right after `gh pr create`.
- Standalone: the user asks "label this PR for a minor release", "tag
  the PR with a bump level", etc.

## When NOT to use

- The project has no version-bump workflow reading these labels → the
  label is inert. (Not the case here — `version.yml` consumes them.)
- The PR is from a fork — labels are still settable but the release
  workflow may not run on merge depending on the project's setup.

## Inputs (the caller must provide)

| Input | Example | Notes |
|---|---|---|
| `pr_number` | `42` | The PR to label. |
| `current_version` | `0.1.0` | SemVer string. Used to compute the resulting next version for the report (e.g. "0.1.0 → 0.2.0"). |
| `changed_files` | output of `git diff --name-only origin/main...HEAD` | For the heuristic. |
| `commits` | output of `git log --format='%B' origin/main..HEAD` | For the heuristic. |

If any input is missing, derive it from the current repo (the skill is
fine working alone, but a wrapper that already has the data should pass
it to avoid recomputing).

## Procedure

### 1. Recommend a level from the diff

Heuristic, in order of precedence (first match wins):

| Signal | Recommended |
|---|---|
| Commit body has `BREAKING CHANGE:` or subject like `feat!:` / `fix(scope)!:` | `major` |
| Any commit starts with `feat:` or `feat(scope):` | `minor` |
| Only `docs/`, `README.md`, `CLAUDE.md`, `.claude/` touched | `none` |
| Any commit starts with `fix:` / `perf:` / `refactor:` | `patch` |
| Mixed / nothing matched | `patch` (conservative default for real code changes) |

```bash
changed=$(git diff --name-only origin/main...HEAD)
commits=$(git log --format='%B' origin/main..HEAD)

if echo "$commits" | grep -qE '(BREAKING CHANGE|^[a-z]+(\([^)]+\))?!:)'; then
    recommended=major
elif echo "$commits" | grep -qE '^feat(\([^)]+\))?:'; then
    recommended=minor
elif ! echo "$changed" | grep -qvE '^(docs/|README\.md|CLAUDE\.md|\.claude/)'; then
    recommended=none
elif echo "$commits" | grep -qE '^(fix|perf|refactor)(\([^)]+\))?:'; then
    recommended=patch
else
    recommended=patch
fi
```

### 2. Apply the label (or skip if `none`) — no prompt

Apply the heuristic's recommendation directly. **Do not ask the user
to confirm** — they explicitly opted into the auto-pick by invoking
this skill (or `finish-branch`, which delegates here). If they want a
different level, they can swap the label after the fact with `gh pr
edit <n> --remove-label bump:X --add-label bump:Y`, which is one
command.

Compute the next version inline so the report is informative:

- `major`: `X.Y.Z` → `(X+1).0.0`
- `minor`: `X.Y.Z` → `X.(Y+1).0`
- `patch`: `X.Y.Z` → `X.Y.(Z+1)`
- `none`: no bump, version stays `X.Y.Z`

```bash
chosen=<level from step 1>   # major | minor | patch | none

# Always strip any pre-existing bump:* label first (a PR has at most one,
# and switching from major→minor on a re-run must not stack).
existing=$(gh pr view "$pr_number" --json labels --jq '.labels[].name' | grep '^bump:' || true)
for l in $existing; do
    [ "$l" = "bump:$chosen" ] || gh pr edit "$pr_number" --remove-label "$l"
done

if [ "$chosen" = "none" ]; then
    echo "PR #$pr_number left unlabeled (no version bump)"
else
    # Ensure the three bump labels exist on the repo (idempotent — fails
    # silently if already present). bump:none is NOT a label — it's the
    # absence of one.
    gh label create "bump:major" --color B60205 --description "Bump MAJOR version on merge" 2>/dev/null || true
    gh label create "bump:minor" --color FBCA04 --description "Bump MINOR version on merge" 2>/dev/null || true
    gh label create "bump:patch" --color 0E8A16 --description "Bump PATCH version on merge" 2>/dev/null || true

    gh pr edit "$pr_number" --add-label "bump:$chosen"
    echo "Tagged PR #$pr_number with bump:$chosen"
fi
```

## Expected report

Single line to the caller (or to the user, if standalone):

- `bump:none` chosen → `"PR #42 left unlabeled (no version bump)"`
- Otherwise → `"PR #42 tagged bump:minor (0.1.0 → 0.2.0 on merge)"`

## Companion workflow

The label is consumed by `.github/workflows/version.yml` (already in
this repo): on push to main, it finds the merged PR, reads its
`bump:*` label, parses the current version from `pyproject.toml`'s
`[project].version` field with `grep`+`sed`, bumps it, commits the
change, and tags `vX.Y.Z`. With no `bump:*` label, the workflow exits
cleanly — matching this skill's "no label = no bump" default.
