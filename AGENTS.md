# AGENTS.md

PyQt6 GUI for the Astro A50 base station. `README.md` (Architecture, EQ
workflow, Tests) describes the modules and how the EQ state flows.

## Checks

CI runs `ruff check --exclude vendor .`, a grep for stray `print(` and
`python tests.py`. Run them before pushing. The `finish-branch` skill in
`.claude/skills/` runs these plus the two rules below, which CI does not check.

## Rules CI does not catch

- Import eh-fifty from the vendored copy, `from vendor.eh_fifty import X`, never
  `import eh_fifty`: a bare import picks up a PyPI install in the dev `.venv`
  and breaks on a fresh source tarball. `scripts/` is the exception.
- Keep the `version = "X.Y.Z"` line of `pyproject.toml` on one line, as is:
  `version.yml` reads it with a strict regex and silently skips the bump
  otherwise.
- Never edit `vendor/` by hand: it is synced from upstream eh-fifty, and
  `vendor-check.yml` tracks upstream releases.

## Device

- Talking to the headset goes through `eh_fifty`'s typed calls, which assert
  their limits (`set_eq_preset_freq_and_bw`, for one). Take dialog and import
  limits from `astroeq.FREQ_RANGE` / `BANDWIDTH_RANGE` rather than restating
  them.
- Reads are not range-checked: a value from the base can sit outside those
  limits, so code that shows it must not rewrite it unless the user changed it.

## Pull requests

Each PR gets a `bump:patch|minor|major` label (`bump-label` skill), which
`version.yml` turns into a tag at merge. No label, no release.
