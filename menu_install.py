"""KDE menu entry install / remove (writes a .desktop file)."""
import subprocess
import sys
import textwrap
from contextlib import suppress
from pathlib import Path

from i18n import t


def install_entry(
    apps_dir: Path,
    desktop_file: Path,
    legacy_desktop_file: Path,
    process_name: str,
    script_path: Path,
) -> str:
    """Write a .desktop file in `apps_dir`. Returns a status-bar message,
    raises on error."""
    apps_dir.mkdir(parents=True, exist_ok=True)
    content = textwrap.dedent(f"""\
        [Desktop Entry]
        Type=Application
        Name={t('desktop_name')}
        GenericName=Headset configuration
        Comment={t('desktop_comment')}
        Exec={sys.executable} {script_path}
        Icon=audio-headset
        Terminal=false
        Categories=AudioVideo;Audio;Settings;
        Keywords=astro;a50;headset;audio;
        StartupWMClass={process_name}
    """)
    desktop_file.write_text(content)
    if legacy_desktop_file.exists():
        legacy_desktop_file.unlink()
    _refresh_desktop_db(apps_dir)
    return t("msg_menu_installed")


def remove_entry(
    apps_dir: Path,
    desktop_file: Path,
    legacy_desktop_file: Path,
) -> str:
    """Delete the .desktop files (legacy and current). Returns a status-bar
    message indicating whether anything was removed."""
    removed = False
    for path in (desktop_file, legacy_desktop_file):
        if path.exists():
            with suppress(OSError):
                path.unlink()
                removed = True
    if removed:
        _refresh_desktop_db(apps_dir)
        return t("msg_menu_removed")
    return t("msg_menu_absent")


def _refresh_desktop_db(apps_dir: Path) -> None:
    with suppress(Exception):
        subprocess.run(
            ["update-desktop-database", str(apps_dir)],
            check=False, capture_output=True, timeout=5,
        )
