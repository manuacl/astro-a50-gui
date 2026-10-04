"""Unit tests for the GUI's pure logic.

Run with:
    .venv/bin/python -m unittest tests.py
"""
import json
import os
import shutil
import string
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import astroeq
import gui
import i18n
import settings
import templates
import themes
from base_info_dialog import format_base_info
from eq_widget import EqTemplatesWidget
from vendor import eh_fifty
from vendor.eh_fifty import DeviceInfo, FirmwareVersion


class BuiltinTemplatesTest(unittest.TestCase):
    BANDS = (1, 2, 3, 4, 5)

    def test_five_builtin_templates(self):
        self.assertEqual(
            set(templates._EQ_TEMPLATES),
            {"A50 MOD KIT", "ASTRO", "MEDIA", "PRO", "STUDIO"},
        )

    def test_template_shape(self):
        for name, tpl in templates._EQ_TEMPLATES.items():
            with self.subTest(name=name):
                self.assertIn("gain", tpl)
                self.assertIn("bands", tpl)
                self.assertEqual(len(tpl["gain"]), 5,
                                 f"{name}: gain must have 5 entries")
                self.assertEqual(set(tpl["bands"]), set(self.BANDS),
                                 f"{name}: bands must be {{1..5}}")
                for g in tpl["gain"]:
                    self.assertGreaterEqual(g, -7)
                    self.assertLessEqual(g, 7)
                # Bands 1 and 5 are highpass/lowpass: bandwidth must be 0.
                for edge in (1, 5):
                    _, bw = tpl["bands"][edge]
                    self.assertEqual(bw, 0,
                                     f"{name}: band {edge} must have bw=0")


class UserTemplatesIOTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "user-templates.json"
        self._patcher = mock.patch.object(templates, "USER_TEMPLATES_FILE", self.path)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self.tmpdir.cleanup()

    def test_load_when_file_missing(self):
        self.assertEqual(templates._load_user_templates(), {})

    def test_save_then_load_roundtrip(self):
        tpl = {
            "MyMix": {
                "gain": [3, -2, 0, 5, 2],
                "bands": {1: (100, 0), 2: (400, 4096), 3: (1000, 8192),
                          4: (4000, 2048), 5: (8000, 0)},
            },
        }
        templates._save_user_templates(tpl)
        loaded = templates._load_user_templates()
        self.assertEqual(loaded, tpl)

    def test_load_skips_malformed_entries(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "Valid": {"gain": [0]*5, "bands": {str(b): [100*b, 0] for b in (1,2,3,4,5)}},
            "BadShape": {"foo": "bar"},
            "MissingBands": {"gain": [0]*5},
        }))
        loaded = templates._load_user_templates()
        self.assertIn("Valid", loaded)
        self.assertNotIn("BadShape", loaded)
        self.assertNotIn("MissingBands", loaded)

    def test_load_returns_empty_dict_on_invalid_json(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not json")
        self.assertEqual(templates._load_user_templates(), {})


class BaseInfoDialogTest(unittest.TestCase):
    def test_format_base_info(self):
        lines = format_base_info(
            DeviceInfo(vendor_id=0x9886, product_id=0x002C),
            FirmwareVersion(major=1, minor=2),
            FirmwareVersion(major=3, minor=4),
            [("0x03", b"\x01\x02"), ("0x83(01)", b"\xaa")],
        )
        joined = "\n".join(lines)
        self.assertIn("9886:002c", joined)
        self.assertIn("1.2", joined)
        self.assertIn("3.4", joined)
        self.assertIn("0x03:", joined)
        self.assertIn("0102", joined)
        self.assertIn("0x83(01):", joined)
        self.assertIn("aa", joined)


class AppVersionTest(unittest.TestCase):
    def test_reads_pyproject_version(self):
        text = (Path(gui.__file__).parent / "pyproject.toml").read_text()
        self.assertIn(f'version = "{gui.app_version()}"', text)

    def test_missing_pyproject_gives_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            gui, "SCRIPT_PATH", Path(tmp) / "gui.py"
        ):
            self.assertEqual(gui.app_version(), "?")


def setUpModule():
    # i18n.LANG is detected at import, from the developer's own settings.json
    # and locale: run the tests in English whatever those are.
    patcher = mock.patch.object(i18n, "LANG", "en")
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


def _isolated_settings(tmp: str):
    """Point settings.json at a temp dir, so the user's real preferences never leak in."""
    return mock.patch.object(settings, "SETTINGS_PATH", Path(tmp) / "settings.json")


class I18nTest(unittest.TestCase):
    def test_known_key_in_active_language(self):
        # `t` proxies through the module-level LANG; force FR for the test.
        with mock.patch.object(i18n, "LANG", "fr"):
            self.assertEqual(i18n.t("err_title"), "Erreur")
        with mock.patch.object(i18n, "LANG", "en"):
            self.assertEqual(i18n.t("err_title"), "Error")

    def test_unknown_key_returns_key(self):
        with mock.patch.object(i18n, "LANG", "fr"):
            self.assertEqual(i18n.t("__nonexistent__"), "__nonexistent__")

    def test_kwargs_formatting(self):
        with mock.patch.object(i18n, "LANG", "fr"):
            self.assertEqual(
                i18n.t("msg_eq_set", name="MEDIA"),
                "Preset EQ → MEDIA",
            )

    def test_fr_falls_back_to_en_for_missing_translation(self):
        # Patch FR table so a key only exists in EN.
        with mock.patch.dict(i18n.TRANSLATIONS["fr"], clear=False) as _:
            i18n.TRANSLATIONS["fr"].pop("err_title", None)
            with mock.patch.object(i18n, "LANG", "fr"):
                self.assertEqual(i18n.t("err_title"), "Error")
        # Restore — mock.patch.dict resets dict; safe.

    def test_every_language_has_the_same_keys_as_en(self):
        reference = set(i18n.TRANSLATIONS["en"])
        for lang, strings in i18n.TRANSLATIONS.items():
            with self.subTest(lang=lang):
                self.assertEqual(set(strings), reference)

    def test_placeholders_match_en(self):
        # A misspelled or extra placeholder raises KeyError at runtime; a missing
        # one silently drops the value from the message.
        def fields(text: str) -> set[str]:
            return {f for _, f, _, _ in string.Formatter().parse(text) if f}

        for lang, strings in i18n.TRANSLATIONS.items():
            for key, text in strings.items():
                with self.subTest(lang=lang, key=key):
                    self.assertEqual(fields(text), fields(i18n.TRANSLATIONS["en"][key]))

    def test_spanish_is_detected(self):
        with mock.patch.object(i18n, "LANG", "es"):
            self.assertEqual(i18n.t("btn_refresh"), "Actualizar")
            self.assertEqual(i18n.t("gate_tournament"), "TORNEO")
        with mock.patch.dict(os.environ, {"A50_LANG": "es"}):
            self.assertEqual(i18n._detect_lang(), "es")
        with (
            tempfile.TemporaryDirectory() as tmp,
            _isolated_settings(tmp),
            mock.patch.dict(os.environ, {"A50_LANG": ""}),
            mock.patch.object(i18n, "QLocale") as qlocale,
        ):
            qlocale.system.return_value.name.return_value = "es_ES"
            self.assertEqual(i18n._detect_lang(), "es")
            qlocale.system.return_value.name.return_value = "de_DE"
            self.assertEqual(i18n._detect_lang(), "en")  # unsupported: English

    def test_gate_labels_fall_back_to_the_mode_name(self):
        with mock.patch.object(i18n, "LANG", "es"):
            self.assertEqual(i18n.gate_label("HOME"), "CASA")
            self.assertEqual(i18n.gate_label("NEW_MODE"), "NEW_MODE")


class EqWidgetHasPendingTest(unittest.TestCase):
    """Tests for EqTemplatesWidget.has_pending() — the dirty signal used by
    the main window's sync button.

    Uses ``__new__`` to bypass the Qt-based __init__; we only set the
    attributes the method reads.
    """

    def _make(
        self,
        slot_pending: dict[int, set[int]] | None = None,
        selected_slot: int = 1,
        device_active: int | None = 1,
    ):
        widget = EqTemplatesWidget.__new__(EqTemplatesWidget)
        widget._slot_device = {1: None, 2: None, 3: None}
        widget._slot_pending = slot_pending or {1: set(), 2: set(), 3: set()}
        widget._selected_slot = selected_slot
        widget._device_active_eq = device_active
        return widget

    def test_clean_state(self):
        widget = self._make()
        self.assertFalse(widget.has_pending())

    def test_pending_band(self):
        widget = self._make(slot_pending={1: {3}, 2: set(), 3: set()})
        self.assertTrue(widget.has_pending())

    def test_radio_differs_from_device(self):
        widget = self._make(selected_slot=2, device_active=1)
        self.assertTrue(widget.has_pending())

    def test_radio_matches_device(self):
        widget = self._make(selected_slot=2, device_active=2)
        self.assertFalse(widget.has_pending())

    def test_no_device_active_yet(self):
        # Before reload, _device_active_eq is None and changing the radio
        # should not flag dirty (we don't know the device state).
        widget = self._make(selected_slot=2, device_active=None)
        self.assertFalse(widget.has_pending())


class EqWidgetMatchTemplateTest(unittest.TestCase):
    def _make(self, user_templates: dict | None = None):
        widget = EqTemplatesWidget.__new__(EqTemplatesWidget)
        widget._slot_device = {1: None, 2: None, 3: None}
        widget._user_templates = user_templates or {}
        return widget

    def test_match_builtin(self):
        widget = self._make()
        media = templates._EQ_TEMPLATES["MEDIA"]
        data = {"name": "MEDIA", "gain": media["gain"], "bands": media["bands"]}
        self.assertEqual(widget._match_template(data), "MEDIA")

    def test_no_match_on_gain_diff(self):
        widget = self._make()
        media = templates._EQ_TEMPLATES["MEDIA"]
        gain = list(media["gain"])
        gain[2] = gain[2] + 1
        data = {"name": "MEDIA", "gain": gain, "bands": media["bands"]}
        self.assertIsNone(widget._match_template(data))

    def test_match_user_template(self):
        user_tpl = {
            "gain": [1, 2, 3, 4, 5],
            "bands": {1: (100, 0), 2: (400, 4096), 3: (1000, 8192),
                      4: (4000, 2048), 5: (8000, 0)},
        }
        widget = self._make(user_templates={"MyMix": user_tpl})
        data = {"name": "MyMix", "gain": user_tpl["gain"], "bands": user_tpl["bands"]}
        self.assertEqual(widget._match_template(data), "MyMix")


class _MockCombo:
    """Stands in for QComboBox in tests that bypass Qt.

    Supports the subset of the QComboBox API that the EQ widget calls:
    ``currentData``, ``findData``, ``itemData``, ``setCurrentIndex``,
    ``blockSignals``. Optionally fires a "signal handler" callback when
    ``setCurrentIndex`` is invoked while signals are unblocked — used by
    the reload regression test to detect that signals are properly
    suppressed around `combo.setCurrentIndex()` during reload.
    """
    def __init__(self, items=None, current_data=None, signal_handler=None):
        # items: list of (display_name, data); kept in insertion order
        if items is None and current_data is not None:
            items = [(current_data, current_data)]
        self._items = list(items) if items else []
        self._current = 0
        if current_data is not None:
            for i, (_, d) in enumerate(self._items):
                if d == current_data:
                    self._current = i
                    break
        self._signals_blocked = False
        self._signal_handler = signal_handler
        self.set_current_index_log = []  # (idx, was_blocked)

    def currentData(self):
        if not self._items or self._current < 0:
            return None
        return self._items[self._current][1]

    def findData(self, data):
        for i, (_, d) in enumerate(self._items):
            if d == data:
                return i
        return -1

    def itemData(self, idx):
        if 0 <= idx < len(self._items):
            return self._items[idx][1]
        return None

    def itemText(self, idx):
        if 0 <= idx < len(self._items):
            return self._items[idx][0]
        return None

    def insertItem(self, idx, _icon, text, data):
        self._items.insert(idx, (text, data))
        if self._items and idx <= self._current and len(self._items) > 1:
            self._current += 1

    def clear(self):
        self._items = []
        self._current = -1

    def addItem(self, _icon, text, data):
        self._items.append((text, data))
        # Qt selects the first item added to an empty combo.
        self._current = max(self._current, 0)

    def removeItem(self, idx):
        if 0 <= idx < len(self._items):
            del self._items[idx]
            if idx < self._current or self._current >= len(self._items):
                self._current = max(self._current - 1, 0)

    def setCurrentIndex(self, idx):
        if 0 <= idx < len(self._items) or idx == -1:
            self._current = idx
        self.set_current_index_log.append((idx, self._signals_blocked))
        if not self._signals_blocked and self._signal_handler is not None:
            self._signal_handler()

    def count(self):
        return len(self._items)

    def blockSignals(self, block):
        prev = self._signals_blocked
        self._signals_blocked = block
        return prev


class _MockRadio:
    """Stands in for QRadioButton — only setChecked is exercised."""
    def __init__(self):
        self.checked = False

    def setChecked(self, value):
        self.checked = value


