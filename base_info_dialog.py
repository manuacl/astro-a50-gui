"""Builds the "Informations base" dialog content.

Pure formatting: takes the raw HID responses and returns the HTML lines
to display in a QMessageBox. Kept separate from the Qt widget so it's
easy to test or render in another UI.
"""
from i18n import t


def format_base_info(
    dev_info: bytes,
    fw_info: bytes,
    base_minor: bytes,
    hs_major: bytes,
    hs_minor: bytes,
) -> list[str]:
    """Format the firmware info lines for the QMessageBox.

    `0x03` response layout:
      [0:4]   header
      [4:6]   vendor ID (LE u16)
      [6:8]   product ID (LE u16)
      [8:16]  datetime that doesn't reflect the current firmware (see
              README for why we no longer expose it)
      [21:25] base firmware major version (LE u32)
    """
    vid = int.from_bytes(dev_info[4:6], "little") if len(dev_info) >= 6 else 0
    pid = int.from_bytes(dev_info[6:8], "little") if len(dev_info) >= 8 else 0
    base_major = int.from_bytes(dev_info[21:25], "little") if len(dev_info) >= 25 else 0
    base_min = base_minor[0] if base_minor else 0
    base_ver = f"{base_major}.{base_min}"
    # 0xda(0a) → headset firmware major (LE u32); 0xd6(0a) → minor (u8).
    hs_maj = int.from_bytes(hs_major[:4], "little") if len(hs_major) >= 4 else 0
    hs_min = hs_minor[0] if hs_minor else 0
    hs_ver = f"{hs_maj}.{hs_min}"

    return [
        f"<b>{t('info_hwid')}:</b> {vid:04x}:{pid:04x}",
        f"<b>{t('info_fw_base')}:</b> {base_ver}",
        f"<b>{t('info_fw_headset')}:</b> {hs_ver}",
        "",
        f"<b>{t('info_raw_title')}</b>",
        f"<small><code>0x03:     {dev_info.hex()}</code></small>",
        f"<small><code>0x83(01): {fw_info.hex()}</code></small>",
        f"<small><code>0x55:     {base_minor.hex()}</code></small>",
        f"<small><code>0xda(0a): {hs_major.hex()}</code></small>",
        f"<small><code>0xd6(0a): {hs_minor.hex()}</code></small>",
    ]
