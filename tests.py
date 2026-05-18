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


if __name__ == "__main__":
    unittest.main()