# A preset set up in Astro Command Center, as the base reports it: no builtin
# or user template matches it (issue #11).
_ARCTURUS_ON_BASE = {
    "name": "ARCTURUS",
    "gain": [5, 6, 4, 6, 5],
    "bands": {1: (100, 0), 2: (1775, 9011), 3: (4664, 8192), 4: (8210, 9011), 5: (11125, 0)},
}


class EqWidgetPushPendingTest(unittest.TestCase):
    """Tests for EqTemplatesWidget.push_pending_to_device() — the sync
    flow that writes the visible state to the headset and (for user
    presets) overwrites their local definition.

    Bypasses Qt via ``__new__``; only the attributes/methods the function
    reads are populated. ``_save_user_templates`` is patched at the
    module level to avoid touching disk.
    """

    def _make(
        self,
        *,
        user_templates: dict | None = None,
        device_templates: dict[int, str | None] | None = None,
        slot_bands: dict[int, list[tuple[int, int]]] | None = None,
        slot_modified: dict[int, set[int]] | None = None,
        slot_pending: dict[int, set[int]] | None = None,
        combos: dict[int, str | None] | None = None,
        selected_slot: int = 1,
        device_active: int | None = 1,
    ):
        widget = EqTemplatesWidget.__new__(EqTemplatesWidget)
        widget._slot_device = {1: None, 2: None, 3: None}
        widget._user_templates = user_templates or {}
        widget._device_templates = device_templates or {1: None, 2: None, 3: None}
        widget._slot_bands = slot_bands or {1: [], 2: [], 3: []}
        widget._slot_modified = slot_modified or {1: set(), 2: set(), 3: set()}
        widget._slot_pending = slot_pending or {1: set(), 2: set(), 3: set()}
        widget._selected_slot = selected_slot
        widget._device_active_eq = device_active
        widget._last_dirty = False
        widget._refresh_meter = lambda: None
        widget._update_apply_enabled = lambda: None
        widget._emit_dirty_if_changed = lambda: None
        widget.device = mock.MagicMock()
        widget.template_combos = {
            slot: _MockCombo(current_data=(combos or {}).get(slot))
            for slot in (1, 2, 3)
        }
        return widget

    @staticmethod
    def _bands_from_template(name: str) -> list[tuple[int, int]]:
        tpl = templates._EQ_TEMPLATES[name]
        return [(tpl["bands"][b][0], tpl["gain"][b - 1]) for b in range(1, 6)]

    def test_no_pending_skips_all_slots(self):
        widget = self._make(combos={1: "MEDIA", 2: "PRO", 3: "ASTRO"})
        with mock.patch("eq_widget._save_user_templates") as save:
            widget.push_pending_to_device()
        widget.device.set_eq_preset_name.assert_not_called()
        widget.device.set_eq_preset_gain.assert_not_called()
        widget.device.set_eq_preset_freq_and_bw.assert_not_called()
        widget.device.set_active_eq_preset.assert_not_called()
        save.assert_not_called()

    def test_active_slot_change_pushes_only_active(self):
        widget = self._make(
            combos={1: "MEDIA", 2: "PRO", 3: "ASTRO"},
            selected_slot=2, device_active=1,
        )
        widget.push_pending_to_device()
        widget.device.set_eq_preset_name.assert_not_called()
        widget.device.set_active_eq_preset.assert_called_once_with(2)
        self.assertEqual(widget._device_active_eq, 2)

    def test_modified_builtin_pushes_as_is_without_local_save(self):
        bands = self._bands_from_template("MEDIA")
        bands[2] = (bands[2][0], 5)  # band 3 gain bumped
        widget = self._make(
            combos={1: "MEDIA", 2: None, 3: None},
            slot_bands={1: bands, 2: [], 3: []},
            slot_modified={1: {3}, 2: set(), 3: set()},
            slot_pending={1: {3}, 2: set(), 3: set()},
            device_templates={1: "MEDIA", 2: None, 3: None},
        )
        with mock.patch("eq_widget._save_user_templates") as save:
            widget.push_pending_to_device()
        # Builtin library untouched, no disk write.
        self.assertEqual(widget._user_templates, {})
        save.assert_not_called()
        # Device received the modified bands under the builtin's name.
        widget.device.set_eq_preset_name.assert_called_once_with(1, "MEDIA")
        expected_gain = [g for (_f, g) in bands]
        widget.device.set_eq_preset_gain.assert_called_once_with(1, expected_gain)
        self.assertEqual(widget.device.set_eq_preset_freq_and_bw.call_count, 5)
        # Slot no longer "matches" any template → off-template orange stays.
        self.assertIsNone(widget._device_templates[1])
        self.assertEqual(widget._slot_modified[1], {3})
        self.assertEqual(widget._slot_pending[1], set())

    def test_modified_user_preset_overwrites_local_and_pushes(self):
        media = templates._EQ_TEMPLATES["MEDIA"]
        user_tpl = {"gain": list(media["gain"]),
                    "bands": dict(media["bands"])}
        bands = [(media["bands"][b][0], media["gain"][b - 1]) for b in range(1, 6)]
        bands[2] = (bands[2][0], 4)  # user edits band 3
        widget = self._make(
            user_templates={"MyMix": user_tpl},
            combos={1: "MyMix", 2: None, 3: None},
            slot_bands={1: bands, 2: [], 3: []},
            slot_modified={1: {3}, 2: set(), 3: set()},
            slot_pending={1: {3}, 2: set(), 3: set()},
            device_templates={1: "MyMix", 2: None, 3: None},
        )
        with mock.patch("eq_widget._save_user_templates") as save:
            widget.push_pending_to_device()
        # User preset overwritten with the new band values, persisted once.
        self.assertEqual(widget._user_templates["MyMix"]["gain"][2], 4)
        save.assert_called_once_with(widget._user_templates)
        # Device received the user preset's new state.
        widget.device.set_eq_preset_name.assert_called_once_with(1, "MyMix")
        # Slot now matches the just-saved user preset → no orange, no pending.
        self.assertEqual(widget._device_templates[1], "MyMix")
        self.assertEqual(widget._slot_modified[1], set())
        self.assertEqual(widget._slot_pending[1], set())

    def test_combo_changed_to_other_template_pushes_template_values(self):
        # Simulates the state right after _on_template_combo_changed sets the
        # slot to PRO: _slot_bands holds PRO values, _slot_pending is full,
        # _slot_modified is empty.
        widget = self._make(
            combos={1: "PRO", 2: None, 3: None},
            slot_bands={1: self._bands_from_template("PRO"), 2: [], 3: []},
            slot_modified={1: set(), 2: set(), 3: set()},
            slot_pending={1: {1, 2, 3, 4, 5}, 2: set(), 3: set()},
            device_templates={1: "MEDIA", 2: None, 3: None},
        )
        with mock.patch("eq_widget._save_user_templates"):
            widget.push_pending_to_device()
        widget.device.set_eq_preset_name.assert_called_once_with(1, "PRO")
        pro = templates._EQ_TEMPLATES["PRO"]
        widget.device.set_eq_preset_gain.assert_called_once_with(1, list(pro["gain"]))
        # Slot now matches PRO cleanly.
        self.assertEqual(widget._device_templates[1], "PRO")
        self.assertEqual(widget._slot_pending[1], set())

    def test_single_save_for_multiple_user_preset_slots(self):
        media = templates._EQ_TEMPLATES["MEDIA"]
        user_tpl = {"gain": list(media["gain"]),
                    "bands": dict(media["bands"])}
        bands1 = [(media["bands"][b][0], media["gain"][b - 1]) for b in range(1, 6)]
        bands1[2] = (bands1[2][0], 3)
        bands2 = [(media["bands"][b][0], media["gain"][b - 1]) for b in range(1, 6)]
        bands2[1] = (bands2[1][0], -2)
        widget = self._make(
            user_templates={"Mix1": dict(user_tpl), "Mix2": dict(user_tpl)},
            combos={1: "Mix1", 2: "Mix2", 3: None},
            slot_bands={1: bands1, 2: bands2, 3: []},
            slot_modified={1: {3}, 2: {2}, 3: set()},
            slot_pending={1: {3}, 2: {2}, 3: set()},
            device_templates={1: "Mix1", 2: "Mix2", 3: None},
        )
        with mock.patch("eq_widget._save_user_templates") as save:
            widget.push_pending_to_device()
        # Both user presets updated, but a single disk write.
        self.assertEqual(widget._user_templates["Mix1"]["gain"][2], 3)
        self.assertEqual(widget._user_templates["Mix2"]["gain"][1], -2)
        save.assert_called_once()

    def test_empty_slot_bands_skipped_even_if_pending(self):
        # Defensive: if a slot somehow has pending but no bands data
        # (device read failure during reload), don't crash and don't push.
        widget = self._make(
            combos={1: "MEDIA", 2: None, 3: None},
            slot_pending={1: {1}, 2: set(), 3: set()},
            slot_bands={1: [], 2: [], 3: []},
        )
        with mock.patch("eq_widget._save_user_templates"):
            widget.push_pending_to_device()
        widget.device.set_eq_preset_name.assert_not_called()

    def test_unknown_device_preset_keeps_its_name_and_bandwidths(self):
        # Issue #11: syncing an edited "<name> (on the base)" slot keeps the
        # device's own name and bandwidths and leaves the library alone.
        widget = self._make(
            user_templates={"MINE": dict(templates._EQ_TEMPLATES["PRO"])},
            combos={1: EqTemplatesWidget.ON_DEVICE},
            slot_bands={1: [(100, 5), (1775, 7), (4664, 4), (8210, 6), (11125, 5)], 2: [], 3: []},
            slot_pending={1: {2}, 2: set(), 3: set()},
        )
        widget._slot_device[1] = dict(_ARCTURUS_ON_BASE)
        with mock.patch("eq_widget._save_user_templates") as save:
            widget.push_pending_to_device()
        widget.device.set_eq_preset_name.assert_called_once_with(1, "ARCTURUS")
        widget.device.set_eq_preset_gain.assert_called_once_with(1, [5, 7, 4, 6, 5])
        widget.device.set_eq_preset_freq_and_bw.assert_any_call(1, 2, 1775, 9011)
        widget.device.set_eq_preset_freq_and_bw.assert_any_call(1, 3, 4664, 8192)
        save.assert_not_called()
        self.assertEqual(widget._slot_device[1]["gain"], [5, 7, 4, 6, 5])


def _reload_widget(device_data: dict[int, dict | None], *, user_templates: dict | None = None):
    """An EQ widget without Qt whose device answers with `device_data`
    (None: that slot's read fails), for reload_under_lock and what follows."""
    widget = EqTemplatesWidget.__new__(EqTemplatesWidget)
    widget._slot_device = {1: None, 2: None, 3: None}
    widget._user_templates = user_templates or {}
    widget._device_templates = {1: None, 2: None, 3: None}
    widget._slot_bands = {1: [], 2: [], 3: []}
    widget._slot_modified = {1: set(), 2: set(), 3: set()}
    widget._slot_pending = {1: set(), 2: set(), 3: set()}
    widget._selected_slot = 1
    widget._device_active_eq = None
    widget._last_dirty = False
    widget._loading = False
    widget._refresh_meter = lambda: None
    widget._update_apply_enabled = lambda: None
    widget._emit_dirty_if_changed = lambda: None
    widget.device = mock.MagicMock()

    def get_name(slot):
        return device_data[slot]["name"]

    def get_gain(slot):
        obj = mock.MagicMock()
        obj.gain = device_data[slot]["gain"]
        return obj

    def get_fb(slot, band):
        obj = mock.MagicMock()
        freq, bw = device_data[slot]["bands"][band]
        obj.center_freq = freq
        obj.bandwidth = bw
        return obj

    widget.device.get_eq_preset_name.side_effect = get_name
    widget.device.get_eq_preset_gain.side_effect = get_gain
    widget.device.get_eq_preset_freq_and_bw.side_effect = get_fb

    all_names = sorted(
        list(templates._EQ_TEMPLATES) + list(widget._user_templates),
        key=str.casefold,
    )
    items = [(n, n) for n in all_names]
    widget.template_combos = {}
    for slot in (1, 2, 3):
        combo = _MockCombo(items=items)
        combo._signal_handler = (
            lambda s=slot, w=widget: w._on_template_combo_changed(s)
        )
        widget.template_combos[slot] = combo
    widget.template_radios = {slot: _MockRadio() for slot in (1, 2, 3)}
    return widget


