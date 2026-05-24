---
name: finish-branch
description: Audit an astro-a50-gui branch (ruff, print() grep, unittest) THEN push and open the PR if everything is green. Trigger when the user says "finis la branche", "prêt à merger ?", "/finish-branch", "ouvre la PR".
---

# Finish branch — astro-a50-gui

Full "branch → opened PR" pipeline:

1. Local audit (CI reproduced + project-specific rules).
2. If **everything is green**: push + `gh pr create`.
3. If **any check is red**: stop, do not push, report what needs fixing.

The goal is **zero surprise** when CI runs and **zero PR opened on red**.

## When to use

- The user says "finis la branche", "prêt à merger ?", "audit branche",
  invokes `/finish-branch`, or asks to open a PR.
- Before a `git push` on an open PR (or before opening one).

## When NOT to use

- The branch is WIP and the user wants to push intermediate state
  without opening a PR — use `git push` directly.
- To validate a UI fix that needs a human eye on the running app —
  prefer launching `gui.py` manually first.

## Procedure

**Phase A — audit**: run checks 1 → 4 in order, stop at the first
block that surfaces red. The user prefers fixing one layer at a time
rather than receiving a tsunami of findings.

**Phase B — push + PR + version label**: only fire if phase A is fully
green.

### 1. CI reproduction (ruff + print() gate + unittest)

Mirrors `.github/workflows/ci.yml`. Run as the venv Python if it
exists (`.venv/bin/python`), otherwise system `python`.

```bash
PY=.venv/bin/python; [ -x "$PY" ] || PY=python

# 1a. ruff check (excludes vendor/ — third-party code).
# Install once: pipx install ruff (or pip install ruff in .venv).
ruff check --exclude vendor . || { echo "FAIL: ruff"; exit 1; }

# 1b. No stray debug print() outside scripts/ and tests.py.
if grep -nE '^[[:space:]]*print\(' \
     --include='*.py' \
     --exclude-dir=vendor --exclude-dir=scripts --exclude-dir=.venv \
     --exclude=tests.py \
     -r .; then
    echo "FAIL: stray print() above — remove or move under scripts/"
    exit 1
fi

# 1c. Tests.
$PY tests.py || { echo "FAIL: unittest"; exit 1; }
```

If `ruff` is not on PATH and not in `.venv`, report:
`FAIL: ruff not installed locally. Run \`pipx install ruff\` or
\`$PY -m pip install ruff\` then retry.` Do **not** install it
silently — the user owns their toolchain.

### 2. Project-specific rules

**2a. eh-fifty must be imported from the vendor copy.**

The project vendors `eh-fifty` under `vendor/eh_fifty.py` so source
tarballs (consumed by Flathub) are self-contained. A bare
`from eh_fifty import X` would silently pick up an upstream PyPI
install in the dev `.venv` while breaking on a freshly-cloned tarball.

```bash
if grep -rnE '^from eh_fifty\b|^import eh_fifty\b' \
     --include='*.py' --exclude-dir=vendor --exclude-dir=.venv .; then
    echo "FAIL: bare eh_fifty import above — use 'from vendor.eh_fifty import X' instead"
    exit 1
fi
```

**2b. pyproject.toml version is well-formed.**

`version.yml` parses the version with a strict regex
(`^version = "X.Y.Z"$`). If the line is reformatted (extra whitespace,
multi-line table) the bump silently no-ops.

```bash
grep -qE '^version = "[0-9]+\.[0-9]+\.[0-9]+"$' pyproject.toml \
    || { echo "FAIL: pyproject.toml version line is not in canonical form"; exit 1; }
```

### 3. Tests & docs consistent with the branch diff

```bash
changed=$(git diff --name-only origin/main...HEAD)
echo "$changed"

# 3a. A modified or added .py in the main source tree (not vendor/,
# scripts/, tests.py itself) should usually come with a tests.py
# touch. Warn (don't block) — some changes are pure refactors with
# no test impact.
for py in $(echo "$changed" \
    | grep -E '\.py$' \
    | grep -vE '^(vendor/|scripts/|tests\.py)'); do
    if ! echo "$changed" | grep -qx tests.py; then
        echo "WARN: $py modified without touching tests.py"
        echo "  → is a test missing? a regression case?"
    fi
done

# 3b. README mention of any new top-level module — the README's
# "Files" section lists every .py at the root. If a new file is added
# there, README.md should mention it.
for py in $(git diff --name-only --diff-filter=A origin/main...HEAD \
    | grep -E '^[^/]+\.py$' \
    | grep -vE '^(tests\.py|setup\.py)$'); do
    if ! grep -q "\`$py\`" README.md; then
        echo "WARN: new top-level module $py not mentioned in README.md 'Files' section"
    fi
done
```

