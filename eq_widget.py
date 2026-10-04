"""EQ templates widget (radios + combos + meter + buttons).

Encapsulates the full EQ presets feature: 3 slot selectors, the
interactive 5-band bargraph, builtin/user template management
(create/update/delete), and the Reset / Save / Create-preset buttons.

The widget owns its own state (`_slot_bands`, `_slot_modified`,
`_slot_pending`, `_user_templates`, `_device_templates`). The main window
shares its `threading.RLock` and `Device` handle with this widget.

Public surface used by the main window:
- ``reload_under_lock(active_eq_preset)`` — re-read all slots from the device
  and refresh the UI. Caller must hold ``self._device_lock``.
- ``push_pending_to_device()`` — push the visible bands of every pending
  slot to the device (auto-saving user presets, leaving builtins untouched).
  Caller must hold ``self._device_lock``.
- ``selected_slot`` property — slot index (1..3) of the currently checked radio.
- ``has_pending() -> bool`` — True if any slot has unsynced changes.
- Signal ``dirty_changed(bool)`` — emitted when the global dirty state changes
  (used by the main window to update its sync button style).
"""
from __future__ import annotations

import threading
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QGraphicsOpacityEffect,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QRadioButton,
)

import astroeq
import themes
from device_handle import DeviceHandle
from eq_meter import _EqMeter
from i18n import t
from templates import _EQ_TEMPLATES, _load_user_templates, _save_user_templates

# set_eq_preset_name sends [slot, len, name, NUL] after a 3-byte header in a
# 64-byte report: longer names make the device write fail.
MAX_NAME_BYTES = 58


def _values(preset: dict) -> list[tuple[int, int]]:
    """A preset's (frequency, gain) per band, as the meter shows them."""
    return [(preset["bands"][b][0], preset["gain"][b - 1]) for b in range(1, 6)]


def _device_bands(bands: list[tuple[int, int]], ref: dict | None) -> dict:
    """The visible bands with the reference's bandwidths (the meter edits gains
    and frequencies only), as written to the device."""
    ref = ref or _EQ_TEMPLATES["MEDIA"]
    return {b: (bands[b - 1][0], 0 if b in (1, 5) else ref["bands"][b][1])
            for b in range(1, 6)}


