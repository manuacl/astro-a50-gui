# Astro A50 GUI

A PyQt6 GUI to configure the Astro A50 Gen 4 wireless headset from Linux,
built on top of the [eh-fifty](https://github.com/tdryer/eh-fifty) library.

## Features

- Headset status (power, dock, battery) — polled asynchronously, never
  blocks the UI thread
- Game/voice balance, noise gate (with mode icons), alert volume
- Stream mix sliders (mic / chat / game / aux) for the analog Stream-out port
- EQ presets with the 5 builtin Astro Command Center templates
  (A50 MOD KIT, ASTRO, MEDIA, PRO, STUDIO), reverse-engineered via USB capture
- Per-band gain editing via a draggable bargraph (orange = modified)
- User-defined EQ presets:
  - Create from any modified state (asks for a name)
  - Update in place (only enabled for user presets)
  - Delete via the trash icon next to the combo
  - Persisted to `$XDG_CONFIG_HOME/astro-a50-gui/user-templates.json`
- **Batch sync**: every edit is local until you click **Synchroniser le
  dispositif**. The button turns orange to signal pending changes.
- KDE menu entry installation
- Base station / headset firmware info dialog
- French and English UI (autodetected via locale, override with `A50_LANG=fr|en`)

## Install

Python 3.10+ and a virtualenv with [eh-fifty][eh-fifty] (the underlying HID
library), PyUSB and PyQt6:

```bash
python3 -m venv .venv
.venv/bin/pip install eh-fifty pyusb PyQt6
```

[eh-fifty]: https://github.com/tdryer/eh-fifty

A udev rule is required to access the A50 USB device as a non-root user
(once, then re-plug the base station):

```bash
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="9886", ATTR{idProduct}=="002c", MODE:="0666"' \
    | sudo tee /etc/udev/rules.d/50-astro-a50.rules
```

## Run

```bash
.venv/bin/python gui.py
```

Or install a KDE menu entry from inside the GUI: **Outils → Installer dans le
menu KDE**.

## Architecture

The application is split into focused modules:

| Module               | Responsibility                                                |
|----------------------|---------------------------------------------------------------|
| `gui.py`             | `A50Window` (Qt main window), wiring, `main()`                |
| `eq_widget.py`       | `EqTemplatesWidget` — radios + combos + meter + buttons + state for the EQ preset feature |
| `eq_meter.py`        | `_EqMeter` — interactive 5-band bargraph widget               |
| `templates.py`       | Builtin `_EQ_TEMPLATES` + user-template JSON persistence      |
| `i18n.py`            | `TRANSLATIONS` (fr/en), `t()`, `_detect_lang()`               |
| `raw_request.py`     | Raw HID opcodes outside eh-fifty's public API (FW info etc.)  |
| `process_lock.py`    | Single-instance helper (`/proc` scan, kill stale GUIs)        |
| `status_worker.py`   | `QObject` worker polling status on a `QThread`                |
| `base_info_dialog.py`| Formats the "Informations base" dialog content                |
| `menu_install.py`    | KDE menu entry install / remove (.desktop file)               |

### Threading model

The A50 device handle is shared between the main UI thread and a background
`QThread` running `StatusWorker`. The worker performs the periodic status
polls (`get_headset_status` + `get_battery_status`) so they never freeze the
UI. A `threading.RLock` serialises every USB access — the worker takes it
during its poll, and the main thread takes it around its own bursts
(`reload_all`, `_on_save`, template save / push, firmware-info dialog).

## EQ workflow

The headset stores 3 slots, each with a `{name, gain[5], (freq, bw)[5]}`
record. Names of builtin templates are app-side fiction (in `_EQ_TEMPLATES`)
but get written verbatim into the slot's `name` field when synced — that's
how `_match_template` recognises a slot at reload time. User presets live in
a JSON library on disk; only their *content* gets pushed to a slot.

- **Radio buttons** (EQ 1/2/3) select which slot the bargraph displays. The
  selection becomes the device's *active* preset on the next **Synchroniser**.
- **Combo box** assigns a template to a slot. Builtins are immutable
  (headset icon). User presets show a star icon and a trash button which is
  invisible (opacity 0) on builtins.
- **Drag** a bar vertically in the bargraph to change a band's gain. Modified
  bars turn orange.
- **Réinitialiser**: discard local edits, reload the template's original
  values. Enabled only when bars are modified.
- **Sauvegarder**: update the current user preset in place (only enabled when
  the slot points at a user preset *and* has modified bars). Also writes the
  new state to the device immediately.
- **Créer un préréglage**: prompt for a new name and store the current bands
  as a new user template, push to device, save.
- **Synchroniser le dispositif** (action bar): pushes the **visible state**
  of every pending slot (the bargraph values, edits and all) plus scalar
  settings and the active-slot radio, then calls `save_values()` so it
  persists across reboots. Turns orange when there is anything to push.

### Sync semantics for modified presets

- **Modified user preset → sync**: the user preset is overwritten locally
  with the visible bands (so the library entry tracks what you sync), then
  pushed to the slot under its name. No prompt. The orange "modified"
  indication clears because the saved definition now matches what's shown.
- **Modified builtin → sync**: pushed as-is to the slot — `name` on the
  device stays as the builtin name ("MEDIA", "PRO"…), bands are the edited
  ones. The builtin library is *never* overwritten. At the next reload,
  `_match_template` won't match (gain differs), the combo falls back on the
  builtin name and the off-template bars stay orange. To name and reuse
  that state, click **Créer un préréglage**.

## Files

- `gui.py`, `eq_widget.py`, `eq_meter.py`, `templates.py`, `i18n.py`,
  `raw_request.py`, `process_lock.py`, `status_worker.py`,
  `base_info_dialog.py`, `menu_install.py` — application code
- `tests.py` — unit tests (run with `.venv/bin/python -m unittest tests.py`)
- `scripts/` — reverse-engineering helpers (USB sniffing, one-shot probes,
  libvirt USB-passthrough udev wrapper). Not required to run the GUI; see
  `scripts/` for individual purpose.

## Tests

```bash
.venv/bin/python -m unittest tests.py
```

Covers:
- Shape and bounds of every builtin EQ template
- User-template JSON round-trip and malformed-entry tolerance
- BCD / datetime helpers used to decode firmware build info
- `i18n.t()` lookups, fallback to English, kwargs formatting, FR/EN key parity
- `EqTemplatesWidget.has_pending()` dirty-state logic (radio vs device,
  pending bands)
- `EqTemplatesWidget._match_template()` (builtin / user / no-match)
- `EqTemplatesWidget.push_pending_to_device()` end-to-end: builtin pushed
  as-is, user preset overwritten locally and pushed, combo change pushes
  template values, no-pending skips, active-slot-only push, batched disk
  save for multiple user presets
- `EqTemplatesWidget.reload_under_lock()` regressions: modified-builtin
  reload leaves `_slot_pending` empty (root cause of the "Synchronisé"
  button never flipping to dirty); combo signals are blocked around
  `setCurrentIndex` during reload; clean reload matches templates with
  no orange flag
- `_on_band_modified` / `_on_template_combo_changed` per-event state
  transitions (off-template marking, back-to-template clearing, drag
  ignored while loading or with no bands, combo switch pulls template
  values and pendings all bands, combo back to device template clears
  pending)
- `_persist_and_push` save/create flow: user preset added or overwritten,
  one `_save_user_templates` call, device receives name/gain/5 bands/
  `save_values`, slot state reset, bandwidths inherited from the previous
  template (shelf bands 1 and 5 forced to 0)

## Limitations

- Editing only changes per-band **gain**. Frequency and bandwidth of each
  band stay fixed at whatever the currently selected template uses.
- The headset's firmware update path is **not** implemented (no Linux
  tooling exists to flash the A50 base/headset; use the official Windows
  Astro Command Center for that).
