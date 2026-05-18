"""Builtin EQ templates and user-template persistence.

The 5 builtin templates were extracted from a USB capture of Astro Command
Center pushing each preset to a slot. Each entry maps a band index (1..5)
to (center_freq_hz, bandwidth). Bands 1 and 5 are highpass / lowpass and
require bandwidth == 0.
"""
import json
import os
from pathlib import Path


_EQ_TEMPLATES: dict[str, dict] = {
    "A50 MOD KIT": {
        "gain": [-5, -7, 5, -7, 5],
        "bands": {1: (200, 0), 2: (325, 6963), 3: (2753, 8192),
                  4: (6691, 2048), 5: (11002, 0)},
    },
    "ASTRO": {
        "gain": [6, -5, 0, 7, 7],
        "bands": {1: (90, 0), 2: (406, 8192), 3: (783, 8192),
                  4: (4001, 8192), 5: (7001, 0)},
    },
    "MEDIA": {
        "gain": [0, -3, 0, 2, 4],
        "bands": {1: (95, 0), 2: (406, 8192), 3: (783, 8192),
                  4: (3901, 6963), 5: (6339, 0)},
    },
    "PRO": {
        "gain": [5, -2, 1, 7, 1],
        "bands": {1: (95, 0), 2: (406, 8192), 3: (783, 8192),
                  4: (3901, 2048), 5: (6339, 0)},
    },
    "STUDIO": {
        "gain": [0, 0, 0, 5, 7],
        "bands": {1: (95, 0), 2: (419, 4096), 3: (783, 8192),
                  4: (3499, 4096), 5: (6100, 0)},
    },
}


USER_TEMPLATES_FILE = (
    Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    / "astro-a50-gui" / "user-templates.json"
)


def _load_user_templates() -> dict:
    """Read user-defined templates from disk; tolerant of missing/malformed entries."""
    if not USER_TEMPLATES_FILE.exists():
        return {}
    try:
        raw = json.loads(USER_TEMPLATES_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, dict] = {}
    for name, tpl in raw.items():
        try:
            bands = {int(k): tuple(v) for k, v in tpl["bands"].items()}
            out[name] = {"gain": list(tpl["gain"]), "bands": bands}
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _save_user_templates(templates: dict) -> None:
    USER_TEMPLATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    serializable = {
        name: {
            "gain": tpl["gain"],
            "bands": {str(b): list(v) for b, v in tpl["bands"].items()},
        }
        for name, tpl in templates.items()
    }
    USER_TEMPLATES_FILE.write_text(json.dumps(serializable, indent=2))
