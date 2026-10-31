"""GUI translations, one locales/<code>.json per language.

Each file maps a key to its text. A regional variant (es_MX.json) only holds
the keys it changes: a lookup tries the variant, then its base language
(es.json), then English. Dropping a new file into locales/ adds the language.
"""
import json
import os
from pathlib import Path

from PyQt6.QtCore import QLocale

import settings

LOCALES_DIR = Path(__file__).resolve().parent / "locales"


def _load(locales_dir: Path) -> dict[str, dict[str, str]]:
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(locales_dir.glob("*.json"))
    }


TRANSLATIONS = _load(LOCALES_DIR)


def _chain(code: str) -> list[str]:
    """Languages a lookup in `code` tries, in order: es_MX, es, en."""
    chain = [code, code.split("_")[0], "en"]
    return [c for i, c in enumerate(chain) if c in TRANSLATIONS and c not in chain[:i]]


def _lookup(code: str, key: str) -> str | None:
    for lang in _chain(code):
        if text := TRANSLATIONS[lang].get(key):
            return text
    return None


def _match(name: str) -> str | None:
    """The best language for a locale name: "es_MX" and "es-MX" give es_MX, else es."""
    lang, _, region = name.replace("-", "_").partition("_")
    code = f"{lang.lower()}_{region.upper()}" if region else lang.lower()
    for candidate in (code, lang.lower()):
        if candidate in TRANSLATIONS:
            return candidate
    return None


# Shown in the language menu in their own language, whatever the UI language is.
LANGUAGE_NAMES = {code: _lookup(code, "language_name") or code for code in TRANSLATIONS}

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
