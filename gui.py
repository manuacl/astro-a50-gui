"""Minimal Qt GUI for configuring an Astro A50 Gen 4 via eh-fifty."""
import atexit
import os
import re
import signal
import sys
import threading
from contextlib import suppress
from pathlib import Path

from PyQt6.QtCore import (
    QEvent,
    QMetaObject,
    QProcess,
    QProcessEnvironment,
    Qt,
    QThread,
    QTimer,
)
from PyQt6.QtGui import QAction, QActionGroup, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

import settings
import themes
from base_info_dialog import format_base_info
from device_handle import DeviceHandle
from eq_widget import EqTemplatesWidget
from i18n import LANGUAGE_NAMES, gate_label, needs_restart, t
from menu_install import install_entry, own_entry, remove_entry
from process_lock import (
    PID_FILE,
    PROCESS_NAME,
    _kill_previous,
    _remove_pid_file,
    _set_process_name,
)
from raw_request import (
    _OP_BASE_FW_MINOR,
    _OP_DEVICE_INFO,
    _OP_FIRMWARE_INFO,
    _OP_HEADSET_FW_MAJOR,
    _OP_HEADSET_FW_MINOR,
    _raw_request,
)
from status_worker import StatusWorker
from vendor.eh_fifty import NoiseGateMode, SliderType

SCRIPT_PATH = Path(__file__).resolve()
# Installed by the package (in /usr/share/astro-a50-gui), which ships its own
# menu entry: Install/Remove in menu would only shadow or fail to remove it
# (issue #19).
PACKAGED = SCRIPT_PATH.parent == Path("/usr/share", PROCESS_NAME)
APPS_DIR = (
    Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    / "applications"
)
DESKTOP_FILE = APPS_DIR / f"{PROCESS_NAME}.desktop"
LEGACY_DESKTOP_FILE = APPS_DIR / "astro-a50-config.desktop"
REPO_URL = "https://github.com/manuacl/astro-a50-gui"


def app_version() -> str:
    """Version from the pyproject.toml shipped next to this file, or "?"."""
    with suppress(OSError):
        text = (SCRIPT_PATH.parent / "pyproject.toml").read_text()
        match = re.search(r'^version = "([^"]+)"$', text, re.MULTILINE)
        if match:
            return match.group(1)
    return "?"


def _slider_types():
    """Each level slider's type, label and hover help."""
    return [
        (SliderType.MIC, t("lbl_mic_level"), t("tip_mic_level")),
        (SliderType.SIDE_TONE, t("lbl_sidetone"), t("tip_sidetone")),
        (SliderType.STREAM_PORT_MIX_MIC, t("lbl_stream_mic"), t("tip_stream_mic")),
        (SliderType.STREAM_PORT_MIX_CHAT, t("lbl_stream_chat"), t("tip_stream_chat")),
        (SliderType.STREAM_PORT_MIX_GAME, t("lbl_stream_game"), t("tip_stream_game")),
        (SliderType.STREAM_PORT_MIX_AUX, t("lbl_stream_aux"), t("tip_stream_aux")),
    ]


def game_percent(balance: int) -> int:
    """eh-fifty's balance (0 = all game, 255 = all voice) as the game percentage."""
    return round((255 - balance) * 100 / 255)


def balance_from_game_percent(game: int) -> int:
    """The eh-fifty balance for a game percentage."""
    return 255 - round(game * 255 / 100)


def safe(call, default=None):
    try:
        return call()
    except Exception:
        return default


