"""GUI translations, one locales/<code>.json per language.

Each file maps a key to its text. A regional variant (es_MX.json) only holds
the keys it changes: a lookup tries the variant, then its base language
(es.json), then English. Dropping a new file into locales/ adds the language.
"""
import json
import logging
import os
from pathlib import Path

from PyQt6.QtCore import QLocale

import settings

LOCALES_DIR = Path(__file__).resolve().parent / "locales"
LOGGER = logging.getLogger(__name__)


def _code(name: str) -> str:
    """Canonical language code for a file or locale name: "es-mx" gives es_MX."""
    lang, _, region = name.replace("-", "_").partition("_")
    return f"{lang.lower()}_{region.upper()}" if region else lang.lower()


def _load(locales_dir: Path) -> dict[str, dict[str, str]]:
    """Every readable locale file. A broken one is skipped and logged, so a
    dropped-in file can't stop the app from starting."""
    translations = {}
    if locales_dir.is_dir():
        for path in sorted(locales_dir.glob("*.json")):
            code = _code(path.stem)
            if code == "auto":  # the language menu's own "auto" choice
                LOGGER.warning("Skipping %s: 'auto' is reserved", path.name)
                continue
            try:
                strings = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                LOGGER.warning("Skipping %s: %s", path.name, exc)
                continue
            if not isinstance(strings, dict) or not all(isinstance(v, str) for v in strings.values()):
                LOGGER.warning("Skipping %s: not a JSON object of strings", path.name)
                continue
            translations[code] = strings
    else:
        LOGGER.warning("No locales folder at %s", locales_dir)
    # English is every lookup's last resort: without it the UI shows raw keys.
    translations.setdefault("en", {})
    return translations


TRANSLATIONS = _load(LOCALES_DIR)


def _chain(code: str) -> list[str]:
    """Languages a lookup in `code` tries, in order: es_MX, es, en."""
    return [c for c in dict.fromkeys((code, code.split("_")[0], "en")) if c in TRANSLATIONS]


def _lookup(code: str, key: str) -> str | None:
    for lang in _chain(code):
        if text := TRANSLATIONS[lang].get(key):
            return text
    return None


def _match(name: str) -> str | None:
    """The best language for a locale name: "es_MX" and "es-MX" give es_MX, else es."""
    code = _code(name)
    for candidate in (code, code.split("_")[0]):
        if candidate in TRANSLATIONS:
            return candidate
    return None


# Shown in the language menu in their own language, whatever the UI language is.
LANGUAGE_NAMES = {code: TRANSLATIONS[code].get("language_name") or code for code in TRANSLATIONS}

def _system_lang() -> str:
    return _match(QLocale.system().name()) or "en"


def _detect_lang() -> str:
    override = _match(os.environ.get("A50_LANG", ""))
    if override:
        return override
    saved = settings.get("language", "auto")
    if saved in TRANSLATIONS:
        return saved
    return _system_lang()


LANG = _detect_lang()


def needs_restart(choice: str) -> bool:
    """Whether picking `choice` in the language menu changes the running language.

    The restart drops A50_LANG (see gui.restart_app), so only the choice and
    the system locale matter.
    """
    return (choice if choice in TRANSLATIONS else _system_lang()) != LANG


def gate_label(mode_name: str) -> str:
    """Translated noise gate mode name, or the mode's own name if there is none."""
    key = f"gate_{mode_name.lower()}"
    return t(key) if key in TRANSLATIONS["en"] else mode_name


def t(key: str, **kwargs) -> str:
    s = _lookup(LANG, key) or key
    return s.format(**kwargs) if kwargs else s