class EqWidgetReloadUnderLockTest(unittest.TestCase):
    """Regression tests for ``reload_under_lock``.

    Key invariant: when reloading, the combo's ``setCurrentIndex`` must
    happen with signals blocked, otherwise ``_on_template_combo_changed``
    fires, overwrites ``_slot_bands`` with template values and populates
    ``_slot_pending`` with all 5 bands. That populates ``_last_dirty=True``
    during the final ``_emit_dirty_if_changed()`` — but the gui ignores
    that emission (still inside ``reload_all``'s ``_loading=True``). The
    EQ widget and the gui then desync, and any subsequent band edit
    short-circuits inside ``_emit_dirty_if_changed`` (``_last_dirty``
    already True → no signal), so the "Synchronisé" button never flips
    to orange.

    The mock combo here invokes its attached "signal handler" when
    ``setCurrentIndex`` is called while signals are unblocked, so if the
    fix is regressed the test fails loudly.
    """

    def _make_widget(self, device_data, *, user_templates=None):
        return _reload_widget(device_data, user_templates=user_templates)

    def test_modified_builtin_reload_leaves_pending_empty(self):
        # Regression: previously _slot_pending[1] ended up as {1,2,3,4,5}.
        media = templates._EQ_TEMPLATES["MEDIA"]
        pro = templates._EQ_TEMPLATES["PRO"]
        astro = templates._EQ_TEMPLATES["ASTRO"]
        modified_gain = list(media["gain"])
        modified_gain[2] = max(-7, min(7, modified_gain[2] + 3))
        device_data = {
            1: {"name": "MEDIA", "gain": modified_gain,
                "bands": dict(media["bands"])},
            2: {"name": "PRO", "gain": list(pro["gain"]),
                "bands": dict(pro["bands"])},
            3: {"name": "ASTRO", "gain": list(astro["gain"]),
                "bands": dict(astro["bands"])},
        }
        widget = self._make_widget(device_data)
        widget.reload_under_lock(active_eq_preset=1)
        self.assertEqual(widget._slot_pending[1], set(),
                         "modified builtin must not populate _slot_pending")
        # Device values preserved (combo signal didn't overwrite them).
        self.assertEqual(widget._slot_bands[1][2][1], modified_gain[2])
        # Off-template band flagged so the meter renders it orange.
        self.assertEqual(widget._slot_modified[1], {3})
        self.assertIsNone(widget._device_templates[1])
        # has_pending mirrors the GUI's expected clean state.
        self.assertFalse(widget.has_pending())

    def test_reload_blocks_combo_signals_around_set_current_index(self):
        media = templates._EQ_TEMPLATES["MEDIA"]
        device_data = {
            s: {"name": "MEDIA", "gain": list(media["gain"]),
                "bands": dict(media["bands"])}
            for s in (1, 2, 3)
        }
        widget = self._make_widget(device_data)
        widget.reload_under_lock(active_eq_preset=1)
        for slot, combo in widget.template_combos.items():
            self.assertTrue(combo.set_current_index_log,
                            f"slot {slot}: setCurrentIndex never called")
            for idx, was_blocked in combo.set_current_index_log:
                self.assertTrue(
                    was_blocked,
                    f"slot {slot}: setCurrentIndex({idx}) fired with "
                    "signals unblocked — _on_template_combo_changed would "
                    "have desynced state during reload",
                )

    def test_clean_builtin_reload_matches_template_no_modified_flag(self):
        media = templates._EQ_TEMPLATES["MEDIA"]
        device_data = {
            s: {"name": "MEDIA", "gain": list(media["gain"]),
                "bands": dict(media["bands"])}
            for s in (1, 2, 3)
        }
        widget = self._make_widget(device_data)
        widget.reload_under_lock(active_eq_preset=2)
        for slot in (1, 2, 3):
            self.assertEqual(widget._slot_pending[slot], set())
            self.assertEqual(widget._slot_modified[slot], set())
            self.assertEqual(widget._device_templates[slot], "MEDIA")
        self.assertEqual(widget._device_active_eq, 2)
        self.assertEqual(widget._selected_slot, 2)
        self.assertFalse(widget.has_pending())

    def test_unknown_device_preset_shows_its_own_name(self):
        # Issue #11: the slot shows "<name> (on the base)", not the first
        # template, and its bands aren't flagged against an unrelated one.
        media = templates._EQ_TEMPLATES["MEDIA"]
        device_data = {
            1: dict(_ARCTURUS_ON_BASE),
            2: {"name": "MEDIA", "gain": list(media["gain"]), "bands": dict(media["bands"])},
            3: dict(_ARCTURUS_ON_BASE, name=""),
        }
        widget = self._make_widget(device_data)
        widget.reload_under_lock(active_eq_preset=1)
        combo = widget.template_combos[1]
        self.assertEqual(combo.currentData(), EqTemplatesWidget.ON_DEVICE)
        self.assertEqual(combo.itemText(0), "ARCTURUS (on the base)")
        self.assertEqual(widget._slot_modified[1], set())
        self.assertEqual(widget._slot_pending[1], set())
        # A matched slot gets no extra entry; a nameless one is labelled by slot.
        self.assertEqual(widget.template_combos[2].findData(EqTemplatesWidget.ON_DEVICE), -1)
        self.assertEqual(widget.template_combos[3].itemText(0), "Preset 3 (on the base)")

    def test_device_entry_restores_the_base_values(self):
        widget = self._make_widget({s: dict(_ARCTURUS_ON_BASE) for s in (1, 2, 3)})
        widget.reload_under_lock(active_eq_preset=1)
        combo = widget.template_combos[1]
        original = list(widget._slot_bands[1])
        combo.setCurrentIndex(combo.findData("MEDIA"))
        self.assertEqual(widget._slot_pending[1], {1, 2, 3, 4, 5})
        combo.setCurrentIndex(combo.findData(EqTemplatesWidget.ON_DEVICE))
        self.assertEqual(widget._slot_bands[1], original)
        self.assertEqual(widget._slot_pending[1], set())
        # Editing a band flags it against the base's own values; undoing it clears that.
        widget._on_band_modified(2, 7)
        self.assertIn(2, widget._slot_modified[1])
        widget._on_band_modified(2, 6)
        self.assertNotIn(2, widget._slot_modified[1])


class EqWidgetHandlersTest(unittest.TestCase):
    """Unit tests for ``_on_band_modified`` and ``_on_template_combo_changed``
    in isolation — to lock down the per-event state transitions used by the
    dirty signal."""

    def _make(self, *, combo_data: str | None = "MEDIA",
              device_template: str | None = "MEDIA",
              selected_slot: int = 1,
              user_templates: dict | None = None):
        media = templates._EQ_TEMPLATES["MEDIA"]
        widget = EqTemplatesWidget.__new__(EqTemplatesWidget)
        widget._slot_device = {1: None, 2: None, 3: None}
        widget._user_templates = user_templates or {}
        widget._device_templates = {1: device_template, 2: None, 3: None}
        widget._slot_bands = {
            1: [(media["bands"][b][0], media["gain"][b - 1])
                for b in range(1, 6)],
            2: [],
            3: [],
        }
        widget._slot_modified = {1: set(), 2: set(), 3: set()}
        widget._slot_pending = {1: set(), 2: set(), 3: set()}
        widget._selected_slot = selected_slot
        widget._device_active_eq = selected_slot
        widget._last_dirty = False
        widget._loading = False
        widget._refresh_meter = lambda: None
        widget._update_apply_enabled = lambda: None
        widget._emit_dirty_if_changed = lambda: None
        widget.device = mock.MagicMock()
        widget.template_combos = {
            1: _MockCombo(current_data=combo_data),
            2: _MockCombo(current_data=None),
            3: _MockCombo(current_data=None),
        }
        return widget

    def test_band_modified_off_template_marks_modified_and_pending(self):
        widget = self._make()
        widget._on_band_modified(3, 5)
        media = templates._EQ_TEMPLATES["MEDIA"]
        self.assertEqual(widget._slot_bands[1][2][1], 5)
        self.assertEqual(widget._slot_bands[1][2][0], media["bands"][3][0])
        self.assertIn(3, widget._slot_modified[1])
        self.assertIn(3, widget._slot_pending[1])
        self.assertTrue(widget.has_pending())

    def test_band_modified_back_to_template_clears_modified_keeps_pending(self):
        widget = self._make()
        widget._slot_modified[1] = {3}
        widget._slot_pending[1] = {3}
        media = templates._EQ_TEMPLATES["MEDIA"]
        # Drag it back to the template's gain for band 3.
        widget._on_band_modified(3, media["gain"][2])
        self.assertNotIn(3, widget._slot_modified[1])
        # Still pending (we touched the band — needs a sync).
        self.assertIn(3, widget._slot_pending[1])

    def test_band_modified_no_bands_returns_silently(self):
        widget = self._make()
        widget._slot_bands[1] = []
        widget._on_band_modified(3, 5)
        self.assertEqual(widget._slot_pending[1], set())
        self.assertEqual(widget._slot_modified[1], set())

    def test_band_modified_skipped_while_loading(self):
        widget = self._make()
        widget._loading = True
        widget._on_band_modified(3, 5)
        self.assertEqual(widget._slot_pending[1], set())

    def test_combo_change_to_other_template_pulls_template_values_and_pendings_all(self):
        widget = self._make(combo_data="PRO", device_template="MEDIA")
        widget._slot_modified[1] = {2}  # stale orange from previous template
        widget._on_template_combo_changed(1)
        pro = templates._EQ_TEMPLATES["PRO"]
        expected = [(pro["bands"][b][0], pro["gain"][b - 1])
                    for b in range(1, 6)]
        self.assertEqual(widget._slot_bands[1], expected)
        self.assertEqual(widget._slot_pending[1], {1, 2, 3, 4, 5})
        self.assertEqual(widget._slot_modified[1], set())
        self.assertTrue(widget.has_pending())

    def test_combo_change_back_to_device_template_clears_pending(self):
        widget = self._make(combo_data="MEDIA", device_template="MEDIA")
        widget._slot_pending[1] = {1, 2, 3, 4, 5}  # was pending from a prior change
        widget._on_template_combo_changed(1)
        self.assertEqual(widget._slot_pending[1], set())
        self.assertEqual(widget._slot_modified[1], set())


class EqWidgetPersistAndPushTest(unittest.TestCase):
    """Tests for ``_persist_and_push`` — the Save / Create-preset flow."""

    def _make(self, *, user_templates: dict | None = None,
              combo_data: str = "MEDIA"):
        import threading as _threading
        media = templates._EQ_TEMPLATES["MEDIA"]
        widget = EqTemplatesWidget.__new__(EqTemplatesWidget)
        widget._slot_device = {1: None, 2: None, 3: None}
        widget._user_templates = dict(user_templates or {})
        widget._device_templates = {1: combo_data, 2: None, 3: None}
        widget._slot_bands = {
            1: [(media["bands"][b][0], media["gain"][b - 1])
                for b in range(1, 6)],
            2: [],
            3: [],
        }
        widget._slot_modified = {1: {3}, 2: set(), 3: set()}
        widget._slot_pending = {1: {3}, 2: set(), 3: set()}
        widget._selected_slot = 1
        widget._device_active_eq = 1
        widget._last_dirty = True
        widget._loading = False
        widget._device_lock = _threading.RLock()
        widget._refresh_combos = lambda **kw: None
        widget.reload_under_lock = lambda *_a, **_k: None
        widget._refresh_meter = lambda: None
        widget._update_apply_enabled = lambda: None
        widget._emit_dirty_if_changed = lambda: None
        widget.device = mock.MagicMock()
        widget.template_combos = {
            1: _MockCombo(current_data=combo_data),
            2: _MockCombo(current_data=None),
            3: _MockCombo(current_data=None),
        }
        # Bump band 3 so there's something to save.
        widget._slot_bands[1][2] = (widget._slot_bands[1][2][0], 4)
        return widget

    def test_create_new_user_preset_saves_pushes_and_clears_pending(self):
        widget = self._make()
        btn = mock.MagicMock()
        with mock.patch("eq_widget.QApplication"), \
             mock.patch("eq_widget._save_user_templates") as save:
            widget._persist_and_push(
                1, "NewMix", is_new=True, btn=btn,
                busy_key="btn_apply_busy", idle_key="btn_apply",
            )
        # User templates library updated and persisted exactly once.
        self.assertIn("NewMix", widget._user_templates)
        self.assertEqual(widget._user_templates["NewMix"]["gain"][2], 4)
        save.assert_called_once_with(widget._user_templates)
        # Device received name + gain + 5 bands + save_values.
        widget.device.set_eq_preset_name.assert_called_once_with(1, "NewMix")
        widget.device.set_eq_preset_gain.assert_called_once()
        self.assertEqual(widget.device.set_eq_preset_freq_and_bw.call_count, 5)
        widget.device.save_values.assert_called_once()
        # State reset for that slot.
        self.assertEqual(widget._device_templates[1], "NewMix")
        self.assertEqual(widget._slot_pending[1], set())

    def test_update_existing_user_preset_overwrites_in_place(self):
        existing = {"gain": [0, 0, 0, 0, 0],
                    "bands": dict(templates._EQ_TEMPLATES["MEDIA"]["bands"])}
        widget = self._make(user_templates={"MyMix": existing},
                            combo_data="MyMix")
        btn = mock.MagicMock()
        with mock.patch("eq_widget.QApplication"), \
             mock.patch("eq_widget._save_user_templates") as save:
            widget._persist_and_push(
                1, "MyMix", is_new=False, btn=btn,
                busy_key="btn_save_eq_busy", idle_key="btn_save_eq",
            )
        self.assertEqual(widget._user_templates["MyMix"]["gain"][2], 4)
        save.assert_called_once()
        widget.device.set_eq_preset_name.assert_called_once_with(1, "MyMix")
        widget.device.save_values.assert_called_once()
        self.assertEqual(widget._device_templates[1], "MyMix")

    def test_persist_and_push_uses_prev_template_bandwidths(self):
        widget = self._make(combo_data="PRO")
        btn = mock.MagicMock()
        with mock.patch("eq_widget.QApplication"), \
             mock.patch("eq_widget._save_user_templates"):
            widget._persist_and_push(
                1, "FromPRO", is_new=True, btn=btn,
                busy_key="btn_apply_busy", idle_key="btn_apply",
            )
        pro = templates._EQ_TEMPLATES["PRO"]
        saved_bands = widget._user_templates["FromPRO"]["bands"]
        # Bands 1 and 5 are shelf filters: bandwidth always 0.
        self.assertEqual(saved_bands[1][1], 0)
        self.assertEqual(saved_bands[5][1], 0)
        # Bands 2-4 inherit PRO's bandwidths.
        for b in (2, 3, 4):
            self.assertEqual(saved_bands[b][1], pro["bands"][b][1])