class A50Window(QMainWindow):
    REFRESH_INTERVAL_MS = 5000

    _SYNC_STYLE_DIRTY = (
        "QPushButton { background-color: #FF9800; color: white; "
        # Same 1px border as the synced style, transparent: the button keeps
        # its height, so the window doesn't shift when Sync changes state.
        "font-weight: bold; padding: 6px 14px; border-radius: 4px; "
        "border: 1px solid transparent; } "
        "QPushButton:hover { background-color: #F57C00; } "
        "QPushButton:pressed { background-color: #E65100; }"
    )
    # Palette roles, not fixed greys, so the muted look reads in any theme.
    _SYNC_STYLE_SYNCED = (
        "QPushButton { background-color: transparent; color: palette(placeholder-text); "
        "padding: 6px 14px; border-radius: 4px; border: 1px solid palette(mid); }"
    )

    def __init__(self, device: DeviceHandle):
        super().__init__()
        self.device = device
        self._loading = False
        # The settings controls' values as last read from or written to the
        # device: Sync is due while they differ (see _controls).
        self._synced: dict = {}
        # The device is shared between the main UI thread, the EQ widget,
        # and the status worker thread; the lock serialises USB HID access.
        # RLock allows nested acquisitions (reload_all wraps refresh_status).
        self._device_lock = threading.RLock()

        self.setWindowTitle(t("window_title"))
        window_icon = QIcon.fromTheme("audio-headset")
        if window_icon.isNull():
            window_icon = QIcon.fromTheme("audio-headphones")
        if not window_icon.isNull():
            self.setWindowIcon(window_icon)
        self.setMinimumWidth(480)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.eq = EqTemplatesWidget(device, self._device_lock, parent=self)
        self.eq.dirty_changed.connect(self._on_eq_dirty_changed)

        layout.addWidget(self._build_status_group())
        layout.addWidget(self._build_audio_group())
        layout.addWidget(self.eq)
        layout.addWidget(self._build_mic_group())
        layout.addWidget(self._build_sliders_group())
        layout.addWidget(self._build_alert_group())
        layout.addLayout(self._build_action_buttons())

        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())
        self._build_menu_bar()

        self._status_thread = QThread(self)
        self._status_worker = StatusWorker(device, self._device_lock)
        self._status_worker.moveToThread(self._status_thread)
        self._status_worker.statusReady.connect(self._on_status_ready)
        self._status_worker.reconnected.connect(self._on_reconnected)
        self._status_thread.start()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._trigger_async_status)
        self.refresh_timer.start(self.REFRESH_INTERVAL_MS)

        self.reload_all()

    def _build_status_group(self):
        box = QGroupBox(t("grp_status"))
        layout = QHBoxLayout(box)
        self.lbl_power = QLabel("—")
        self.lbl_dock = QLabel("—")
        self.lbl_battery = QLabel("—")
        for w in (self.lbl_power, self.lbl_dock, self.lbl_battery):
            layout.addWidget(w)
        layout.addStretch(1)
        return box

    def _build_audio_group(self):
        box = QGroupBox(t("grp_balance"))
        layout = QHBoxLayout(box)
        # The slider is the game percentage: voice on the left, game on the
        # right, like the headset's buttons. eh-fifty's 0-255 (0 = all game)
        # is only converted when reading from and writing to the device.
        self.sld_balance = QSlider(Qt.Orientation.Horizontal)
        self.sld_balance.setRange(0, 100)
        self.sld_balance.setSingleStep(1)
        self.sld_balance.setPageStep(5)
        self.sld_balance.setToolTip(t("tip_balance"))
        self.sld_balance.valueChanged.connect(self._on_balance_changed)
        self.lbl_voice_pct = QLabel("—")
        self.lbl_game_pct = QLabel("—")
        width = self.lbl_voice_pct.fontMetrics().horizontalAdvance("100%")
        for lbl in (self.lbl_voice_pct, self.lbl_game_pct):
            lbl.setMinimumWidth(width)
        self.lbl_voice_pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._icon_label("im-user", t("lbl_voice")))
        layout.addWidget(self.lbl_voice_pct)
        layout.addWidget(self.sld_balance, 1)
        layout.addWidget(self.lbl_game_pct)
        layout.addWidget(self._icon_label("input-gamepad", t("lbl_game")))
        return box

    def _show_balance(self, game: int | None) -> None:
        voice_text = t("na") if game is None else f"{100 - game}%"
        game_text = t("na") if game is None else f"{game}%"
        self.lbl_voice_pct.setText(voice_text)
        self.lbl_game_pct.setText(game_text)

    @staticmethod
    def _icon_label(icon_name: str, tooltip: str, size: int = 20) -> QLabel:
        lbl = QLabel()
        icon = QIcon.fromTheme(icon_name)
        if not icon.isNull():
            lbl.setPixmap(icon.pixmap(size, size))
        else:
            lbl.setText(tooltip)
        lbl.setToolTip(tooltip)
        return lbl


    def _build_mic_group(self):
        # No "Mic EQ" combo — eh_fifty exposes opcodes 0x71/0x7b but ACC on
        # A50 Gen 4 never uses them, and a live loopback test (mic→game sink
        # while toggling presets 0/1/2) found no audible difference. Feature
        # appears orphaned in the firmware.
        box = QGroupBox(t("grp_mic"))
        layout = QGridLayout(box)
        lbl = QLabel(t("lbl_noise_gate"))
        lbl.setToolTip(t("tip_noise_gate"))
        layout.addWidget(lbl, 0, 0)
        self.cmb_gate = QComboBox()
        self.cmb_gate.setToolTip(t("tip_noise_gate"))
        # Outline icons picturing what Command Center shows: antenna, moon,
        # house and trophy. hasThemeIcon, not fromTheme().isNull(): fromTheme
        # falls back to a shorter name (network-wireless for
        # network-wireless-hotspot), so the second choice would never be tried.
        gate_icons = {
            NoiseGateMode.STREAMING: ("network-wireless-hotspot", "camera-video"),
            NoiseGateMode.NIGHT: ("weather-clear-night-symbolic", "weather-clear-night"),
            NoiseGateMode.HOME: ("go-home", "user-home"),
            NoiseGateMode.TOURNAMENT: ("games-highscores", "applications-games"),
        }
        for m in NoiseGateMode:
            choices = gate_icons.get(m, ())
            found = [n for n in choices if QIcon.hasThemeIcon(n)] or choices
            icon = QIcon.fromTheme(found[0]) if found else QIcon()
            self.cmb_gate.addItem(icon, gate_label(m.name), m)
        self.cmb_gate.currentIndexChanged.connect(self._on_gate_changed)
        layout.addWidget(self.cmb_gate, 0, 1)
        return box

    def _build_sliders_group(self):
        box = QGroupBox(t("grp_levels"))
        layout = QGridLayout(box)
        self.slider_widgets = {}
        for row, (st, label, tip) in enumerate(_slider_types()):
            name = QLabel(label)
            name.setToolTip(tip)
            layout.addWidget(name, row, 0)
            sld = QSlider(Qt.Orientation.Horizontal)
            sld.setToolTip(tip)
            sld.setRange(0, 100)
            sld.setSingleStep(1)
            sld.setPageStep(5)
            lbl = QLabel("—")
            lbl.setMinimumWidth(48)
            sld.valueChanged.connect(lambda v, s=st, lab=lbl: self._on_slider_changed(s, v, lab))
            layout.addWidget(sld, row, 1)
            layout.addWidget(lbl, row, 2)
            self.slider_widgets[st] = (sld, lbl)
        return box

    def _build_alert_group(self):
        box = QGroupBox(t("grp_notifications"))
        layout = QGridLayout(box)
        lbl = QLabel(t("lbl_alert_volume"))
        lbl.setToolTip(t("tip_alert_volume"))
        layout.addWidget(lbl, 0, 0)
        self.sld_alert = QSlider(Qt.Orientation.Horizontal)
        self.sld_alert.setRange(0, 100)
        self.sld_alert.setToolTip(t("tip_alert_volume"))
        self.lbl_alert = QLabel("—")
        self.lbl_alert.setMinimumWidth(48)
        self.sld_alert.valueChanged.connect(self._on_alert_changed)
        layout.addWidget(self.sld_alert, 0, 1)
        layout.addWidget(self.lbl_alert, 0, 2)
        return box

    def _build_menu_bar(self):
        bar = self.menuBar()
        tools = bar.addMenu(t("menu_tools"))

        if not PACKAGED:
            act_install = QAction(t("act_install_menu"), self)
            act_install.triggered.connect(self._install_menu_entry)
            tools.addAction(act_install)

        # Packaged, Remove stays while an entry made from a checkout exists: it
        # shadows the package's own entry and nothing else would remove it.
        if not PACKAGED or own_entry(DESKTOP_FILE) or LEGACY_DESKTOP_FILE.exists():
            self._act_remove = QAction(t("act_remove_menu"), self)
            self._act_remove.triggered.connect(self._remove_menu_entry)
            tools.addAction(self._act_remove)
            tools.addSeparator()
        act_info = QAction(t("act_base_info"), self)
        act_info.triggered.connect(self._show_base_info)
        tools.addAction(act_info)

        tools.addSeparator()
        act_import = QAction(t("act_import_presets"), self)
        act_import.triggered.connect(self._import_presets)
        tools.addAction(act_import)
        act_export = QAction(t("act_export_preset"), self)
        act_export.triggered.connect(self._export_preset)
        tools.addAction(act_export)

        tools.addSeparator()
        tools.addMenu(self._build_language_menu(tools))
        tools.addMenu(self._build_theme_menu(tools))

        tools.addSeparator()
        act_about = QAction(t("act_about"), self)
        act_about.triggered.connect(self._show_about)
        tools.addAction(act_about)

        act_quit = QAction(t("act_quit"), self)
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)
        tools.addAction(act_quit)

    def _show_about(self):
        QMessageBox.about(
            self,
            # The menu label's "&" marks a shortcut; a window title would show it.
            t("act_about").replace("&", ""),
            t("about_text", version=app_version(), url=REPO_URL),
        )

    def _build_language_menu(self, parent) -> QMenu:
        menu = QMenu(t("menu_language"), parent)
        group = QActionGroup(menu)
        current = settings.get("language", "auto")
        self._lang_actions = {}
        for code, name in [("auto", t("lang_auto")), *LANGUAGE_NAMES.items()]:
            action = QAction(name, menu)
            action.setCheckable(True)
            action.setChecked(code == current)
            action.triggered.connect(lambda _=False, c=code: self._set_language(c))
            group.addAction(action)
            menu.addAction(action)
            self._lang_actions[code] = action
        return menu

    def _import_presets(self):
        try:
            imported = self.eq.import_presets()
            if imported:
                if not self.eq.has_pending():
                    # The base may have changed behind the app (Command Center,
                    # in a VM it was passed to): re-read the EQ slots and the
                    # active one. With unsynced EQ edits, which a reload would
                    # discard, the widget's own re-match is all there is.
                    with self._device_lock:
                        self.eq.reload_under_lock(safe(self.device.get_active_eq_preset))
                # Importing a preset means using it: load the first one into
                # the selected EQ slot, pending until synced.
                self.eq.load_into_selected_slot(imported[0])
        except Exception as e:
            QMessageBox.warning(self, t("err_title"), t("err_import", name="", error=e))
            return
        if imported:
            self.statusBar().showMessage(t("msg_imported", n=len(imported)), 4000)

    def _export_preset(self):
        try:
            name = self.eq.export_preset()
        except Exception as e:
            QMessageBox.warning(self, t("err_title"), t("err_export", error=e))
            return
        if name:
            self.statusBar().showMessage(t("msg_exported", name=name), 4000)

    def _save_setting(self, key: str, value) -> bool:
        """Store a preference; warn instead of crashing if ~/.config can't be written."""
        try:
            settings.put(key, value)
        except OSError as e:
            QMessageBox.warning(self, t("err_title"), t("err_settings_save", error=e))
            return False
        return True

    def _set_language(self, code: str):
        previous = settings.get("language", "auto")
        if code == previous:
            return
        if not self._save_setting("language", code):
            self._lang_actions.get(previous, self._lang_actions["auto"]).setChecked(True)
            return
        if not needs_restart(code):
            return  # already running in that language (A50_LANG, declined restart...)
        msg = t("dlg_restart_msg")
        if self._has_unsynced():
            msg += "\n\n" + t("dlg_restart_unsaved")
        reply = QMessageBox.question(self, t("dlg_restart_title"), msg)
        if reply == QMessageBox.StandardButton.Yes:
            if restart_app():
                self.close()
            else:
                QMessageBox.warning(self, t("err_title"), t("err_restart"))

    def _build_theme_menu(self, parent) -> QMenu:
        menu = QMenu(t("menu_theme"), parent)
        group = QActionGroup(menu)
        current = themes.current()  # what is really applied, even after a failed load
        self._theme_actions = {}
        for name in [themes.AUTO, *sorted(themes.available(), key=themes.label)]:
            action = QAction(themes.label(name), menu)
            action.setCheckable(True)
            action.setChecked(name == current)
            action.triggered.connect(lambda _=False, n=name: self._set_theme(n))
            group.addAction(action)
            menu.addAction(action)
            self._theme_actions[name] = action
        return menu

    def _set_theme(self, name: str):
        applied = themes.apply(QApplication.instance(), name)
        self._theme_actions[applied].setChecked(True)
        if applied != name:
            self.statusBar().showMessage(t("err_theme", name=themes.label(name)), 5000)
        self._save_setting("theme", applied)

    def _show_base_info(self):
        # Each read on its own: with the headset off or undocked, the headset
        # opcodes answer ERROR, which must not hide what the base did answer.
        # A failed read is shown as its exception, never as an empty string.
        def read(call):
            try:
                return call()
            except Exception as e:
                return e

        with self._device_lock:
            dev_info = read(self.device.get_device_info)
            base_fw = read(self.device.get_base_firmware_version)
            headset_fw = read(self.device.get_headset_firmware_version)
            raw = [
                (label, read(lambda op=op, arg=arg: _raw_request(self.device, op, arg)))
                for label, op, arg in (
                    ("0x03", _OP_DEVICE_INFO, b""),
                    ("0x83(01)", _OP_FIRMWARE_INFO, b"\x01"),
                    ("0x55", _OP_BASE_FW_MINOR, b""),
                    ("0xda(0a)", _OP_HEADSET_FW_MAJOR, b"\x0a"),
                    ("0xd6(0a)", _OP_HEADSET_FW_MINOR, b"\x0a"),
                )
            ]
        if all(isinstance(v, Exception) for v in (dev_info, base_fw, headset_fw)):
            QMessageBox.warning(self, t("err_title"), t("err_base_info", error=repr(dev_info)))
            return
        lines = format_base_info(dev_info, base_fw, headset_fw, raw)
        QMessageBox.information(self, t("act_base_info"), "<br>".join(lines))

    def _install_menu_entry(self):
        try:
            msg = install_entry(
                APPS_DIR, DESKTOP_FILE, LEGACY_DESKTOP_FILE,
                PROCESS_NAME, SCRIPT_PATH,
            )
            self.statusBar().showMessage(msg, 6000)
        except Exception as e:
            QMessageBox.warning(self, t("err_title"), t("err_menu_install", error=e))

    def _remove_menu_entry(self):
        try:
            msg = remove_entry(APPS_DIR, DESKTOP_FILE, LEGACY_DESKTOP_FILE)
        except OSError as e:
            QMessageBox.warning(self, t("err_title"), t("err_menu_remove", error=e))
            return
        if PACKAGED:
            self._act_remove.setVisible(False)  # nothing of ours is left to remove
        self.statusBar().showMessage(msg, 6000)

    def _build_action_buttons(self):
        row = QHBoxLayout()
        self.btn_refresh = QPushButton(t("btn_refresh"))
        self.btn_refresh.clicked.connect(self.reload_all)
        row.addWidget(self.btn_refresh)
        row.addStretch(1)
        self.btn_save = QPushButton(t("btn_save"))
        self.btn_save.clicked.connect(self._on_save)
        row.addWidget(self.btn_save)
        self._apply_sync_style()
        return row

    def _apply_sync_style(self):
        if not hasattr(self, "btn_save"):
            return
        dirty = self._has_unsynced()
        # In sync, the greyed button is also disabled: nothing to write.
        self.btn_save.setEnabled(dirty)
        if dirty:
            self.btn_save.setText(t("btn_save"))
            self.btn_save.setStyleSheet(self._SYNC_STYLE_DIRTY)
        else:
            self.btn_save.setText(t("btn_save_synced"))
            self.btn_save.setStyleSheet(self._SYNC_STYLE_SYNCED)

    def _controls(self) -> dict:
        """Each enabled settings control's value (a failed read disables it)."""
        values = {}
        for key, control in (("balance", self.sld_balance), ("alert", self.sld_alert)):
            if control.isEnabled():
                values[key] = control.value()
        if self.cmb_gate.isEnabled():
            values["gate"] = self.cmb_gate.currentData()
        for st, (sld, _lbl) in self.slider_widgets.items():
            if sld.isEnabled():
                values[st] = sld.value()
        return values

    def _has_unsynced(self) -> bool:
        """A setting differs from the device's, or the EQ has pending edits:
        moving a control back, or Reset on the EQ, turns Sync back (issue #22)."""
        return self._controls() != self._synced or self.eq.has_pending()

    def _settings_changed(self):
        if not self._loading:
            self._apply_sync_style()

    def reload_all(self):
        self._loading = True
        try:
            with self._device_lock:
                self.refresh_status()
                active = safe(self.device.get_active_eq_preset)
                self.eq.reload_under_lock(active)
                balance = safe(self.device.get_balance)
                gate = safe(self.device.get_noise_gate_mode)
                alert = safe(self.device.get_alert_volume)
                slider_values = {
                    st: safe(lambda st=st: self.device.get_slider_value(st))
                    for st in self.slider_widgets
                }

            # A control whose read failed is disabled, like the sliders below,
            # so Sync never writes a default that was never loaded.
            self.sld_balance.setEnabled(balance is not None)
            game = None if balance is None else game_percent(balance)
            if game is not None:
                self.sld_balance.setValue(game)
            # Explicit: setValue doesn't signal when the value is unchanged.
            self._show_balance(game)

            gate_idx = self.cmb_gate.findData(gate) if gate is not None else -1
            self.cmb_gate.setEnabled(gate_idx >= 0)
            if gate_idx >= 0:
                self.cmb_gate.setCurrentIndex(gate_idx)

            self.sld_alert.setEnabled(alert is not None)
            if alert is not None:
                self.sld_alert.setValue(alert)
                self.lbl_alert.setText(f"{alert}%")
            else:
                self.lbl_alert.setText(t("na"))

            for st, (sld, lbl) in self.slider_widgets.items():
                v = slider_values.get(st)
                if v is not None:
                    sld.setEnabled(True)
                    sld.setValue(v)
                    lbl.setText(f"{v}%")
                else:
                    sld.setEnabled(False)
                    lbl.setText(t("na"))

            self.statusBar().showMessage(t("msg_loaded"), 2000)
        finally:
            self._loading = False
        self._synced = self._controls()
        self._apply_sync_style()

    def _on_eq_dirty_changed(self, _is_dirty: bool):
        if not self._loading:
            self._apply_sync_style()

    def refresh_status(self):
        """Synchronous status refresh (used at startup and on user Rafraîchir)."""
        with self._device_lock:
            status = safe(self.device.get_headset_status)
            battery = safe(self.device.get_battery_status)
        self._update_status_display(status, battery)

    def _trigger_async_status(self):
        """Tell the worker thread to refresh status without blocking the UI."""
        QMetaObject.invokeMethod(
            self._status_worker, "refresh",
            Qt.ConnectionType.QueuedConnection,
        )

    def _on_status_ready(self, status, battery):
        """Slot called by the worker thread when a status poll completes."""
        self._update_status_display(status, battery)

    def _on_reconnected(self):
        """The worker reopened the base: load what the failed reads left out.

        Only then: a reload drops unsynced edits, and controls that were read
        fine keep what the user sees."""
        controls = [self.sld_balance, self.cmb_gate, self.sld_alert,
                    *(sld for sld, _ in self.slider_widgets.values())]
        if not all(c.isEnabled() for c in controls):
            self.reload_all()

    def _update_status_display(self, status, battery):
        if status is None:
            self.lbl_power.setText(t("base_unreachable"))
            self.lbl_dock.setText("")
            self.lbl_battery.setText("")
            return
        power_dot = "🟢" if status.is_on else "⚪"
        dock_dot = "🟢" if status.is_docked else "⚪"
        self.lbl_power.setText(f"{power_dot} {t('headset_on') if status.is_on else t('headset_off')}")
        self.lbl_dock.setText(f"{dock_dot} {t('docked') if status.is_docked else t('undocked')}")
        if battery is not None:
            charging = t("charging") if battery.is_charging else ""
            self.lbl_battery.setText(f"🔋 {battery.charge_percent}%{charging}")
        else:
            self.lbl_battery.setText("🔋 —")

    def _on_balance_changed(self, value: int):
        self._show_balance(value)
        if self._loading:
            return
        self._settings_changed()

    def _on_gate_changed(self, _):
        if self._loading:
            return
        if self.cmb_gate.currentData() is None:
            return
        self._settings_changed()

    def _on_alert_changed(self, value: int):
        self.lbl_alert.setText(f"{value}%")
        if self._loading:
            return
        self._settings_changed()

    def _on_slider_changed(self, slider_type, value: int, lbl: QLabel):
        lbl.setText(f"{value}%")
        if self._loading:
            return
        self._settings_changed()

    def _on_save(self):
        """Push the whole UI state to the device and persist with save_values()."""
        self.btn_save.setEnabled(False)
        self.btn_save.setText(t("btn_save_busy"))
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            with self._device_lock:
                # 1. Simple scalar settings (balance, gate, alert, sliders).
                # The slider shows the live balance (get_balance), which the
                # headset buttons change; write the default balance only when
                # the user moved the slider, so Sync never persists a button
                # adjustment as the power-on default. Compared in percent, so a
                # balance of 113 set with the buttons isn't rewritten as 112.
                game = self.sld_balance.value()
                if self.sld_balance.isEnabled() and game != self._synced.get("balance"):
                    self.device.set_default_balance(balance_from_game_percent(game))
                gate_mode = self.cmb_gate.currentData()
                if self.cmb_gate.isEnabled() and gate_mode is not None:
                    self.device.set_noise_gate_mode(gate_mode)
                if self.sld_alert.isEnabled():
                    self.device.set_alert_volume(self.sld_alert.value())
                for st, (sld, _) in self.slider_widgets.items():
                    if sld.isEnabled():
                        self.device.set_slider_value(st, sld.value())
                # 2. EQ template assignments + active-slot radio
                self.eq.push_pending_to_device()
                self.device.save_values()
            self._synced = self._controls()
            self.statusBar().showMessage(t("msg_saved"), 3000)
        except Exception as e:
            QMessageBox.warning(self, t("err_title"), t("err_save", error=e))
        finally:
            QApplication.restoreOverrideCursor()
            self._apply_sync_style()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange:
            if self.isActiveWindow():
                if not self.refresh_timer.isActive():
                    self.refresh_status()
                    self.refresh_timer.start(self.REFRESH_INTERVAL_MS)
            else:
                self.refresh_timer.stop()
        super().changeEvent(event)

    def closeEvent(self, event):
        self.refresh_timer.stop()
        self._status_thread.quit()
        self._status_thread.wait(2000)
        with suppress(Exception), self._device_lock:
            self.device.close()
        super().closeEvent(event)


