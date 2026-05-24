"""Unit tests for the GUI's pure logic.

Run with:
    .venv/bin/python -m unittest tests.py
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import i18n
import raw_request
import templates
from eq_widget import EqTemplatesWidget


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


class HelpersTest(unittest.TestCase):
    def test_bcd_decode(self):
        self.assertEqual(raw_request._bcd(0x00), 0)
        self.assertEqual(raw_request._bcd(0x12), 12)
        self.assertEqual(raw_request._bcd(0x99), 99)

    def test_decode_datetime(self):
        # 2026-05-17 14:30:00 → LE year=0x07EA, BCD 05 17 14 30 00
        buf = bytes([0xEA, 0x07, 0x05, 0x17, 0x14, 0x30, 0x00])
        self.assertEqual(raw_request._decode_datetime(buf), "2026-05-17 14:30:00")

    def test_decode_datetime_too_short(self):
        self.assertEqual(raw_request._decode_datetime(b"\x00\x00"), "?")


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

    def test_every_fr_key_has_an_en_counterpart(self):
        missing = set(i18n.TRANSLATIONS["fr"]) - set(i18n.TRANSLATIONS["en"])
        self.assertEqual(missing, set(), f"keys missing in EN: {missing}")


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
        if not self._items:
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

    def setCurrentIndex(self, idx):
        if 0 <= idx < len(self._items):
            self._current = idx
        self.set_current_index_log.append((idx, self._signals_blocked))
        if not self._signals_blocked and self._signal_handler is not None:
            self._signal_handler()

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

    def _make_widget(self, device_data: dict[int, dict | None],
                     *, user_templates: dict | None = None):
        widget = EqTemplatesWidget.__new__(EqTemplatesWidget)
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


if __name__ == "__main__":
    unittest.main()
