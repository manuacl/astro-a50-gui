"""Vendored third-party single-file libraries.

Currently:
- ``eh_fifty`` — Tom Dryer's MIT-licensed Astro A50 Gen 4 library
  (https://github.com/tdryer/eh-fifty). See ``LICENSE.txt``.

Vendored so source-tarball releases (consumed by Flathub) are
self-contained and don't depend on an upstream PyPI install at build
time. Sync upstream changes by copying the new ``eh_fifty.py`` here,
updating ``LICENSE.txt`` if its terms change, and bumping the matching
entry in ``_VENDORED_VERSIONS``.

``_VENDORED_VERSIONS`` is the source of truth for the weekly
``.github/workflows/vendor-check.yml`` job, which compares each entry
against PyPI and opens an issue when upstream moves ahead.
"""

_VENDORED_VERSIONS = {
    "eh_fifty": "0.3.0",
}