class EqTemplatesWidget(QGroupBox):
    """Self-contained 5-band EQ + 3-slot preset editor."""

    dirty_changed = pyqtSignal(bool)

    # Combo data of the "<name> (on the base)" entry, shown while a slot holds
    # a preset that matches no template (e.g. one set up in Command Center).
    ON_DEVICE = "\x00on-device"

    def __init__(self, device: DeviceHandle, lock: threading.RLock, parent=None):
        super().__init__(t("grp_eq_templates"), parent)
        self.device = device
        self._device_lock = lock
        self._loading = False

        self._user_templates: dict[str, dict] = _load_user_templates()
        self._device_templates: dict[int, str | None] = {1: None, 2: None, 3: None}
        self._slot_bands: dict[int, list[tuple[int, int]]] = {1: [], 2: [], 3: []}
        self._slot_modified: dict[int, set[int]] = {1: set(), 2: set(), 3: set()}
        self._slot_pending: dict[int, set[int]] = {1: set(), 2: set(), 3: set()}
        self._slot_device: dict[int, dict | None] = {1: None, 2: None, 3: None}
        self._selected_slot = 1
        # Active EQ slot last known to be on the device (so we can detect when
        # the user picks a different radio and emit dirty accordingly).
        self._device_active_eq: int | None = None
        self._last_dirty = False

        self.template_combos: dict[int, QComboBox] = {}
        self.template_radios: dict[int, QRadioButton] = {}
        self.template_delete_btns: dict[int, QPushButton] = {}

        self._build_ui()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        layout = QGridLayout(self)
        self.template_radio_group = QButtonGroup(self)
        self.template_radio_group.setExclusive(True)
        for row, slot in enumerate((1, 2, 3)):
            radio = QRadioButton(t("lbl_eq_slot", n=slot))
            radio.toggled.connect(lambda checked, s=slot: self._on_slot_radio(s, checked))
            self.template_radio_group.addButton(radio, slot)
            layout.addWidget(radio, row, 0)
            combo_row = QHBoxLayout()
            combo_row.setContentsMargins(0, 0, 0, 0)
            combo = QComboBox()
            for name in sorted(self._all_templates(), key=str.casefold):
                combo.addItem(self._template_icon(name), name, name)
            combo.currentIndexChanged.connect(
                lambda _i, s=slot: self._on_template_combo_changed(s)
            )
            combo_row.addWidget(combo, 1)
            trash = QPushButton()
            icon = themes.icon("edit-delete", "user-trash")
            if not icon.isNull():
                trash.setIcon(icon)
            else:
                trash.setText("✕")
            trash.setFlat(True)
            trash.setMaximumWidth(28)
            trash.setToolTip(t("btn_delete_eq"))
            trash.clicked.connect(lambda _, s=slot: self._on_delete_template_for_slot(s))
            effect = QGraphicsOpacityEffect(trash)
            effect.setOpacity(0.0)
            trash.setGraphicsEffect(effect)
            trash.setEnabled(False)
            combo_row.addWidget(trash)
            layout.addLayout(combo_row, row, 1)
            self.template_combos[slot] = combo
            self.template_radios[slot] = radio
            self.template_delete_btns[slot] = trash
        layout.setColumnStretch(0, 0)
        layout.setColumnStretch(1, 1)
        self.meter = _EqMeter()
        self.meter.bandModified.connect(self._on_band_modified)
        layout.addWidget(self.meter, len(self.template_combos), 0, 1, 2)
        btn_row = QHBoxLayout()
        self.btn_reset_eq = QPushButton(t("btn_reset_eq"))
        self.btn_reset_eq.clicked.connect(self._on_reset_templates)
        self.btn_reset_eq.setEnabled(False)
        btn_row.addWidget(self.btn_reset_eq)
        btn_row.addStretch(1)
        self.btn_save_eq = QPushButton(t("btn_save_eq"))
        self.btn_save_eq.clicked.connect(self._on_update_template)
        self.btn_save_eq.setEnabled(False)
        self.btn_apply_eq = QPushButton(t("btn_apply"))
        self.btn_apply_eq.clicked.connect(self._on_create_template)
        self.btn_apply_eq.setEnabled(False)
        btn_row.addWidget(self.btn_save_eq)
        btn_row.addWidget(self.btn_apply_eq)
        layout.addLayout(btn_row, len(self.template_combos) + 1, 0, 1, 2)

    # ---------------------------------------------------------- properties

    @property
    def selected_slot(self) -> int:
        return self._selected_slot

    def has_pending(self) -> bool:
        if any(self._slot_pending.values()):
            return True
        return (self._device_active_eq is not None
                and self._selected_slot != self._device_active_eq)

    # ----------------------------------------------------- public reload/push

    def reload_under_lock(self, active_eq_preset: int | None) -> None:
        """Re-read all 3 slots from the device and refresh the UI.

        Caller must hold ``self._device_lock``.
        """
        self._loading = True
        try:
            for slot in self.template_combos:
                data = self._read_slot(slot)
                self._slot_device[slot] = data
                self._slot_bands[slot] = [] if data is None else _values(data)
                self._slot_pending[slot].clear()
            self._rematch()
            self._device_active_eq = active_eq_preset
            if active_eq_preset is not None and active_eq_preset in self.template_radios:
                self.template_radios[active_eq_preset].setChecked(True)
                self._selected_slot = active_eq_preset
            self._refresh_meter()
            self._update_apply_enabled()
        finally:
            self._loading = False
        self._emit_dirty_if_changed()

    def push_pending_to_device(self) -> None:
        """Push the visible state of every pending slot to the device, plus
        the active-EQ-slot selection if the user picked another radio.

        For slots whose currently selected combo is a user preset, the user
        preset is overwritten locally with the visible bands — what you sync
        becomes the new definition of that user preset. Modified builtins
        are pushed as-is (the slot's name on the device stays as the builtin
        name, the bands are the edited ones); the builtin itself is never
        overwritten.

        Caller must hold ``self._device_lock``."""
        redefined: dict[str, int] = {}  # user preset -> the slot that redefined it
        try:
            for slot in self.template_combos:
                if not self._slot_pending[slot]:
                    continue
                bands = self._slot_bands[slot]
                if not bands:
                    continue
                name, ref = self._resolve(slot)
                gain = [g for (_freq, g) in bands]
                device_bands = _device_bands(bands, ref)
                self.device.set_eq_preset_name(slot, name)
                self.device.set_eq_preset_gain(slot, gain)
                for b, (freq, bw) in device_bands.items():
                    self.device.set_eq_preset_freq_and_bw(slot, b, freq, bw)
                # Only once the device took it: a failed write keeps the preset
                # and its orange marks as they were. Only a slot showing the user
                # preset redefines it, not the base's own values that happen to
                # carry its name.
                if self.template_combos[slot].currentData() in self._user_templates:
                    self._user_templates[name] = {
                        "gain": list(gain),
                        "bands": dict(device_bands),
                    }
                    redefined[name] = slot
                self._slot_device[slot] = {"name": name, "gain": gain, "bands": device_bands}
                self._slot_pending[slot].clear()
            if (self._device_active_eq is not None
                    and self._selected_slot != self._device_active_eq):
                self.device.set_active_eq_preset(self._selected_slot)
                self._device_active_eq = self._selected_slot
        finally:
            # Presets the device already took are saved even when a later
            # slot fails, so memory and disk never disagree; the slots already
            # written are matched against what they now hold, other slots
            # showing a redefined preset take its new values, as Save does,
            # and the meter and buttons follow.
            if redefined:
                _save_user_templates(self._user_templates)
            self._rematch()
            for name, slot in redefined.items():
                self._show_new_values(name, skip=slot)
            self._refresh_meter()
            self._update_apply_enabled()
            self._emit_dirty_if_changed()

    # ------------------------------------------------------ handlers

    def _on_band_modified(self, band: int, gain: int) -> None:
        if self._loading:
            return
        slot = self._selected_slot
        bands = self._slot_bands[slot]
        if not bands:
            return
        freq, _old = bands[band - 1]
        bands[band - 1] = (freq, gain)
        self._slot_pending[slot].add(band)
        self._update_modified(slot)
        self._refresh_meter()
        self._update_apply_enabled()
        self._emit_dirty_if_changed()

    def _on_slot_radio(self, slot: int, checked: bool) -> None:
        if not checked:
            return
        self._selected_slot = slot
        self._refresh_meter()
        self._update_apply_enabled()
        if self._loading:
            return
        # Picking a different radio than the device's current active preset
        # makes us dirty (will be cleared by push_pending_to_device).
        self._emit_dirty_if_changed()

    def _on_template_combo_changed(self, slot: int) -> None:
        desired = self.template_combos[slot].currentData()
        ref = self._reference(slot)
        if ref is not None:
            self._slot_bands[slot] = _values(ref)
        # The base's own values, or the template it already holds: nothing to push.
        if desired in (self.ON_DEVICE, self._device_templates.get(slot)) or desired is None:
            self._slot_pending[slot].clear()
        else:
            self._slot_pending[slot] = {1, 2, 3, 4, 5}
        self._update_modified(slot)
        if slot == self._selected_slot:
            self._refresh_meter()
        self._update_apply_enabled()
        if self._loading:
            return
        self._emit_dirty_if_changed()

    def _on_reset_templates(self) -> None:
        if self._reference(self._selected_slot) is not None:
            self._on_template_combo_changed(self._selected_slot)

    def _on_delete_template_for_slot(self, slot: int) -> None:
        name = self.template_combos[slot].currentData()
        if name not in self._user_templates:
            return
        resp = QMessageBox.question(
            self, t("dlg_delete_preset_title"),
            t("dlg_delete_preset_msg", name=name),
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        removed = self._user_templates.pop(name)
        try:
            _save_user_templates(self._user_templates)
        except OSError as e:
            self._user_templates[name] = removed
            QMessageBox.warning(self, t("err_title"), t("err_template_save", error=e))
            return
        affected_slots = [
            s for s, c in self.template_combos.items() if c.currentData() == name
        ]
        # Those slots show what the base holds again, not the first template:
        # a Sync would otherwise rename the device's slot.
        for s in affected_slots:
            self._slot_pending[s].clear()
            device = self._slot_device.get(s)
            self._slot_bands[s] = _values(device) if device else []
        self._rematch()
        self._refresh_meter()
        self._update_apply_enabled()
        self._emit_dirty_if_changed()

    def _on_create_template(self) -> None:
        slot = self._selected_slot
        if not self._slot_modified.get(slot) or not self._slot_bands[slot]:
            return
        new_name = self._prompt_new_template_name()
        if new_name is None:
            return
        self._persist_and_push(slot, new_name, is_new=True,
                               btn=self.btn_apply_eq,
                               busy_key="btn_apply_busy", idle_key="btn_apply")

    def _on_update_template(self) -> None:
        slot = self._selected_slot
        name = self.template_combos[slot].currentData()
        if name not in self._user_templates:
            return
        if not self._slot_modified.get(slot) or not self._slot_bands[slot]:
            return
        self._persist_and_push(slot, name, is_new=False,
                               btn=self.btn_save_eq,
                               busy_key="btn_save_eq_busy", idle_key="btn_save_eq")

    # ------------------------------------------------------ persistence

    def _persist_and_push(self, slot: int, name: str, *, is_new: bool,
                          btn: QPushButton, busy_key: str, idle_key: str) -> None:
        bands = self._slot_bands[slot]
        btn.setEnabled(False)
        btn.setText(t(busy_key))
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            gain = [g for (_freq, g) in bands]
            new_bands = _device_bands(bands, self._reference(slot))
            # Device first: a write it refuses leaves library, disk and combos
            # untouched instead of holding a preset that can never be pushed.
            with self._device_lock:
                self.device.set_eq_preset_name(slot, name)
                self.device.set_eq_preset_gain(slot, gain)
                for b, (freq, bw) in new_bands.items():
                    self.device.set_eq_preset_freq_and_bw(slot, b, freq, bw)
                self.device.save_values()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            btn.setText(t(idle_key))
            self._update_apply_enabled()
            QMessageBox.warning(self, t("err_title"),
                                t("err_template_apply", error=repr(e) if not str(e) else e))
            return
        self._slot_device[slot] = {"name": name, "gain": gain, "bands": new_bands}
        self._slot_pending[slot].clear()
        self._user_templates[name] = {"gain": list(gain), "bands": new_bands}
        # Only this slot changed on the device: a global reload would drop the
        # other slots' unsynced edits and forget the device's active slot.
        self._rematch()
        self._show_new_values(name, skip=slot)
        QApplication.restoreOverrideCursor()
        btn.setText(t(idle_key))
        self._refresh_meter()
        self._update_apply_enabled()
        self._emit_dirty_if_changed()
        try:
            _save_user_templates(self._user_templates)
        except OSError as e:
            # The device holds the preset; only the library file failed.
            QMessageBox.warning(self, t("err_title"), t("err_template_save", error=e))

    def _prompt_new_template_name(self, suggestion: str | None = None) -> str | None:
        if suggestion is None:
            existing = set(self._all_templates())
            i = 1
            while f"{t('default_template_name')} {i}" in existing:
                i += 1
            suggestion = f"{t('default_template_name')} {i}"
        while True:
            name, ok = QInputDialog.getText(
                self,
                t("dlg_save_preset_title"),
                t("dlg_save_preset_label"),
                text=suggestion,
            )
            if not ok:
                return None
            name = name.strip()
            if not name:
                continue
            if len(name.encode()) > MAX_NAME_BYTES:
                QMessageBox.warning(self, t("err_title"),
                                    t("err_name_too_long", max=MAX_NAME_BYTES))
                continue
            if name in _EQ_TEMPLATES:
                QMessageBox.warning(self, t("err_title"),
                                    t("err_name_builtin", name=name))
                continue
            if name in self._user_templates:
                resp = QMessageBox.question(
                    self, t("dlg_save_preset_title"),
                    t("dlg_overwrite_user", name=name),
                )
                if resp != QMessageBox.StandardButton.Yes:
                    continue
            return name

    # ------------------------------------------ Command Center files

    def import_presets(self) -> list[str]:
        """Tools menu: pick .astroeq files exported by Astro Command Center."""
        paths, _ = QFileDialog.getOpenFileNames(
            self, t("act_import_presets"), str(Path.home()), t("filter_astroeq"))
        return self.import_files(paths)

    def import_files(self, paths) -> list[str]:
        """Add each .astroeq file as a user template named after the file.

        Command Center shows imported presets by file name, so we do the same.
        A preset identical to an existing one (builtin or user) is reused
        silently; the same name with different values opens the usual name
        dialog. Returns the imported names.
        """
        imported = []
        overwritten = []
        added = False
        for path in map(Path, paths):
            try:
                template = astroeq.parse(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as e:
                QMessageBox.warning(self, t("err_title"),
                                    t("err_import", name=path.name, error=e))
                continue
            name = path.stem.strip()
            if self._all_templates().get(name) == template:
                imported.append(name)  # already there, identical: nothing to ask
                continue
            if (not name or name in self._all_templates()
                    or len(name.encode()) > MAX_NAME_BYTES):
                name = self._prompt_new_template_name(suggestion=name or None)
                if name is None:
                    continue
            if name in self._user_templates:
                overwritten.append(name)  # the name dialog confirmed it
            self._user_templates[name] = template
            imported.append(name)
            added = True
        if added:
            try:
                _save_user_templates(self._user_templates)
            except OSError as e:
                QMessageBox.warning(self, t("err_title"), t("err_template_save", error=e))
            self._rematch()
            for name in overwritten:
                self._show_new_values(name)
            self._refresh_meter()
            self._update_apply_enabled()
            self._emit_dirty_if_changed()
        return imported

    def load_into_selected_slot(self, name: str) -> None:
        """Put a template in the selected EQ slot, as if picked in its combo:
        it shows on the meter and stays pending until synced."""
        combo = self.template_combos[self._selected_slot]
        idx = combo.findData(name)
        if idx < 0:
            return
        if idx == combo.currentIndex():
            # Qt emits nothing for the current index: reapply it, or the slot's
            # edited bands would stay while the import reports it loaded.
            self._on_template_combo_changed(self._selected_slot)
        else:
            combo.setCurrentIndex(idx)

    def export_preset(self) -> str | None:
        """Tools menu: save a template as a .astroeq file for Command Center."""
        names = sorted(self._all_templates(), key=str.casefold)
        current = self.template_combos[self._selected_slot].currentData()
        name, ok = QInputDialog.getItem(
            self, t("act_export_preset"), t("dlg_export_label"), names,
            names.index(current) if current in names else 0, False)
        if not ok:
            return None
        default = Path.home() / (name.replace("/", "-") + astroeq.SUFFIX)
        dialog = QFileDialog(self, t("act_export_preset"), str(default), t("filter_astroeq"))
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        # The dialog adds the suffix itself, so its overwrite check sees the
        # name that is actually written.
        dialog.setDefaultSuffix(astroeq.SUFFIX.lstrip("."))
        if not dialog.exec():
            return None
        path = dialog.selectedFiles()[0]
        try:
            self.export_file(name, path)
        except OSError as e:
            QMessageBox.warning(self, t("err_title"), t("err_export", error=e))
            return None
        return name

    def export_file(self, name: str, path) -> None:
        text = astroeq.dump(name, self._all_templates()[name])
        Path(path).write_text(text, encoding="utf-8", newline="")

    def _show_new_values(self, name: str, skip: int | None = None) -> None:
        """Another slot showing the user preset `name`, just redefined, takes its
        new values and needs a Sync, unless it holds unsynced edits of its own."""
        tpl = self._user_templates[name]
        for s, combo in self.template_combos.items():
            if s == skip or combo.currentData() != name or not self._slot_bands[s]:
                continue
            if self._device_templates.get(s) == name:
                continue  # the base already holds the new definition
            if not self._slot_pending[s]:
                self._slot_bands[s] = _values(tpl)
                self._slot_pending[s] = {1, 2, 3, 4, 5}
            self._update_modified(s)

    def _refresh_combos(self, select: dict[int, str]) -> None:
        all_names = sorted(self._all_templates(), key=str.casefold)
        for slot, combo in self.template_combos.items():
            was_blocked = combo.blockSignals(True)
            current = select.get(slot) or combo.currentData()
            listed = [combo.itemData(i) for i in range(combo.count())
                      if combo.itemData(i) != self.ON_DEVICE]
            if listed != all_names:  # the library changed: rebuild the list
                combo.clear()
                for name in all_names:
                    combo.addItem(self._template_icon(name), name, name)
            # A slot with unsynced edits on the base's own values keeps them as
            # its reference, even once a template takes that name (an import):
            # its Sync must not write that template's bandwidths.
            self._sync_device_item(slot, keep=(
                current == self.ON_DEVICE and bool(self._slot_pending[slot])
                and self._device_templates.get(slot) is None))
            if current == self.ON_DEVICE and combo.findData(current) < 0:
                # Its name became a template (an import): show the slot under it
                # instead of falling back to the first template.
                current = self._device_target(slot)
            if current is not None:
                idx = combo.findData(current)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            combo.blockSignals(was_blocked)

    def _sync_device_item(self, slot: int, keep: bool = False) -> None:
        """Show the "<name> (on the base)" entry only while the slot holds a
        preset that matches no template and isn't named after one."""
        combo = self.template_combos[slot]
        idx = combo.findData(self.ON_DEVICE)
        if idx >= 0:
            combo.removeItem(idx)
        data = self._slot_device.get(slot)
        if data is not None and (keep or self._device_target(slot) == self.ON_DEVICE):
            label = t("device_preset", name=data["name"] or t("preset_n", n=slot))
            icon = themes.icon("audio-headset", "audio-headphones")
            combo.insertItem(0, icon, label, self.ON_DEVICE)

    # ------------------------------------------------------ device reads

    def _read_slot(self, slot: int) -> dict | None:
        """Caller must hold ``self._device_lock``."""
        try:
            name = self.device.get_eq_preset_name(slot)
            gain = self.device.get_eq_preset_gain(slot).gain
            bands = {}
            for b in (1, 2, 3, 4, 5):
                fb = self.device.get_eq_preset_freq_and_bw(slot, b)
                bands[b] = (fb.center_freq, fb.bandwidth)
        except Exception:
            return None
        return {"name": name, "gain": gain, "bands": bands}

    # ------------------------------------------------------ helpers

    def _all_templates(self) -> dict:
        return {**_EQ_TEMPLATES, **self._user_templates}

    @staticmethod
    def _template_icon(name: str) -> QIcon:
        if name in _EQ_TEMPLATES:
            return themes.icon("audio-headset", "audio-headphones")
        return themes.icon("emblem-favorite", "starred")

    def _match_template(self, data: dict) -> str | None:
        for name, tpl in self._all_templates().items():
            if (name == data["name"] and tpl["gain"] == data["gain"]
                    and tpl["bands"] == data["bands"]):
                return name
        return None

    def _resolve(self, slot: int) -> tuple[str, dict | None]:
        """The slot's reference and the name a Sync writes for it: the template
        its combo shows, or for "<name> (on the base)" the base's own values.
        Every path goes through here, so a new kind of entry is handled once."""
        name = self.template_combos[slot].currentData()
        if name == self.ON_DEVICE:
            device = self._slot_device.get(slot)
            return (device["name"] if device else ""), device
        return name or "", self._all_templates().get(name)

    def _reference(self, slot: int) -> dict | None:
        """What the slot is compared with, reset to and synced as."""
        return self._resolve(slot)[1]

    def _device_target(self, slot: int) -> str | None:
        """What a slot shows for what the base holds: the template it matches,
        else its name's template, else "<name> (on the base)"; None if unread."""
        data = self._slot_device.get(slot)
        if data is None:
            return None
        if self._device_templates.get(slot):
            return self._device_templates[slot]
        return data["name"] if data["name"] in self._all_templates() else self.ON_DEVICE

    def _rematch(self) -> None:
        """Match what each slot holds on the base against the library again,
        without reading the device: after a reload, a sync, or a library change.
        A slot without unsynced edits shows the template it holds, else its
        name's template, else "<name> (on the base)"; a slot with edits keeps
        the reference it shows."""
        for slot, data in self._slot_device.items():
            self._device_templates[slot] = self._match_template(data) if data else None
        idle = [s for s in self.template_combos if not self._slot_pending[s]]
        self._refresh_combos(select={
            s: self._device_target(s) for s in idle if self._slot_device.get(s)})
        for slot in idle:
            if self._slot_device.get(slot) is None:
                # Unread: show no preset rather than one the slot may not hold.
                combo = self.template_combos[slot]
                was_blocked = combo.blockSignals(True)
                combo.setCurrentIndex(-1)
                combo.blockSignals(was_blocked)
        for slot in self.template_combos:
            self._update_modified(slot)

    def _update_modified(self, slot: int) -> None:
        """Mark the bands that differ from the slot's reference."""
        bands = self._slot_bands[slot]
        ref = self._reference(slot)
        if not bands or ref is None:
            self._slot_modified[slot] = set(range(1, 6)) if bands else set()
            return
        ref_values = _values(ref)
        # With nothing queued the slot is what the base holds, so its own
        # bandwidths count too (set in Command Center, say). Once a Sync is
        # queued, it writes the reference's bandwidths.
        device = None if self._slot_pending[slot] else self._slot_device.get(slot)
        self._slot_modified[slot] = {
            b for b in range(1, 6)
            if bands[b - 1] != ref_values[b - 1]
            or (device is not None and device["bands"][b][1] != ref["bands"][b][1])
        }

    def _refresh_meter(self) -> None:
        slot = self._selected_slot
        self.meter.set_state(
            self._slot_bands.get(slot, []),
            self._slot_modified.get(slot, set()),
        )

    def _update_apply_enabled(self) -> None:
        slot = self._selected_slot
        modified = bool(self._slot_modified.get(slot))
        current_tpl = self.template_combos[slot].currentData()
        self.btn_save_eq.setEnabled(modified and current_tpl in self._user_templates)
        self.btn_apply_eq.setEnabled(modified)
        self.btn_reset_eq.setEnabled(modified)
        for s, btn in self.template_delete_btns.items():
            enabled = self.template_combos[s].currentData() in self._user_templates
            btn.setEnabled(enabled)
            eff = btn.graphicsEffect()
            if eff is not None:
                eff.setOpacity(1.0 if enabled else 0.0)

    def _emit_dirty_if_changed(self, force: bool = False) -> None:
        is_dirty = self.has_pending()
        if force or is_dirty != self._last_dirty:
            self._last_dirty = is_dirty
            self.dirty_changed.emit(is_dirty)
