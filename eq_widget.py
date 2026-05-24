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

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QGraphicsOpacityEffect, QGridLayout,
    QGroupBox, QHBoxLayout, QInputDialog, QMessageBox, QPushButton, QRadioButton,
)

from vendor.eh_fifty import Device

from eq_meter import _EqMeter
from i18n import t
from templates import _EQ_TEMPLATES, _load_user_templates, _save_user_templates


class EqTemplatesWidget(QGroupBox):
    """Self-contained 5-band EQ + 3-slot preset editor."""

    dirty_changed = pyqtSignal(bool)

    def __init__(self, device: Device, lock: threading.RLock, parent=None):
        super().__init__(t("grp_eq_templates"), parent)
        self.device = device
        self._device_lock = lock
        self._loading = False

        self._user_templates: dict[str, dict] = _load_user_templates()
        self._device_templates: dict[int, str | None] = {1: None, 2: None, 3: None}
        self._slot_bands: dict[int, list[tuple[int, int]]] = {1: [], 2: [], 3: []}
        self._slot_modified: dict[int, set[int]] = {1: set(), 2: set(), 3: set()}
        self._slot_pending: dict[int, set[int]] = {1: set(), 2: set(), 3: set()}
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
        l = QGridLayout(self)
        self.template_radio_group = QButtonGroup(self)
        self.template_radio_group.setExclusive(True)
        for row, slot in enumerate((1, 2, 3)):
            radio = QRadioButton(t("lbl_eq_slot", n=slot))
            radio.toggled.connect(lambda checked, s=slot: self._on_slot_radio(s, checked))
            self.template_radio_group.addButton(radio, slot)
            l.addWidget(radio, row, 0)
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
            icon = QIcon.fromTheme("edit-delete") or QIcon.fromTheme("user-trash")
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
            l.addLayout(combo_row, row, 1)
            self.template_combos[slot] = combo
            self.template_radios[slot] = radio
            self.template_delete_btns[slot] = trash
        l.setColumnStretch(0, 0)
        l.setColumnStretch(1, 1)
        self.meter = _EqMeter()
        self.meter.bandModified.connect(self._on_band_modified)
        l.addWidget(self.meter, len(self.template_combos), 0, 1, 2)
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
        l.addLayout(btn_row, len(self.template_combos) + 1, 0, 1, 2)

    # ---------------------------------------------------------- properties

    @property
    def selected_slot(self) -> int:
        return self._selected_slot

    def has_pending(self) -> bool:
        if any(self._slot_pending.values()):
            return True
        if (self._device_active_eq is not None
                and self._selected_slot != self._device_active_eq):
            return True
        return False

    # ----------------------------------------------------- public reload/push

    def reload_under_lock(self, active_eq_preset: int | None) -> None:
        """Re-read all 3 slots from the device and refresh the UI.

        Caller must hold ``self._device_lock``.
        """
        self._loading = True
        try:
            slot_data = {slot: self._read_slot(slot) for slot in self.template_combos}
            for slot, combo in self.template_combos.items():
                data = slot_data[slot]
                if data is None:
                    self._slot_bands[slot] = []
                else:
                    self._slot_bands[slot] = [
                        (data["bands"][b][0], data["gain"][b - 1])
                        for b in range(1, 6)
                    ]
                self._slot_pending[slot].clear()
                all_tpls = self._all_templates()
                detected = self._match_template(data) if data else None
                self._device_templates[slot] = detected
                target = detected
                if target is None and data is not None and data["name"] in all_tpls:
                    target = data["name"]
                idx = combo.findData(target) if target else 0
                if idx < 0:
                    idx = 0
                was_blocked = combo.blockSignals(True)
                combo.setCurrentIndex(idx)
                combo.blockSignals(was_blocked)
                modified: set[int] = set()
                tpl_name = combo.itemData(idx)
                if data is not None and tpl_name in all_tpls:
                    tpl = all_tpls[tpl_name]
                    for b in range(1, 6):
                        if (data["gain"][b - 1] != tpl["gain"][b - 1]
                                or data["bands"][b] != tpl["bands"][b]):
                            modified.add(b)
                self._slot_modified[slot] = modified
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
        all_tpls = self._all_templates()
        user_templates_changed = False
        for slot, combo in self.template_combos.items():
            if not self._slot_pending[slot]:
                continue
            bands = self._slot_bands[slot]
            if not bands:
                continue
            name = combo.currentData() or ""
            prev_tpl = all_tpls.get(name, _EQ_TEMPLATES["MEDIA"])
            gain = [g for (_freq, g) in bands]
            device_bands = {
                b: (bands[b - 1][0], 0 if b in (1, 5) else prev_tpl["bands"][b][1])
                for b in range(1, 6)
            }
            if name in self._user_templates:
                self._user_templates[name] = {
                    "gain": list(gain),
                    "bands": dict(device_bands),
                }
                user_templates_changed = True
                self._slot_modified[slot].clear()
            self.device.set_eq_preset_name(slot, name)
            self.device.set_eq_preset_gain(slot, gain)
            for b, (freq, bw) in device_bands.items():
                self.device.set_eq_preset_freq_and_bw(slot, b, freq, bw)
            data = {"name": name, "gain": gain, "bands": device_bands}
            self._device_templates[slot] = self._match_template(data)
            self._slot_pending[slot].clear()
        if user_templates_changed:
            _save_user_templates(self._user_templates)
        if self._selected_slot != self._device_active_eq:
            self.device.set_active_eq_preset(self._selected_slot)
            self._device_active_eq = self._selected_slot
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
        if self._is_band_off_template(slot, band):
            self._slot_modified[slot].add(band)
        else:
            self._slot_modified[slot].discard(band)
        self._slot_pending[slot].add(band)
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
        all_tpls = self._all_templates()
        self._slot_modified[slot].clear()
        if desired is not None and desired in all_tpls:
            tpl = all_tpls[desired]
            self._slot_bands[slot] = [
                (tpl["bands"][b][0], tpl["gain"][b - 1]) for b in range(1, 6)
            ]
        if desired is not None and desired != self._device_templates.get(slot):
            self._slot_pending[slot] = {1, 2, 3, 4, 5}
        else:
            self._slot_pending[slot].clear()
        if slot == self._selected_slot:
            self._refresh_meter()
        self._update_apply_enabled()
        if self._loading:
            return
        self._emit_dirty_if_changed()

    def _on_reset_templates(self) -> None:
        slot = self._selected_slot
        name = self.template_combos[slot].currentData()
        tpl = self._all_templates().get(name) if name else None
        if tpl is None:
            return
        self._slot_bands[slot] = [
            (tpl["bands"][b][0], tpl["gain"][b - 1]) for b in range(1, 6)
        ]
        self._slot_modified[slot].clear()
        if name != self._device_templates.get(slot):
            self._slot_pending[slot] = {1, 2, 3, 4, 5}
        else:
            self._slot_pending[slot].clear()
        self._refresh_meter()
        self._update_apply_enabled()
        self._emit_dirty_if_changed()

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
        del self._user_templates[name]
        _save_user_templates(self._user_templates)
        affected_slots = [
            s for s, c in self.template_combos.items() if c.currentData() == name
        ]
        all_names = sorted(self._all_templates(), key=str.casefold)
        fallback = all_names[0] if all_names else ""
        self._refresh_combos(select={s: fallback for s in affected_slots})
        for s in affected_slots:
            self._on_template_combo_changed(s)
        self._update_apply_enabled()

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
            prev_name = self.template_combos[slot].currentData()
            prev_tpl = self._all_templates().get(prev_name, _EQ_TEMPLATES["MEDIA"])
            new_bands = {
                b: (bands[b - 1][0], 0 if b in (1, 5) else prev_tpl["bands"][b][1])
                for b in range(1, 6)
            }
            self._user_templates[name] = {"gain": list(gain), "bands": new_bands}
            _save_user_templates(self._user_templates)
            if is_new:
                self._refresh_combos(select={slot: name})
            with self._device_lock:
                self.device.set_eq_preset_name(slot, name)
                self.device.set_eq_preset_gain(slot, gain)
                for b, (freq, bw) in new_bands.items():
                    self.device.set_eq_preset_freq_and_bw(slot, b, freq, bw)
                self.device.save_values()
            self._device_templates[slot] = name
            self._slot_pending[slot].clear()
        except Exception as e:
            QMessageBox.warning(self, t("err_title"), t("err_template_apply", error=e))
            return
        finally:
            QApplication.restoreOverrideCursor()
            btn.setText(t(idle_key))
        # Reload everything to mirror the new device state.
        with self._device_lock:
            self.reload_under_lock(None)

    def _prompt_new_template_name(self) -> str | None:
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

    def _refresh_combos(self, select: dict[int, str] | None = None) -> None:
        select = select or {}
        all_names = sorted(self._all_templates(), key=str.casefold)
        for slot, combo in self.template_combos.items():
            was_blocked = combo.blockSignals(True)
            current = select.get(slot) or combo.currentData()
            combo.clear()
            for name in all_names:
                combo.addItem(self._template_icon(name), name, name)
            if current is not None:
                idx = combo.findData(current)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            combo.blockSignals(was_blocked)

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
            icon = QIcon.fromTheme("audio-headset")
            if icon.isNull():
                icon = QIcon.fromTheme("audio-headphones")
        else:
            icon = QIcon.fromTheme("emblem-favorite")
            if icon.isNull():
                icon = QIcon.fromTheme("starred")
        return icon

    def _match_template(self, data: dict) -> str | None:
        for name, tpl in self._all_templates().items():
            if (name == data["name"] and tpl["gain"] == data["gain"]
                    and tpl["bands"] == data["bands"]):
                return name
        return None

    def _is_band_off_template(self, slot: int, band: int) -> bool:
        combo = self.template_combos[slot]
        name = combo.currentData()
        all_tpls = self._all_templates()
        if name is None or name not in all_tpls:
            return True
        tpl = all_tpls[name]
        bands = self._slot_bands[slot]
        if not bands:
            return False
        freq, gain = bands[band - 1]
        tpl_freq, _tpl_bw = tpl["bands"][band]
        return gain != tpl["gain"][band - 1] or freq != tpl_freq

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
