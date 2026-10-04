"""Optional colour themes: palettes (.json) in the themes/ folder.

A theme only recolours the desktop's own style: every widget keeps its usual
shape and size, only the colours change. "auto" follows the desktop's own
colours, whatever they are. Each `themes/<name>.json` maps QPalette colour roles to
colours, with an optional "disabled" block for greyed-out widgets:

    {"Window": "#202326", "WindowText": "#fcfcfc", "disabled": {"Text": "#6d6f71"}}

A new file shows up in Tools → Theme; dashes in the file name become spaces.
"""
import json
import logging
from pathlib import Path

from PyQt6.QtGui import QColor, QIcon, QPalette
from PyQt6.QtWidgets import QApplication

from i18n import TRANSLATIONS, t

THEMES_DIR = Path(__file__).resolve().parent / "themes"
AUTO = "auto"
LOGGER = logging.getLogger(__name__)
_native_palette = None  # desktop palette, captured on first use
_current = AUTO


def icon(*names: str) -> QIcon:
    """The first of `names` the desktop's icon theme really has, else Qt's
    best guess for the first. hasThemeIcon, not fromTheme().isNull(): fromTheme
    falls back to a shorter name (network-wireless for network-wireless-hotspot),
    so a second choice would never be tried."""
    return QIcon.fromTheme(next((n for n in names if QIcon.hasThemeIcon(n)), names[0]))


def current() -> str:
    """Name of the theme currently applied."""
    return _current


def available() -> list[str]:
    if not THEMES_DIR.is_dir():
        return []
    return sorted(p.stem for p in THEMES_DIR.glob("*.json") if p.stem != AUTO)


def label(name: str) -> str:
    key = "theme_" + name.replace("-", "_")
    if key in TRANSLATIONS["en"]:
        return t(key)
    return name.replace("-", " ").replace("_", " ").title()


def load_palette(name: str, base: QPalette) -> QPalette:
    """Build the palette of themes/<name>.json on top of `base` (raises on a bad file)."""
    data = json.loads((THEMES_DIR / f"{name}.json").read_text())
    disabled = data.get("disabled", {}) if isinstance(data, dict) else None
    if not isinstance(disabled, dict):
        raise TypeError(f"{name}.json: expected a JSON object")
    palette = QPalette(base)
    for group, colours in (
        (QPalette.ColorGroup.All, {k: v for k, v in data.items() if k != "disabled"}),
        (QPalette.ColorGroup.Disabled, disabled),
    ):
        for role, value in colours.items():
            colour = QColor(value) if isinstance(value, str) else QColor()
            if not colour.isValid():
                raise ValueError(f"{name}.json: invalid colour for {role}: {value!r}")
            palette.setColor(group, QPalette.ColorRole[role], colour)
    return palette


def apply(app: QApplication, name: str) -> str:
    """Apply a theme; return the name actually applied ("auto" if it can't be loaded).

    Never raises: it runs from a Qt slot and at startup, and a broken theme file
    must not take the app down.
    """
    global _native_palette, _current
    if _native_palette is None:
        _native_palette = app.palette()
    if name != AUTO:
        try:
            app.setPalette(load_palette(name, _native_palette))
            _current = name
            return name
        except Exception as e:
            LOGGER.warning("Theme %r could not be loaded, using automatic: %s", name, e)
    app.setPalette(_native_palette)
    _current = AUTO
    return AUTO