class SettingsTest(unittest.TestCase):
    def test_roundtrip_and_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmp, _isolated_settings(tmp):
            self.assertEqual(settings.get("theme", "auto"), "auto")
            settings.put("theme", "dark")
            self.assertEqual(settings.get("theme"), "dark")
            settings.SETTINGS_PATH.write_text("{not json")
            self.assertEqual(settings.load(), {})

    def test_wrong_types_fall_back_to_the_default(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            _isolated_settings(tmp),
            mock.patch.dict(os.environ, {"A50_LANG": ""}),
            mock.patch.object(i18n, "QLocale") as qlocale,
        ):
            settings.SETTINGS_PATH.write_text('{"language": ["es"], "theme": 3}')
            self.assertEqual(settings.get("language", "auto"), "auto")
            self.assertEqual(settings.get("theme", "auto"), "auto")
            qlocale.system.return_value.name.return_value = "fr_FR"
            self.assertEqual(i18n._detect_lang(), "fr")  # no TypeError at startup

    def test_a50_lang_accepts_a_region(self):
        for value in ("es", "es_ES", "es-ES", "ES_es"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"A50_LANG": value}):
                self.assertEqual(i18n._detect_lang(), "es")

    def test_put_never_leaves_a_truncated_file(self):
        with tempfile.TemporaryDirectory() as tmp, _isolated_settings(tmp):
            settings.put("theme", "dark")
            with (
                mock.patch.object(settings.os, "replace", side_effect=OSError("disk full")),
                self.assertRaises(OSError),
            ):
                settings.put("language", "es")
            self.assertEqual(settings.load(), {"theme": "dark"})  # old content intact
            self.assertEqual(sorted(p.name for p in Path(tmp).rglob("*") if p.is_file()),
                             ["settings.json"])  # no temp file left behind

    def test_language_priority(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            _isolated_settings(tmp),
            mock.patch.object(i18n, "QLocale") as qlocale,
        ):
            qlocale.system.return_value.name.return_value = "fr_FR"
            with mock.patch.dict(os.environ, {"A50_LANG": ""}):
                self.assertEqual(i18n._detect_lang(), "fr")  # system locale
                settings.put("language", "es")
                self.assertEqual(i18n._detect_lang(), "es")  # menu choice beats the locale
                settings.put("language", "auto")
                self.assertEqual(i18n._detect_lang(), "fr")
            with mock.patch.dict(os.environ, {"A50_LANG": "en"}):
                self.assertEqual(i18n._detect_lang(), "en")  # env var beats everything


class ThemesTest(unittest.TestCase):
    def test_bundled_themes_are_listed(self):
        self.assertEqual(themes.available(), ["dark", "light"])

    def test_auto_json_is_not_listed_twice(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(themes, "THEMES_DIR", Path(tmp)),
        ):
            for name in ("auto", "dark"):
                Path(tmp, f"{name}.json").write_text("{}")
            self.assertEqual(themes.available(), ["dark"])

    def test_labels(self):
        with mock.patch.object(i18n, "LANG", "es"):
            self.assertEqual(themes.label("auto"), "Automático (colores del sistema)")
            self.assertEqual(themes.label("light"), "Claro")
            self.assertEqual(themes.label("dark"), "Oscuro")
            self.assertEqual(themes.label("my-theme"), "My Theme")

    def test_themes_only_change_colours(self):
        palette_cls = themes.QPalette
        for name, window, disabled_text in (("dark", "#202326", "#6d6f71"), ("light", "#eff0f1", "#a8a9ab")):
            with self.subTest(theme=name):
                app = mock.MagicMock()
                with (
                    mock.patch.object(themes, "_native_palette", palette_cls()),
                    mock.patch.object(themes, "_current", themes.AUTO),
                ):
                    self.assertEqual(themes.apply(app, name), name)
                    self.assertEqual(themes.current(), name)
                palette = app.setPalette.call_args.args[0]
                self.assertEqual(palette.color(palette_cls.ColorRole.Window).name(), window)
                disabled = palette.color(palette_cls.ColorGroup.Disabled, palette_cls.ColorRole.Text)
                self.assertEqual(disabled.name(), disabled_text)
                app.setStyle.assert_not_called()
                app.setStyleSheet.assert_not_called()

    def test_missing_or_broken_theme_falls_back_to_auto(self):
        native = themes.QPalette()
        app = mock.MagicMock()
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(themes, "THEMES_DIR", Path(tmp)),
            mock.patch.object(themes, "_native_palette", native),
            self.assertLogs(themes.LOGGER, "WARNING"),
        ):
            Path(tmp, "broken.json").write_text('{"NotARole": "#000000"}')
            self.assertEqual(themes.apply(app, "does-not-exist"), themes.AUTO)
            self.assertEqual(themes.apply(app, "broken"), themes.AUTO)
        app.setPalette.assert_called_with(native)

    def test_malformed_theme_files_fall_back_to_auto(self):
        native = themes.QPalette()
        bad_files = ["[]", '{"Window": null}', '{"disabled": []}', '{"Window": "#zzz"}', "{not json"]
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(themes, "THEMES_DIR", Path(tmp)),
            mock.patch.object(themes, "_native_palette", native),
            mock.patch.object(themes, "_current", "dark"),
            self.assertLogs(themes.LOGGER, "WARNING"),
        ):
            for i, content in enumerate(bad_files):
                with self.subTest(content=content):
                    Path(tmp, f"bad{i}.json").write_text(content)
                    app = mock.MagicMock()
                    self.assertEqual(themes.apply(app, f"bad{i}"), themes.AUTO)
                    app.setPalette.assert_called_once_with(native)
                    self.assertEqual(themes.current(), themes.AUTO)


class RestartAppTest(unittest.TestCase):
    def test_drops_a50_lang_and_reports_failure(self):
        # Points 4 and 5: a failed start is reported, and the new process
        # doesn't inherit A50_LANG, so the language picked in the menu applies.
        with (
            mock.patch.dict(os.environ, {"A50_LANG": "fr"}),
            mock.patch.object(gui, "QProcess") as process_cls,
        ):
            process = process_cls.return_value
            process.startDetached.return_value = (False, -1)
            self.assertFalse(gui.restart_app())
            process.startDetached.return_value = (True, 1234)
            self.assertTrue(gui.restart_app())
        env = process.setProcessEnvironment.call_args.args[0]
        self.assertFalse(env.contains("A50_LANG"))
        process.setArguments.assert_called_with([str(gui.SCRIPT_PATH)])


class MenuSlotsTest(unittest.TestCase):
    """The menu slots never let an exception escape (PyQt6 would abort the app)."""

    def _window(self):
        window = mock.MagicMock()
        window._has_unsynced.return_value = False
        window._lang_actions = {c: mock.MagicMock() for c in ("auto", "en", "es", "fr")}
        window._theme_actions = {n: mock.MagicMock() for n in ("auto", "dark", "light")}
        window._save_setting = lambda key, value: gui.A50Window._save_setting(window, key, value)
        return window

    def test_menu_entry_actions_are_hidden_when_run_from_the_package(self):
        # Issue #19: the package ships its own menu entry.
        # Remove stays while an entry made from a checkout would shadow it.
        window = self._window()
        window._build_language_menu = window._build_theme_menu = lambda _p: mock.MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            user_entry = Path(tmp) / "a.desktop"
            ours = "[Desktop Entry]\nExec=/venv/bin/python /src/gui.py\n"
            kmenuedit = "[Desktop Entry]\nName=Mine\nExec=astro-a50-gui\n"
            for packaged, content, install, remove in (
                (False, None, True, True),
                (True, None, False, False),
                (True, ours, False, True),
                (True, kmenuedit, False, False),  # the user's own edit of the package entry
            ):
                if content is None:
                    user_entry.unlink(missing_ok=True)
                else:
                    user_entry.write_text(content)
                with (mock.patch.object(gui, "PACKAGED", packaged),
                      mock.patch.object(gui, "DESKTOP_FILE", user_entry),
                      mock.patch.object(gui, "LEGACY_DESKTOP_FILE", Path(tmp) / "old.desktop"),
                      mock.patch.object(gui, "QAction") as action):
                    gui.A50Window._build_menu_bar(window)
                texts = [c.args[0] for c in action.call_args_list]
                self.assertEqual(gui.t("act_install_menu") in texts, install)
                self.assertEqual(gui.t("act_remove_menu") in texts, remove)
                self.assertIn(gui.t("act_about"), texts)

    def test_packaged_remove_hides_itself_once_done(self):
        window = self._window()
        for packaged, error, hidden in ((True, None, True), (True, OSError("ro"), False),
                                        (False, None, False)):
            window._act_remove.reset_mock()
            with (mock.patch.object(gui, "PACKAGED", packaged),
                  mock.patch.object(gui, "remove_entry", side_effect=error, return_value="ok"),
                  mock.patch.object(gui.QMessageBox, "warning")):
                gui.A50Window._remove_menu_entry(window)
            self.assertEqual(window._act_remove.setVisible.call_args_list,
                             [mock.call(False)] if hidden else [])

    def test_unwritable_config_warns_instead_of_crashing(self):
        window = self._window()
        with (
            mock.patch.object(gui.settings, "put", side_effect=PermissionError("read-only")),
            mock.patch.object(gui.QMessageBox, "warning") as warning,
        ):
            self.assertFalse(gui.A50Window._save_setting(window, "theme", "dark"))
        warning.assert_called_once()

    def test_unsaved_language_restores_the_menu(self):
        window = self._window()
        with (
            tempfile.TemporaryDirectory() as tmp,
            _isolated_settings(tmp),
            mock.patch.object(gui.settings, "put", side_effect=OSError("disk full")),
            mock.patch.object(gui.QMessageBox, "warning"),
            mock.patch.object(gui.QMessageBox, "question") as question,
        ):
            gui.A50Window._set_language(window, "es")
        window._lang_actions["auto"].setChecked.assert_called_with(True)
        question.assert_not_called()

    def test_unsaved_language_with_an_unknown_previous_one(self):
        # "de" has no menu entry (hand-edited file, newer version...): roll back
        # to Automatic instead of raising KeyError in the slot.
        window = self._window()
        with (
            tempfile.TemporaryDirectory() as tmp,
            _isolated_settings(tmp),
            mock.patch.object(gui.QMessageBox, "warning"),
        ):
            settings.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            settings.SETTINGS_PATH.write_text('{"language": "de"}')
            with mock.patch.object(gui.settings, "put", side_effect=OSError("disk full")):
                gui.A50Window._set_language(window, "es")
        window._lang_actions["auto"].setChecked.assert_called_with(True)

    def test_no_restart_offered_when_the_language_does_not_change(self):
        # Running in Spanish after declining a restart to French: picking
        # Spanish again just saves it, it doesn't offer a pointless restart.
        window = self._window()
        with (
            tempfile.TemporaryDirectory() as tmp,
            _isolated_settings(tmp),
            mock.patch.object(i18n, "LANG", "es"),
            mock.patch.object(gui.QMessageBox, "question") as question,
        ):
            settings.put("language", "fr")
            gui.A50Window._set_language(window, "es")
            self.assertEqual(settings.get("language"), "es")
        question.assert_not_called()

    def test_failed_restart_warns_and_keeps_the_window(self):
        window = self._window()
        with (
            tempfile.TemporaryDirectory() as tmp,
            _isolated_settings(tmp),
            mock.patch.object(
                gui.QMessageBox, "question", return_value=gui.QMessageBox.StandardButton.Yes
            ),
            mock.patch.object(gui.QMessageBox, "warning") as warning,
            mock.patch.object(gui, "restart_app", return_value=False),
        ):
            gui.A50Window._set_language(window, "es")
            self.assertEqual(settings.get("language"), "es")
        warning.assert_called_once()
        window.close.assert_not_called()

    def test_failed_theme_checks_automatic_again(self):
        window = self._window()
        with (
            tempfile.TemporaryDirectory() as tmp,
            _isolated_settings(tmp),
            mock.patch.object(gui.themes, "apply", return_value=themes.AUTO),
            mock.patch.object(gui, "QApplication"),
        ):
            gui.A50Window._set_theme(window, "dark")
            self.assertEqual(settings.get("theme"), themes.AUTO)
        window._theme_actions[themes.AUTO].setChecked.assert_called_with(True)
        window.statusBar.return_value.showMessage.assert_called_once()