### 4. Git state — blocking

```bash
# 4a. Clean working tree.
[ -z "$(git status --short)" ] \
    || { echo "FAIL: uncommitted changes — commit or stash"; exit 1; }

# 4b. Not on main.
branch=$(git rev-parse --abbrev-ref HEAD)
[ "$branch" = "main" ] && { echo "FAIL: HEAD is on main, no PR possible"; exit 1; }

# 4c. Up to date with origin/main.
git fetch origin main --quiet
behind=$(git rev-list --count HEAD..origin/main)
[ "$behind" -gt 0 ] && { echo "FAIL: branch behind origin/main by $behind commit(s) — rebase first"; exit 1; }
```

### 5. Push + open the PR (phase B)

**Only run if 1 → 4 are all green.**

```bash
git log --oneline origin/main..HEAD              # included commits
git diff origin/main...HEAD --stat               # change scope
```

Build the PR title + body from the commits AND the diff (not just the
latest commit). Follow conventional-commit prefixes
(`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`).

```bash
git push -u origin HEAD

# If a PR already exists, the push updated it — just capture the number.
if pr_url=$(gh pr view --json url --jq .url 2>/dev/null); then
    pr_number=$(gh pr view --json number --jq .number)
else
    gh pr create --title "<concise title (<70 chars)>" --body "$(cat <<'EOF'
## Summary
<1-3 bullets: what the PR changes and why>

## Test plan
- [x] `ruff check --exclude vendor .`
- [x] no stray `print()`
- [x] `python tests.py` (<N/N>)
- [x] vendor-only `eh_fifty` import rule
- [x] `pyproject.toml` version line is canonical
- [ ] <manual GUI check if UI changed: launch `python gui.py` with the headset on>

(Each phase-A check goes in as one bullet. Drop bullets that don't
apply. Do NOT list "CI green" — CI is mandatory, listing it is noise.)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
    pr_url=$(gh pr view --json url --jq .url)
    pr_number=$(gh pr view --json number --jq .number)
fi
echo "PR: $pr_url (#$pr_number)"
```

### 6. Version bump label — delegate to `bump-label` skill

Hand off to the project-bundled `bump-label` skill
(`.claude/skills/bump-label/`). The skill applies the recommended
label directly, no confirmation prompt — the user can swap it after
the fact with one `gh pr edit` if they disagree.

Inputs to pass:

- `pr_number`: captured at step 5.
- `current_version`:
  `grep -E '^version = "' pyproject.toml | head -1 | sed -E 's/version = "([^"]+)"/\1/'`
- `changed_files`: `git diff --name-only origin/main...HEAD`
- `commits`: `git log --format='%B' origin/main..HEAD`

The label is consumed by `.github/workflows/version.yml`: on merge to
main, it bumps `pyproject.toml`'s `[project].version`, commits the
change, and tags `vX.Y.Z`. No `bump:*` label → workflow exits cleanly.

Return the PR URL **and** the chosen bump level (or "no bump") to the
user.

## Expected report

Summary at the end of phase A:

```
✓ ruff check
✓ no stray print()
✓ unittest (41/41)
✓ vendor-only eh_fifty import rule
✓ pyproject.toml version line canonical
⚠ tests & docs: gui.py modified without touching tests.py
✓ git: clean working tree, up to date with origin/main
```

- **All green (✓ + ⚠ ok to push)** → chain into phase B (push +
  `gh pr create` + bump label), then return:
  "PR opened: <url> (tagged bump:minor)".
- **Any FAIL** → **do not push, do not create a PR**. Cite the
  file:line, suggest the fix, let the user apply it.
- **⚠ findings**: confirm with the user before phase B if any are
  present.

## Why this procedure

- **Reproducing CI locally** avoids the push → CI red → fix → repush
  round-trip.
- **Vendor-import rule** prevents accidental regression of the
  vendoring choice (would break Flathub source tarballs silently).
- **Canonical version-line rule** prevents `version.yml` from
  no-op'ing silently after a `pyproject.toml` refactor.
- **Stopping at the first red block** avoids drowning the user; the
  next block may depend on the previous one.

Check details in `.github/workflows/ci.yml`.