def restart_app() -> bool:
    """Start a new instance of the GUI; it closes this one (see process_lock).

    A50_LANG is dropped from its environment, so the language picked in the
    menu takes effect. Returns False if the new process could not be started.
    """
    process = QProcess()
    env = QProcessEnvironment.systemEnvironment()
    env.remove("A50_LANG")
    process.setProcessEnvironment(env)
    process.setProgram(sys.executable)
    process.setArguments([str(SCRIPT_PATH)])
    started, _pid = process.startDetached()
    return started


def main():
    _set_process_name()
    _kill_previous(SCRIPT_PATH)
    PID_FILE.write_text(str(os.getpid()))
    atexit.register(_remove_pid_file)
    # No Qt thread exists yet, so exiting outright is safe until then.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    app = QApplication(sys.argv)
    # From here, close windows on SIGTERM so closeEvent stops the status
    # thread; sys.exit() would tear Qt down with it running and abort.
    # The close is queued to the event loop: it never cuts into a slot
    # mid-write, and a signal received during start-up closes the window
    # once it is shown (closeAllWindows skips hidden windows). Python runs
    # signal handlers only between bytecodes, so the timer hands control
    # back to the interpreter while the Qt loop is idle.
    signal.signal(signal.SIGTERM, lambda *_: QTimer.singleShot(0, app.closeAllWindows))
    sigterm_timer = QTimer()
    sigterm_timer.timeout.connect(lambda: None)
    sigterm_timer.start(500)
    # Help KDE / Wayland associate the window with the .desktop entry so
    # the headset icon survives across windows / taskbar / Alt-Tab.
    app.setApplicationName(PROCESS_NAME)
    app.setDesktopFileName(PROCESS_NAME)
    app_icon = QIcon.fromTheme("audio-headset")
    if app_icon.isNull():
        app_icon = QIcon.fromTheme("audio-headphones")
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    themes.apply(app, settings.get("theme", themes.AUTO))
    try:
        device = DeviceHandle()
    except Exception as e:
        QMessageBox.critical(None, t("err_open_title"), t("err_open", error=e))
        return 1
    window = A50Window(device)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