# A preset exported by Astro Command Center, byte for byte (BOM and CRLF included).
_ARCTURUS = (
    "\ufeff[General]\r\nName=ZaliaS Arcturus[2019]\r\n\r\n[Eq_Bands]\r\n"
    "Eq_Band_1=5\r\nEq_Band_2=6\r\nEq_Band_3=4\r\nEq_Band_4=6\r\nEq_Band_5=5\r\n"
    "Eq_Freq_Band_1=100\r\nEq_Freq_Band_2=1775\r\nEq_Freq_Band_3=4664\r\n"
    "Eq_Freq_Band_4=8210\r\nEq_Freq_Band_5=11125\r\n"
    "Eq_Bandwidth_2=2.2\r\nEq_Bandwidth_3=2\r\nEq_Bandwidth_4=2.2\r\n\r\n"
)
_ARCTURUS_TEMPLATE = {
    "gain": [5, 6, 4, 6, 5],
    "bands": {1: (100, 0), 2: (1775, 9011), 3: (4664, 8192), 4: (8210, 9011), 5: (11125, 0)},
}


class AstroEqTest(unittest.TestCase):
    def test_parses_a_command_center_file(self):
        self.assertEqual(astroeq.parse(_ARCTURUS), _ARCTURUS_TEMPLATE)

    def test_limits_match_eh_fifty(self):
        self.assertEqual(
            astroeq.GAIN_RANGE, (eh_fifty._EQ_PRESET_MIN_GAIN, eh_fifty._EQ_PRESET_MAX_GAIN)
        )
        self.assertEqual(
            astroeq.FREQ_RANGE,
            (eh_fifty._EQ_PRESET_MIN_CENTER_FREQ, eh_fifty._EQ_PRESET_MAX_CENTER_FREQ),
        )
        low, high = astroeq.BANDWIDTH_RANGE
        self.assertEqual(eh_fifty._EQ_PRESET_MIN_BANDWIDTH, int(4096 * low))
        self.assertEqual(eh_fifty._EQ_PRESET_MAX_BANDWIDTH, int(4096 * high))

    def test_rejects_invalid_files(self):
        broken = {
            "gain out of range": _ARCTURUS.replace("Eq_Band_1=5", "Eq_Band_1=8"),
            "frequency too low": _ARCTURUS.replace("Eq_Freq_Band_1=100", "Eq_Freq_Band_1=79"),
            "bandwidth too wide": _ARCTURUS.replace("Eq_Bandwidth_3=2", "Eq_Bandwidth_3=3.1"),
            "missing value": _ARCTURUS.replace("Eq_Band_2=6\r\n", ""),
            "not a number": _ARCTURUS.replace("Eq_Band_2=6", "Eq_Band_2=loud"),
            "no EQ section": "[General]\nName=x\n",
            "not INI at all": "hello",
        }
        for case, text in broken.items():
            with self.subTest(case=case), self.assertRaises(ValueError):
                astroeq.parse(text)

    def test_round_trip(self):
        # Builtin templates and an imported one survive dump -> parse unchanged,
        # and the output uses Command Center's own layout.
        cases = {**templates._EQ_TEMPLATES, "ARCTURUS": _ARCTURUS_TEMPLATE}
        for name, tpl in cases.items():
            with self.subTest(name=name):
                expected = {"gain": list(tpl["gain"]), "bands": dict(tpl["bands"])}
                self.assertEqual(astroeq.parse(astroeq.dump(name, tpl)), expected)
        text = astroeq.dump("ARCTURUS", _ARCTURUS_TEMPLATE)
        self.assertIn("Eq_Bandwidth_2=2.2\r\n", text)
        self.assertIn("Eq_Bandwidth_3=2\r\n", text)


class EqSlotReferenceTest(unittest.TestCase):
    """Issue #18: a slot's reference (a template, or the base's own values)
    drives reset, sync, the modified marks and the "(on the base)" entry."""

    def setUp(self):
        # Never the real library: a sync or delete here may save it.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(templates, "USER_TEMPLATES_FILE", Path(tmp.name) / "user.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _reloaded(self, device_data, user_templates=None):
        widget = _reload_widget(device_data, user_templates=user_templates)
        widget.reload_under_lock(active_eq_preset=1)
        return widget

    def _arcturus_everywhere(self):
        return self._reloaded({s: dict(_ARCTURUS_ON_BASE) for s in (1, 2, 3)})

    def test_on_base_entry_follows_a_sync(self):
        # B1: after syncing MEDIA over it, the slot no longer offers the old
        # "ARCTURUS (on the base)" entry, which would load MEDIA's values.
        widget = self._arcturus_everywhere()
        combo = widget.template_combos[1]
        combo.setCurrentIndex(combo.findData("MEDIA"))
        widget.push_pending_to_device()
        self.assertEqual(combo.findData(EqTemplatesWidget.ON_DEVICE), -1)
        self.assertEqual(combo.currentData(), "MEDIA")

    def test_reset_on_the_base_entry_restores_its_values(self):
        # B2
        widget = self._arcturus_everywhere()
        original = list(widget._slot_bands[1])
        widget._on_band_modified(2, 0)
        widget._on_reset_templates()
        self.assertEqual(widget._slot_bands[1], original)
        self.assertEqual(widget._slot_modified[1], set())
        self.assertEqual(widget._slot_pending[1], set())

    def test_synced_edit_on_the_base_entry_is_no_longer_marked(self):
        # B3: the base now holds exactly the edited values.
        widget = self._arcturus_everywhere()
        widget._on_band_modified(2, 0)
        widget.push_pending_to_device()
        self.assertEqual(widget._slot_modified[1], set())
        self.assertEqual(widget.template_combos[1].currentData(), EqTemplatesWidget.ON_DEVICE)

    def test_import_names_a_slot_holding_that_preset_without_reading_the_base(self):
        # What gui.py re-read the device for: now the widget re-matches itself.
        widget = self._arcturus_everywhere()
        widget.device.reset_mock()
        arc = _ARCTURUS_ON_BASE
        path = Path(tempfile.mkdtemp()) / "ARCTURUS.astroeq"
        self.addCleanup(shutil.rmtree, path.parent)
        path.write_text(astroeq.dump("ARCTURUS", {"gain": arc["gain"], "bands": arc["bands"]}))
        with mock.patch("eq_widget._save_user_templates"):
            widget.import_files([path])
        for slot in (1, 2, 3):
            self.assertEqual(widget.template_combos[slot].currentData(), "ARCTURUS")
            self.assertEqual(widget._slot_modified[slot], set())
            self.assertEqual(widget._slot_pending[slot], set())
        widget.device.get_eq_preset_name.assert_not_called()

    def test_deleting_the_preset_a_slot_holds_shows_the_base_values(self):
        # Before: the slot fell back to the first template with a pending
        # Sync, which would rename the device's slot.
        mine = {"gain": [1] * 5, "bands": dict(templates._EQ_TEMPLATES["MEDIA"]["bands"])}
        widget = self._reloaded({1: dict(mine, name="MINE"), 2: dict(_ARCTURUS_ON_BASE),
                                 3: dict(_ARCTURUS_ON_BASE)}, user_templates={"MINE": dict(mine)})
        self.assertEqual(widget.template_combos[1].currentData(), "MINE")
        with (mock.patch("eq_widget.QMessageBox") as box,
              mock.patch("eq_widget._save_user_templates")):
            box.StandardButton.Yes = "yes"
            box.question.return_value = "yes"
            widget._on_delete_template_for_slot(1)
        self.assertEqual(widget.template_combos[1].currentData(), EqTemplatesWidget.ON_DEVICE)
        self.assertEqual([g for _f, g in widget._slot_bands[1]], [1] * 5)
        self.assertEqual(widget._slot_pending[1], set())


class EqSlotReferenceReviewTest(unittest.TestCase):
    """Follow-ups from the review of #21."""

    def setUp(self):
        # Never the real library: a sync or delete here may save it.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(templates, "USER_TEMPLATES_FILE", Path(tmp.name) / "user.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_delete_shows_what_the_base_holds_even_for_an_edited_builtin(self):
        # The base holds MEDIA with its own gains: deleting the preset shown in
        # that slot must not load the MEDIA template and queue it for Sync.
        media = templates._EQ_TEMPLATES["MEDIA"]
        mine = {"gain": [1] * 5, "bands": dict(media["bands"])}
        edited = {"name": "MEDIA", "gain": [7] * 5, "bands": dict(media["bands"])}
        widget = _reload_widget({1: edited, 2: dict(_ARCTURUS_ON_BASE), 3: dict(_ARCTURUS_ON_BASE)},
                                user_templates={"MINE": dict(mine)})
        widget.reload_under_lock(1)
        combo = widget.template_combos[1]
        combo.setCurrentIndex(combo.findData("MINE"))
        with (mock.patch("eq_widget.QMessageBox") as box,
              mock.patch("eq_widget._save_user_templates")):
            box.StandardButton.Yes = "yes"
            box.question.return_value = "yes"
            widget._on_delete_template_for_slot(1)
        self.assertEqual(combo.currentData(), "MEDIA")
        self.assertEqual([g for _f, g in widget._slot_bands[1]], [7] * 5)
        self.assertEqual(widget._slot_pending[1], set())

    def test_failed_sync_still_rematches_the_slots_already_written(self):
        widget = _reload_widget({s: dict(_ARCTURUS_ON_BASE) for s in (1, 2, 3)})
        widget.reload_under_lock(1)
        for slot in (1, 2):
            combo = widget.template_combos[slot]
            combo.setCurrentIndex(combo.findData("MEDIA"))
        widget.device.set_eq_preset_name.side_effect = (
            lambda slot, _name: (_ for _ in ()).throw(OSError("unplugged")) if slot == 2 else None)
        with self.assertRaises(OSError):
            widget.push_pending_to_device()
        self.assertEqual(widget._device_templates[1], "MEDIA")
        self.assertEqual(widget.template_combos[1].findData(EqTemplatesWidget.ON_DEVICE), -1)

    def test_unreadable_slot_shows_no_preset(self):
        mine = {"gain": [1] * 5, "bands": dict(templates._EQ_TEMPLATES["MEDIA"]["bands"])}
        device = {1: dict(_ARCTURUS_ON_BASE), 2: dict(mine, name="MINE"), 3: dict(_ARCTURUS_ON_BASE)}
        widget = _reload_widget(device, user_templates={"MINE": dict(mine)})
        widget.reload_under_lock(1)
        self.assertEqual(widget.template_combos[2].currentData(), "MINE")
        device[2] = None  # the next read of slot 2 fails
        widget.reload_under_lock(1)
        self.assertIsNone(widget.template_combos[2].currentData())
        self.assertEqual(widget._slot_bands[2], [])

    def test_sync_leaves_the_combo_lists_alone_when_the_library_is_unchanged(self):
        widget = _reload_widget({s: dict(_ARCTURUS_ON_BASE) for s in (1, 2, 3)})
        widget.reload_under_lock(1)
        cleared = []
        for slot, combo in widget.template_combos.items():
            combo.clear = lambda s=slot, c=combo: (cleared.append(s), _MockCombo.clear(c))
        widget._on_band_modified(2, 0)
        widget.push_pending_to_device()
        self.assertEqual(cleared, [])


class EqSlotReferenceSecondReviewTest(unittest.TestCase):
    """Follow-ups from the second review of #21."""

    def setUp(self):
        # Never the real library: a sync or delete here may save it.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(templates, "USER_TEMPLATES_FILE", Path(tmp.name) / "user.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_bandwidths_set_elsewhere_are_marked_until_a_sync_is_queued(self):
        # The base holds MEDIA's name, gains and frequencies with its own
        # bandwidths (set in Command Center): the bands differ from MEDIA.
        media = templates._EQ_TEMPLATES["MEDIA"]
        bands = dict(media["bands"])
        bands[3] = (bands[3][0], bands[3][1] + 1000)
        base = {"name": "MEDIA", "gain": list(media["gain"]), "bands": bands}
        widget = _reload_widget({1: base, 2: dict(_ARCTURUS_ON_BASE), 3: dict(_ARCTURUS_ON_BASE)})
        widget.reload_under_lock(1)
        self.assertEqual(widget.template_combos[1].currentData(), "MEDIA")
        self.assertEqual(widget._slot_modified[1], {3})
        widget._on_reset_templates()  # queues MEDIA, bandwidths included
        self.assertEqual(widget._slot_pending[1], {1, 2, 3, 4, 5})
        self.assertEqual(widget._slot_modified[1], set())

    def test_sync_redefining_a_user_preset_queues_the_other_slots_showing_it(self):
        mine = {"gain": [1] * 5, "bands": dict(templates._EQ_TEMPLATES["MEDIA"]["bands"])}
        widget = _reload_widget({1: dict(mine, name="Mine"), 2: dict(mine, name="Mine"),
                                 3: dict(_ARCTURUS_ON_BASE)}, user_templates={"Mine": dict(mine)})
        widget.reload_under_lock(1)
        with mock.patch("eq_widget._save_user_templates"):
            widget._on_band_modified(2, 5)
            widget.push_pending_to_device()
        self.assertEqual(widget._user_templates["Mine"]["gain"], [1, 5, 1, 1, 1])
        self.assertEqual(widget._slot_pending[2], {1, 2, 3, 4, 5})
        self.assertEqual([g for _f, g in widget._slot_bands[2]], [1, 5, 1, 1, 1])

    def test_import_overwrite_leaves_a_slot_already_holding_the_new_values(self):
        # Edited in Command Center, which wrote it to slot 2, then imported here.
        old = {"gain": [1] * 5, "bands": dict(templates._EQ_TEMPLATES["MEDIA"]["bands"])}
        new = dict(old, gain=[2] * 5)
        widget = _reload_widget({1: dict(_ARCTURUS_ON_BASE), 2: dict(new, name="Mine"),
                                 3: dict(_ARCTURUS_ON_BASE)}, user_templates={"Mine": dict(old)})
        widget.reload_under_lock(1)
        path = Path(tempfile.mkdtemp()) / "Mine.astroeq"
        self.addCleanup(shutil.rmtree, path.parent)
        path.write_text(astroeq.dump("Mine", new))
        with (mock.patch.object(EqTemplatesWidget, "_prompt_new_template_name",
                                return_value="Mine"),
              mock.patch("eq_widget._save_user_templates")):
            widget.import_files([path])
        self.assertEqual(widget.template_combos[2].currentData(), "Mine")
        self.assertEqual(widget._slot_pending[2], set())

    def test_import_of_the_same_name_keeps_a_pending_base_entry(self):
        # Slot 1 shows "ARCTURUS (on the base)" with an unsynced edit; a file of
        # that name with other bandwidths is imported. The slot keeps the
        # base's own values: Sync must not write the file's bandwidths.
        arc = dict(_ARCTURUS_ON_BASE)
        widget = _reload_widget({s: dict(arc) for s in (1, 2, 3)})
        widget.reload_under_lock(1)
        widget._on_band_modified(2, 0)
        file_bands = {b: (f, bw + 500 if bw else 0) for b, (f, bw) in arc["bands"].items()}
        path = Path(tempfile.mkdtemp()) / "ARCTURUS.astroeq"
        self.addCleanup(shutil.rmtree, path.parent)
        path.write_text(astroeq.dump("ARCTURUS", {"gain": arc["gain"], "bands": file_bands}))
        with mock.patch("eq_widget._save_user_templates"):
            widget.import_files([path])
        self.assertEqual(widget.template_combos[1].currentData(), EqTemplatesWidget.ON_DEVICE)
        widget.push_pending_to_device()
        widget.device.set_eq_preset_name.assert_called_with(1, "ARCTURUS")
        widget.device.set_eq_preset_freq_and_bw.assert_any_call(1, 2, 1775, 9011)
        # Synced, it shows the file's template, marked where the bandwidths differ.
        self.assertEqual(widget.template_combos[1].currentData(), "ARCTURUS")
        self.assertEqual(widget._slot_modified[1], {2, 3, 4})

    def test_failed_sync_refreshes_the_meter_and_buttons(self):
        widget = _reload_widget({s: dict(_ARCTURUS_ON_BASE) for s in (1, 2, 3)})
        widget.reload_under_lock(1)
        widget._refresh_meter = mock.MagicMock()
        widget._update_apply_enabled = mock.MagicMock()
        widget._on_band_modified(2, 0)
        widget._refresh_meter.reset_mock()
        widget._update_apply_enabled.reset_mock()
        widget.device.set_eq_preset_gain.side_effect = OSError("unplugged")
        with self.assertRaises(OSError):
            widget.push_pending_to_device()
        widget._refresh_meter.assert_called()
        widget._update_apply_enabled.assert_called()


class EqWidgetImportExportTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        patcher = mock.patch.object(templates, "USER_TEMPLATES_FILE", self.dir / "user.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _make(self):
        widget = EqTemplatesWidget.__new__(EqTemplatesWidget)
        widget._user_templates = {}
        for name in ("_rematch", "_refresh_meter", "_update_apply_enabled",
                     "_emit_dirty_if_changed"):
            setattr(widget, name, mock.MagicMock())
        return widget

    def test_import_adds_presets_named_after_their_files(self):
        (self.dir / "ARCTURUS.astroeq").write_text(_ARCTURUS, encoding="utf-8")
        (self.dir / "broken.astroeq").write_text("hello", encoding="utf-8")
        widget = self._make()
        with mock.patch("eq_widget.QMessageBox.warning") as warning:
            imported = widget.import_files([self.dir / "ARCTURUS.astroeq", self.dir / "broken.astroeq"])
        self.assertEqual(imported, ["ARCTURUS"])
        warning.assert_called_once()  # the broken file is reported, not fatal
        saved = templates._load_user_templates()
        self.assertEqual(saved["ARCTURUS"], _ARCTURUS_TEMPLATE)
        widget._rematch.assert_called_once()

    def test_reimporting_an_identical_preset_asks_nothing(self):
        (self.dir / "ARCTURUS.astroeq").write_text(_ARCTURUS, encoding="utf-8")
        widget = self._make()
        widget._user_templates = {"ARCTURUS": _ARCTURUS_TEMPLATE}
        widget._prompt_new_template_name = mock.MagicMock()
        self.assertEqual(widget.import_files([self.dir / "ARCTURUS.astroeq"]), ["ARCTURUS"])
        widget._prompt_new_template_name.assert_not_called()
        widget._rematch.assert_not_called()  # nothing new to save or list

    def test_import_name_clash_asks_for_another_name(self):
        (self.dir / "MEDIA.astroeq").write_text(_ARCTURUS, encoding="utf-8")
        widget = self._make()
        widget._prompt_new_template_name = mock.MagicMock(return_value="MEDIA 2")
        self.assertEqual(widget.import_files([self.dir / "MEDIA.astroeq"]), ["MEDIA 2"])
        widget._prompt_new_template_name.assert_called_once_with(suggestion="MEDIA")

    def test_import_rereads_the_eq_slots_unless_edits_are_pending(self):
        # The base may have changed behind the app (Command Center, in a VM the
        # base was passed to): re-read it, unless unsynced EQ edits would be
        # lost; the widget re-matches its slots itself in that case.
        for pending, reloads in ((False, 1), (True, 0)):
            with self.subTest(pending=pending):
                window = mock.MagicMock()
                window._device_lock = threading.RLock()
                window.eq.import_presets.return_value = ["ARCTURUS"]
                window.eq.has_pending.return_value = pending
                window.device.get_active_eq_preset.return_value = 2
                gui.A50Window._import_presets(window)
                self.assertEqual(window.eq.reload_under_lock.call_count, reloads)
                if reloads:
                    window.eq.reload_under_lock.assert_called_with(2)
                window.eq.load_into_selected_slot.assert_called_once_with("ARCTURUS")

    def test_load_into_selected_slot_picks_it_in_that_combo(self):
        widget = self._make()
        widget._selected_slot = 2
        widget.template_combos = {s: mock.MagicMock() for s in (1, 2, 3)}
        widget.template_combos[2].findData.return_value = 4
        widget.load_into_selected_slot("PURE")
        widget.template_combos[2].setCurrentIndex.assert_called_once_with(4)
        widget.template_combos[1].setCurrentIndex.assert_not_called()
        widget.template_combos[2].findData.return_value = -1  # unknown name: no change
        widget.load_into_selected_slot("NOPE")
        widget.template_combos[2].setCurrentIndex.assert_called_once()

    def test_load_into_selected_slot_reapplies_the_preset_already_shown(self):
        # setCurrentIndex on the current index emits nothing: the slot's edited
        # bands would stay while the import says it was loaded.
        widget = self._make()
        widget._selected_slot = 2
        widget.template_combos = {s: mock.MagicMock() for s in (1, 2, 3)}
        widget.template_combos[2].findData.return_value = 4
        widget.template_combos[2].currentIndex.return_value = 4
        widget._on_template_combo_changed = mock.MagicMock()
        widget.load_into_selected_slot("ARCTURUS")
        widget._on_template_combo_changed.assert_called_once_with(2)

    def test_export_lets_the_dialog_add_the_suffix(self):
        # The dialog's overwrite check must see the final name, suffix included.
        widget = self._make()
        widget._selected_slot = 1
        widget.template_combos = {1: mock.MagicMock()}
        path = self.dir / "ARCTURUS.ASTROEQ"
        with (mock.patch("eq_widget.QInputDialog.getItem", return_value=("MEDIA", True)),
              mock.patch("eq_widget.QFileDialog") as dialog_cls):
            dialog = dialog_cls.return_value
            dialog.exec.return_value = True
            dialog.selectedFiles.return_value = [str(path)]
            self.assertEqual(widget.export_preset(), "MEDIA")
        dialog.setDefaultSuffix.assert_called_once_with("astroeq")
        self.assertTrue(path.exists())  # no second suffix on an upper-case one

    def _reloaded(self, device_data, user_templates=None):
        widget = _reload_widget(device_data, user_templates=user_templates)
        widget.reload_under_lock(active_eq_preset=1)
        return widget

    def test_import_of_the_base_preset_keeps_it_selected(self):
        # The "(on the base)" entry goes once its name is a template: the slot
        # must pick that template, not fall back to the first one and rename
        # the device's slot on Sync.
        arc = dict(_ARCTURUS_ON_BASE)
        widget = self._reloaded({1: dict(arc), 2: dict(arc), 3: dict(arc)})
        widget._on_band_modified(2, 0)  # unsynced edit: no re-read after import
        path = self.dir / "ARCTURUS.astroeq"
        path.write_text(astroeq.dump("ARCTURUS", {"gain": arc["gain"], "bands": arc["bands"]}))
        widget.import_files([path])
        self.assertEqual(widget.template_combos[1].currentData(), "ARCTURUS")
        widget.push_pending_to_device()
        widget.device.set_eq_preset_name.assert_called_with(1, "ARCTURUS")

    def test_import_overwriting_a_preset_updates_the_slots_showing_it(self):
        mine = {"gain": [0] * 5, "bands": dict(templates._EQ_TEMPLATES["MEDIA"]["bands"])}
        arc = dict(_ARCTURUS_ON_BASE)
        widget = self._reloaded({1: dict(arc), 2: dict(mine, name="MINE"), 3: dict(arc)},
                                user_templates={"MINE": dict(mine)})
        path = self.dir / "MINE.astroeq"
        path.write_text(astroeq.dump("MINE", dict(mine, gain=[3] * 5)))
        with mock.patch.object(EqTemplatesWidget, "_prompt_new_template_name",
                               return_value="MINE"):
            widget.import_files([path])
        self.assertEqual([g for _f, g in widget._slot_bands[2]], [3] * 5)
        self.assertEqual(widget._slot_pending[2], {1, 2, 3, 4, 5})

    def test_import_of_a_too_long_file_name_asks_for_another(self):
        widget = self._make()
        path = self.dir / ("é" * 30 + ".astroeq")  # 60 bytes, over the 58 a slot holds
        path.write_text(astroeq.dump("x", templates._EQ_TEMPLATES["MEDIA"]))
        with mock.patch.object(EqTemplatesWidget, "_prompt_new_template_name",
                               return_value="Short") as prompt:
            self.assertEqual(widget.import_files([path]), ["Short"])
        prompt.assert_called_once()

    def test_import_reports_a_failed_library_save_as_the_other_saves_do(self):
        from i18n import t
        widget = self._make()
        path = self.dir / "NEW.astroeq"
        path.write_text(astroeq.dump("NEW", templates._EQ_TEMPLATES["MEDIA"]))
        with (mock.patch("eq_widget._save_user_templates", side_effect=OSError("ro")),
              mock.patch("eq_widget.QMessageBox") as box):
            widget.import_files([path])
        self.assertIn(t("err_template_save", error="ro"), str(box.warning.call_args))

    def test_export_writes_a_file_command_center_layout(self):
        widget = self._make()
        path = self.dir / "MEDIA.astroeq"
        widget.export_file("MEDIA", path)
        raw = path.read_bytes()
        self.assertIn(b"\r\n", raw)
        media = templates._EQ_TEMPLATES["MEDIA"]
        self.assertEqual(
            astroeq.parse(raw.decode()), {"gain": media["gain"], "bands": media["bands"]}
        )
class SweepRegressionTest(unittest.TestCase):
    """Regressions for the bugs found by the sweep of main at 5800fc1."""

    # --- templates.py -------------------------------------------------

    def test_load_tolerates_any_file_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "user-templates.json"
            with mock.patch.object(templates, "USER_TEMPLATES_FILE", path):
                for content in (b"[]", b"null", b"\xe9t\xe9",
                                json.dumps({"A": {"gain": [0] * 5, "bands": []}}).encode(),
                                json.dumps({"A": {"gain": [0, 0], "bands": {}}}).encode()):
                    path.write_bytes(content)
                    self.assertEqual(templates._load_user_templates(), {}, content)

    def test_interrupted_save_keeps_the_previous_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "user-templates.json"
            old = {"Mix": {"gain": [1, 2, 3, 4, 5],
                           "bands": {b: (100 * b, 0) for b in range(1, 6)}}}
            with mock.patch.object(templates, "USER_TEMPLATES_FILE", path):
                templates._save_user_templates(old)
                with (mock.patch.object(templates.os, "fsync", side_effect=OSError("disk full")),
                      self.assertRaises(OSError)):
                    templates._save_user_templates({})
                self.assertEqual(templates._load_user_templates(), old)

    # --- eq_widget.py -------------------------------------------------

    def test_delete_with_unwritable_library_warns_and_keeps_preset(self):
        widget = EqWidgetPersistAndPushTest._make(
            self, user_templates={"Mine": {"gain": [0] * 5, "bands": {}}}, combo_data="Mine")
        with (mock.patch("eq_widget.QMessageBox") as box,
              mock.patch("eq_widget._save_user_templates", side_effect=PermissionError("ro"))):
            box.StandardButton.Yes = "yes"
            box.question.return_value = "yes"
            widget._on_delete_template_for_slot(1)
        box.warning.assert_called_once()
        self.assertIn("Mine", widget._user_templates)

    def test_reload_keeps_a_device_preset_the_library_does_not_know(self):
        bands = {1: (80, 0), 2: (500, 1234), 3: (900, 4321), 4: (3000, 777), 5: (9000, 0)}
        foreign = {"name": "FROM ACC", "gain": [1, 2, 3, 4, 5], "bands": bands}
        media = templates._EQ_TEMPLATES["MEDIA"]
        device = {1: foreign, 2: dict(media, name="MEDIA"), 3: dict(media, name="MEDIA")}
        widget = _reload_widget(device)
        widget.reload_under_lock(1)
        self.assertEqual(widget.template_combos[1].currentData(), EqTemplatesWidget.ON_DEVICE)
        self.assertEqual(widget._slot_modified[1], set())
        # An edit then Sync keeps the device's name and bandwidths.
        widget._slot_pending[1] = {3}
        widget.push_pending_to_device()
        widget.device.set_eq_preset_name.assert_called_with(1, "FROM ACC")
        widget.device.set_eq_preset_freq_and_bw.assert_any_call(1, 2, 500, 1234)

    def test_band_back_to_template_repaints_the_meter(self):
        widget = EqWidgetHandlersTest._make(self)
        painted = []
        widget._refresh_meter = lambda: painted.append(set(widget._slot_modified[1]))
        media = templates._EQ_TEMPLATES["MEDIA"]
        widget._on_band_modified(3, 5)
        widget._on_band_modified(3, media["gain"][2])
        self.assertEqual(painted[-1], set())

    def test_too_long_name_is_refused_before_anything_is_saved(self):
        widget = EqWidgetPersistAndPushTest._make(self)
        with (mock.patch("eq_widget.QInputDialog.getText",
                         side_effect=[("é" * 30, True), (None, False)]),
              mock.patch("eq_widget.QMessageBox") as box):
            self.assertIsNone(widget._prompt_new_template_name())
        box.warning.assert_called_once()

    def test_refused_device_write_leaves_library_and_disk_untouched(self):
        widget = EqWidgetPersistAndPushTest._make(self)
        widget.device.set_eq_preset_name.side_effect = AssertionError()
        with (mock.patch("eq_widget.QApplication"), mock.patch("eq_widget.QMessageBox") as box,
              mock.patch("eq_widget._save_user_templates") as save):
            widget._persist_and_push(1, "NewMix", is_new=True, btn=mock.MagicMock(),
                                     busy_key="btn_apply_busy", idle_key="btn_apply")
        self.assertNotIn("NewMix", widget._user_templates)
        save.assert_not_called()
        self.assertIn("AssertionError", str(box.warning.call_args))

    def test_create_keeps_other_slots_edits_and_the_active_slot(self):
        widget = EqWidgetPersistAndPushTest._make(self)
        widget.reload_under_lock = mock.MagicMock()
        widget._slot_pending[2] = {1}
        with mock.patch("eq_widget.QApplication"), mock.patch("eq_widget._save_user_templates"):
            widget._persist_and_push(1, "NewMix", is_new=True, btn=mock.MagicMock(),
                                     busy_key="btn_apply_busy", idle_key="btn_apply")
        widget.reload_under_lock.assert_not_called()
        self.assertEqual(widget._slot_pending[2], {1})
        self.assertEqual(widget._device_active_eq, 1)

    def test_failed_sync_keeps_the_user_preset(self):
        media = templates._EQ_TEMPLATES["MEDIA"]
        mine = {"gain": list(media["gain"]), "bands": dict(media["bands"])}
        widget = EqWidgetPushPendingTest._make(
            self, user_templates={"Mine": dict(mine)}, combos={1: "Mine"},
            slot_bands={1: [(f, g + 1 if b == 0 else g) for b, (f, g) in
                            enumerate(EqWidgetPushPendingTest._bands_from_template("MEDIA"))],
                        2: [], 3: []},
            slot_modified={1: {1}, 2: set(), 3: set()}, slot_pending={1: {1}, 2: set(), 3: set()})
        widget.device.set_eq_preset_gain.side_effect = OSError("unplugged")
        with mock.patch("eq_widget._save_user_templates"), self.assertRaises(OSError):
            widget.push_pending_to_device()
        self.assertEqual(widget._user_templates["Mine"]["gain"], mine["gain"])
        self.assertEqual(widget._slot_modified[1], {1})

    def test_unread_active_slot_is_never_written(self):
        widget = EqWidgetPushPendingTest._make(self, device_active=None, selected_slot=1)
        widget.push_pending_to_device()
        widget.device.set_active_eq_preset.assert_not_called()

    # --- gui.py -------------------------------------------------------

    def _window(self, **reads):
        window = mock.MagicMock()
        window._loading = False
        window.slider_widgets = {}
        for name, value in reads.items():
            getattr(window.device, name).side_effect = (
                value if isinstance(value, Exception) else None)
            getattr(window.device, name).return_value = value
        return window

    def test_failed_reads_disable_controls_and_sync_skips_them(self):
        err = OSError("timeout")
        window = self._window(get_balance=err, get_noise_gate_mode=err,
                              get_alert_volume=err, get_active_eq_preset=err)
        window.cmb_gate.findData.return_value = -1
        gui.A50Window.reload_all(window)
        window.sld_balance.setEnabled.assert_called_with(False)
        window.cmb_gate.setEnabled.assert_called_with(False)
        window.sld_alert.setEnabled.assert_called_with(False)
        for w in (window.sld_balance, window.cmb_gate, window.sld_alert):
            w.isEnabled.return_value = False
        with mock.patch.object(gui, "QApplication"):
            gui.A50Window._on_save(window)
        window.device.set_default_balance.assert_not_called()
        window.device.set_noise_gate_mode.assert_not_called()
        window.device.set_alert_volume.assert_not_called()

    def test_sync_writes_the_default_balance_only_when_the_slider_moved(self):
        class Slider:
            v, enabled = 0, False

            def setValue(self, v):
                self.v = v

            def value(self):
                return self.v

            def setEnabled(self, enabled):
                self.enabled = enabled

            def isEnabled(self):
                return self.enabled

        window = self._window(get_balance=113)
        window.sld_balance = Slider()
        window.cmb_gate.findData.return_value = 0
        window._controls = gui.A50Window._controls.__get__(window)

        def sync():
            with mock.patch.object(gui, "QApplication"):
                gui.A50Window._on_save(window)

        gui.A50Window.reload_all(window)
        # 113 (set with the headset buttons) is recorded as 56% game...
        self.assertEqual(window._synced["balance"], gui.game_percent(113))
        # ...so Sync with the slider untouched writes nothing, not a rounded 112.
        sync()
        window.device.set_default_balance.assert_not_called()
        window.sld_balance.setValue(20)
        sync()
        window.device.set_default_balance.assert_called_once_with(gui.balance_from_game_percent(20))
        sync()  # synced at 20 now: nothing new to write
        window.device.set_default_balance.assert_called_once()

    @staticmethod
    def _settings_window():
        """A window whose settings controls hold real values."""
        class Control:
            def __init__(self, value):
                self.v = value

            def value(self):
                return self.v

            currentData = value

            def isEnabled(self):
                return True

        window = mock.MagicMock()
        window._loading = False
        window.sld_balance, window.cmb_gate, window.sld_alert = Control(50), Control(1), Control(50)
        window.slider_widgets = {"MIC": (Control(40), mock.MagicMock())}
        window._SYNC_STYLE_DIRTY = gui.A50Window._SYNC_STYLE_DIRTY
        window._SYNC_STYLE_SYNCED = gui.A50Window._SYNC_STYLE_SYNCED
        for name in ("_controls", "_has_unsynced", "_apply_sync_style"):
            setattr(window, name, getattr(gui.A50Window, name).__get__(window))
        window.eq.has_pending.return_value = False
        window._synced = window._controls()
        return window

    def _assert_sync(self, window, orange):
        style = gui.A50Window._SYNC_STYLE_DIRTY if orange else gui.A50Window._SYNC_STYLE_SYNCED
        window.btn_save.setStyleSheet.assert_called_with(style)
        # In sync, the greyed button can't be pressed either.
        window.btn_save.setEnabled.assert_called_with(orange)

    def test_sync_turns_back_to_synced_once_eq_edits_are_undone(self):
        # Issue #22: an EQ edit undone with Reset left Sync orange.
        window = self._settings_window()
        window.eq.has_pending.return_value = True
        gui.A50Window._on_eq_dirty_changed(window, True)
        self._assert_sync(window, orange=True)
        window.eq.has_pending.return_value = False  # Reset: nothing left to sync
        gui.A50Window._on_eq_dirty_changed(window, False)
        self._assert_sync(window, orange=False)

    def test_sync_turns_back_to_synced_once_a_setting_is_moved_back(self):
        window = self._settings_window()
        window.slider_widgets["MIC"][0].v = 60
        gui.A50Window._settings_changed(window)
        self._assert_sync(window, orange=True)
        # An EQ change undone meanwhile doesn't hide the slider's.
        gui.A50Window._on_eq_dirty_changed(window, False)
        self._assert_sync(window, orange=True)
        window.slider_widgets["MIC"][0].v = 40
        gui.A50Window._settings_changed(window)
        self._assert_sync(window, orange=False)
        window.sld_balance.v = 51
        gui.A50Window._settings_changed(window)
        self._assert_sync(window, orange=True)
        window.sld_balance.v = 50
        gui.A50Window._settings_changed(window)
        self._assert_sync(window, orange=False)

    def test_failed_sync_keeps_the_settings_to_sync(self):
        window = self._settings_window()
        window.sld_alert.v = 70
        window.device.save_values.side_effect = OSError("unplugged")
        with mock.patch.object(gui, "QApplication"), mock.patch.object(gui, "QMessageBox"):
            gui.A50Window._on_save(window)
        self._assert_sync(window, orange=True)
        window.device.save_values.side_effect = None
        with mock.patch.object(gui, "QApplication"):
            gui.A50Window._on_save(window)
        self._assert_sync(window, orange=False)

    def test_sync_button_keeps_its_height_in_both_states(self):
        # A 1px border in one style only made the whole window shift by 2px
        # each time Sync changed state.
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # CI has no display
        from PyQt6.QtWidgets import QApplication, QPushButton
        type(self)._qt_app = QApplication.instance() or QApplication([])
        heights = []
        for style in (gui.A50Window._SYNC_STYLE_DIRTY, gui.A50Window._SYNC_STYLE_SYNCED):
            button = QPushButton("Sync")
            button.setStyleSheet(style)
            heights.append(button.sizeHint().height())
        self.assertEqual(heights[0], heights[1])

    def test_gate_change_announces_nothing_before_sync(self):
        window = mock.MagicMock()
        window._loading = False
        gui.A50Window._on_gate_changed(window, 0)
        window.statusBar.return_value.showMessage.assert_not_called()
        window._settings_changed.assert_called_once()

    def test_apps_dir_follows_xdg_data_home(self):
        import importlib
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/xdg/data"}):
            try:
                self.assertEqual(importlib.reload(gui).APPS_DIR, Path("/xdg/data/applications"))
            finally:
                importlib.reload(gui)

    def test_base_info_shows_the_base_when_the_headset_is_off(self):
        window = mock.MagicMock()
        window._device_lock = mock.MagicMock()
        window.device.get_device_info.return_value = DeviceInfo(vendor_id=0x9886, product_id=0x2C)
        window.device.get_base_firmware_version.return_value = FirmwareVersion(major=40372, minor=43)
        window.device.get_headset_firmware_version.side_effect = AssertionError()
        with (mock.patch.object(gui, "_raw_request", side_effect=OSError("SLAVE")),
              mock.patch.object(gui, "QMessageBox") as box):
            gui.A50Window._show_base_info(window)
        box.warning.assert_not_called()
        text = box.information.call_args[0][2]
        self.assertIn("40372.43", text)
        self.assertIn("AssertionError", text)

    # --- raw_request.py -----------------------------------------------

    def test_raw_request_raises_on_an_error_answer(self):
        from raw_request import RawRequestError, _raw_request
        device = mock.MagicMock()
        device._dev.read.return_value = (bytes([0x02, 0x01, 29, 5, 0, 0, 1])
                                         + b"HID_ERROR_SLAVE_NO_SLAVE\x00")
        with self.assertRaisesRegex(RawRequestError, "SLAVE_NO_SLAVE"):
            _raw_request(device, 0x83, b"\x01")

    # --- menu_install.py ----------------------------------------------

    def test_exec_line_quotes_paths(self):
        import menu_install
        with tempfile.TemporaryDirectory() as tmp:
            apps = Path(tmp)
            with mock.patch.object(menu_install.sys, "executable", "/opt/my env/python"):
                menu_install.install_entry(apps, apps / "a.desktop", apps / "old.desktop",
                                           "astro-a50-gui", Path("/home/u/100% a/gui.py"))
            exec_line = next(line for line in (apps / "a.desktop").read_text().splitlines()
                             if line.startswith("Exec="))
        self.assertEqual(exec_line, 'Exec="/opt/my env/python" "/home/u/100%% a/gui.py"')

    def test_remove_entry_reports_a_failed_unlink(self):
        import menu_install
        with tempfile.TemporaryDirectory() as tmp:
            apps = Path(tmp)
            (apps / "a.desktop").write_text("[Desktop Entry]\nExec=python /src/gui.py\n")
            with (mock.patch.object(Path, "unlink", side_effect=PermissionError("ro")),
                  self.assertRaises(OSError)):
                menu_install.remove_entry(apps, apps / "a.desktop", apps / "old.desktop")

    def test_menu_messages_mention_a_packaged_entry_of_the_same_name(self):
        # Issue #19: the package's own entry stays in the menu after Remove.
        import menu_install
        from i18n import t
        with tempfile.TemporaryDirectory() as user, tempfile.TemporaryDirectory() as system:
            apps = Path(user) / "applications"
            args = (apps, apps / "a.desktop", apps / "old.desktop")
            # The user's own data dir may be listed too: its entry is not the package's.
            dirs = f"{user}:/nonexistent:{system}"
            with mock.patch.dict(os.environ, {"XDG_DATA_DIRS": dirs}):
                self.assertEqual(menu_install.install_entry(*args, "p", Path("/src/gui.py")),
                                 t("msg_menu_installed"))
                self.assertEqual(menu_install.remove_entry(*args), t("msg_menu_removed"))
                (Path(system) / "applications").mkdir()
                (Path(system) / "applications" / "a.desktop").write_text("x")
                self.assertEqual(menu_install.install_entry(*args, "p", Path("/src/gui.py")),
                                 t("msg_menu_installed_system"))
                self.assertEqual(menu_install.remove_entry(*args), t("msg_menu_removed_system"))
                self.assertEqual(menu_install.remove_entry(*args), t("msg_menu_absent_system"))

    def test_remove_keeps_the_menu_editors_copy_of_the_package_entry(self):
        # The menu editor saves an edited package entry under the same file name.
        import menu_install
        with tempfile.TemporaryDirectory() as tmp:
            apps = Path(tmp)
            edited = apps / "a.desktop"
            edited.write_text("[Desktop Entry]\nName=Mine\nExec=astro-a50-gui\n")
            (apps / "old.desktop").write_text("[Desktop Entry]\nExec=python /src/gui.py\n")
            menu_install.remove_entry(apps, edited, apps / "old.desktop")
            self.assertTrue(edited.exists())
            self.assertFalse((apps / "old.desktop").exists())

    def test_own_entry_ignores_spaces_around_the_equals_sign(self):
        # The Desktop Entry spec ignores them; another tool may write them.
        import menu_install
        with tempfile.TemporaryDirectory() as tmp:
            entry = Path(tmp) / "a.desktop"
            entry.write_text("[Desktop Entry]\nExec = python /src/gui.py\n")
            self.assertTrue(menu_install.own_entry(entry))
            entry.write_text("[Desktop Entry]\nExecAfter=gui.py\nExec=astro-a50-gui\n")
            self.assertFalse(menu_install.own_entry(entry))

    # --- process_lock.py ----------------------------------------------

    @staticmethod
    def _proc(root: Path, pid: int, *, comm: bytes, start: int, cmdline=b"", cwd="/"):
        d = root / str(pid)
        d.mkdir()
        (d / "comm").write_bytes(comm + b"\n")
        (d / "cmdline").write_bytes(cmdline)
        (d / "stat").write_bytes(f"{pid} (x) S".encode() + b" 0" * 18 + f" {start} 0".encode())
        (d / "exe").symlink_to("/usr/bin/python3")
        (d / "cwd").symlink_to(cwd)
        return d

    def test_instance_scan_survives_odd_processes_and_kills_only_older_ones(self):
        import process_lock
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            me = os.getpid()
            name = process_lock.PROCESS_NAME.encode()
            self._proc(root, me, comm=name, start=500)
            self._proc(root, 10, comm=name, start=100)            # older: stop it
            self._proc(root, 99999, comm=name, start=900)         # newer: leave it
            self._proc(root, 11, comm=b"\xff\xfe", start=50)      # non-UTF-8 comm
            found = process_lock._find_other_instances(Path("/x/gui.py"), proc=root)
        self.assertEqual(found, [10])

    def test_relative_script_resolves_against_the_process_cwd(self):
        import process_lock
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            other = root / "other"
            other.mkdir()
            (other / "gui.py").write_text("")
            ours = (root / "ours.py").resolve()
            d = self._proc(root, 12, comm=b"python3", start=1,
                           cmdline=b"python3\x00gui.py\x00", cwd=str(other))
            with mock.patch.object(process_lock.Path, "cwd", return_value=root):
                self.assertFalse(process_lock._is_our_instance(d, ours.with_name("gui.py")))
            self.assertTrue(process_lock._is_our_instance(d, (other / "gui.py").resolve()))

    def test_wait_for_a_foreign_process_does_not_raise(self):
        import process_lock
        with mock.patch.object(process_lock.os, "kill", side_effect=PermissionError()):
            self.assertTrue(process_lock._wait_for_exit(1, timeout_s=0.1))

    # --- status_worker.py ---------------------------------------------

    def test_worker_reopens_the_base_after_the_handle_died(self):
        import threading

        import usb.core

        import status_worker
        device = mock.MagicMock()
        device.get_headset_status.side_effect = [usb.core.USBError("gone"), "status"]
        device.get_battery_status.side_effect = [usb.core.USBError("gone"), "battery"]
        device.reopen.return_value = True
        worker = status_worker.StatusWorker.__new__(status_worker.StatusWorker)
        worker._device, worker._lock = device, threading.RLock()
        worker.statusReady = mock.MagicMock()
        worker.reconnected = mock.MagicMock()
        worker.refresh()
        device.reopen.assert_called_once_with()
        worker.statusReady.emit.assert_called_once_with("status", "battery")
        worker.reconnected.emit.assert_called_once()

    def test_worker_does_not_reopen_on_an_error_answer(self):
        import threading

        import status_worker
        device = mock.MagicMock()
        device.get_headset_status.side_effect = AssertionError()
        worker = status_worker.StatusWorker.__new__(status_worker.StatusWorker)
        worker._device, worker._lock = device, threading.RLock()
        worker.statusReady = mock.MagicMock()
        worker.reconnected = mock.MagicMock()
        worker.refresh()
        device.reopen.assert_not_called()

    def test_device_handle_swaps_in_a_new_device(self):
        from device_handle import DeviceHandle
        from vendor.eh_fifty import DeviceNotConnected
        first, second = mock.MagicMock(), mock.MagicMock()
        factory = mock.MagicMock(side_effect=[first, DeviceNotConnected(), second])
        handle = DeviceHandle(factory)
        handle.get_battery_status()
        first.get_battery_status.assert_called_once()
        self.assertFalse(handle.reopen())          # base still unplugged
        first.close.assert_called_once()
        with self.assertRaises(DeviceNotConnected):
            handle.get_battery_status()
        self.assertTrue(handle.reopen())           # plugged back
        handle.get_battery_status()
        second.get_battery_status.assert_called_once()

    def test_absent_base_fails_on_the_call_not_the_lookup(self):
        from device_handle import DeviceHandle
        from vendor.eh_fifty import DeviceNotConnected
        handle = DeviceHandle(mock.MagicMock(side_effect=[mock.MagicMock(), DeviceNotConnected()]))
        self.assertFalse(handle.reopen())
        method = handle.get_headset_status          # must not raise
        with self.assertRaises(DeviceNotConnected):
            method()
        self.assertIsNone(gui.safe(handle.get_headset_status))
        window = mock.MagicMock()
        window.device, window._device_lock = handle, mock.MagicMock()
        gui.A50Window.refresh_status(window)         # Refresh with the base unplugged
        window._update_status_display.assert_called_once_with(None, None)

    def test_reconnect_reloads_only_what_failed_to_load(self):
        window = mock.MagicMock()
        window.slider_widgets = {}
        for c in (window.sld_balance, window.cmb_gate, window.sld_alert):
            c.isEnabled.return_value = True
        gui.A50Window._on_reconnected(window)
        window.reload_all.assert_not_called()
        window.sld_alert.isEnabled.return_value = False
        gui.A50Window._on_reconnected(window)
        window.reload_all.assert_called_once()

    def test_failed_library_write_after_create_keeps_device_state(self):
        widget = EqWidgetPersistAndPushTest._make(self)
        btn = mock.MagicMock()
        with (mock.patch("eq_widget.QApplication"), mock.patch("eq_widget.QMessageBox") as box,
              mock.patch("eq_widget._save_user_templates", side_effect=OSError("ro"))):
            widget._persist_and_push(1, "NewMix", is_new=True, btn=btn,
                                     busy_key="btn_apply_busy", idle_key="btn_apply")
        self.assertEqual(widget._device_templates[1], "NewMix")
        btn.setText.assert_called_with(gui.t("btn_apply"))
        box.warning.assert_called_once()
        self.assertEqual(widget._slot_pending[1], set())

    def test_save_of_a_preset_shown_in_another_slot_pends_that_slot(self):
        media = templates._EQ_TEMPLATES["MEDIA"]
        mine = {"gain": list(media["gain"]), "bands": dict(media["bands"])}
        widget = EqWidgetPersistAndPushTest._make(
            self, user_templates={"Mine": mine}, combo_data="Mine")
        widget.template_combos[2] = _MockCombo(current_data="Mine")
        widget._slot_bands[2] = [(f, g) for f, g in
                                 EqWidgetPushPendingTest._bands_from_template("MEDIA")]
        widget._device_templates[2] = "Mine"
        widget._slot_device[2] = dict(mine, name="Mine")
        with mock.patch("eq_widget.QApplication"), mock.patch("eq_widget._save_user_templates"):
            widget._persist_and_push(1, "Mine", is_new=False, btn=mock.MagicMock(),
                                     busy_key="btn_save_eq_busy", idle_key="btn_save_eq")
        self.assertEqual(widget._slot_pending[2], {1, 2, 3, 4, 5})
        self.assertEqual(widget._slot_bands[2][2][1], 4)
        self.assertEqual(widget._slot_modified[2], set())

    def test_presets_the_device_took_are_saved_when_a_later_slot_fails(self):
        media = templates._EQ_TEMPLATES["MEDIA"]
        mine = {"gain": list(media["gain"]), "bands": dict(media["bands"])}
        edited = [(f, g + 1) for f, g in EqWidgetPushPendingTest._bands_from_template("MEDIA")]
        widget = EqWidgetPushPendingTest._make(
            self, user_templates={"Mine": dict(mine)}, combos={1: "Mine", 2: "PRO"},
            slot_bands={1: list(edited), 2: list(edited), 3: []},
            slot_pending={1: {1}, 2: {1}, 3: set()})
        widget.device.set_eq_preset_name.side_effect = [None, OSError("unplugged")]
        with mock.patch("eq_widget._save_user_templates") as save, self.assertRaises(OSError):
            widget.push_pending_to_device()
        save.assert_called_once()


class BalanceAndHelpTest(unittest.TestCase):
    def test_balance_slider_is_the_game_percentage(self):
        # eh-fifty: 0 is all game, 255 all voice.
        self.assertEqual(gui.game_percent(0), 100)
        self.assertEqual(gui.game_percent(255), 0)
        self.assertEqual(gui.game_percent(128), 50)
        for game in range(101):
            with self.subTest(game=game):
                self.assertEqual(gui.game_percent(gui.balance_from_game_percent(game)), game)

    def test_voice_icon_is_drawn_in_the_text_colour(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # CI has no display
        from PyQt6.QtGui import QColor, QImageReader
        from PyQt6.QtWidgets import QApplication
        type(self)._qt_app = QApplication.instance() or QApplication([])
        if b"svg" not in [bytes(f) for f in QImageReader.supportedImageFormats()]:
            self.skipTest("no Qt SVG plugin (qt6-svg): the app shows the microphone instead")
        pixmap = themes.svg_pixmap("voice", QColor("#ff0000"), 20)
        self.assertEqual((pixmap.width(), pixmap.height()), (20, 20))
        image = pixmap.toImage()
        colours = {image.pixelColor(x, y).name() for x in range(20) for y in range(20)
                   if image.pixelColor(x, y).alpha() == 255}
        self.assertEqual(colours, {"#ff0000"})
        self.assertTrue(themes.svg_pixmap("missing", QColor("#ff0000"), 20).isNull())

    def test_every_level_slider_has_help(self):
        for st, _label, tip in gui._slider_types():
            with self.subTest(slider=st.name):
                self.assertFalse(tip.startswith("tip_"))

    def test_every_language_has_every_string(self):
        # t() falls back to English, so a missing string would go unnoticed.
        english = set(i18n.TRANSLATIONS["en"])
        for lang, strings in i18n.TRANSLATIONS.items():
            with self.subTest(lang=lang):
                self.assertEqual(set(strings), english)

    def test_theme_icon_takes_the_first_name_the_theme_has(self):
        with mock.patch.object(themes.QIcon, "hasThemeIcon", side_effect=lambda n: n == "b"), \
                mock.patch.object(themes.QIcon, "fromTheme") as from_theme:
            themes.icon("a", "b")
            from_theme.assert_called_with("b")
            # None found: Qt's own fallback for the first name.
            themes.icon("x", "y")
            from_theme.assert_called_with("x")


if __name__ == "__main__":
    unittest.main()
