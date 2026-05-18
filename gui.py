"""Minimal Qt GUI for configuring an Astro A50 Gen 4 via eh-fifty."""
import atexit
import os
import signal
import sys
import threading
from contextlib import suppress
from pathlib import Path

from PyQt6.QtCore import Qt, QEvent, QMetaObject, QThread, QTimer
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QSlider, QComboBox, QPushButton, QStatusBar, QMessageBox,
)

from eh_fifty import Device, NoiseGateMode, SliderType

from base_info_dialog import format_base_info
from eq_widget import EqTemplatesWidget
from i18n import t
from menu_install import install_entry, remove_entry
from process_lock import (
    PID_FILE, PROCESS_NAME,
    _kill_previous, _remove_pid_file, _set_process_name,
)
from raw_request import (
    _OP_BASE_FW_MINOR, _OP_DEVICE_INFO, _OP_FIRMWARE_INFO,
    _OP_HEADSET_FW_MAJOR, _OP_HEADSET_FW_MINOR,
    _raw_request,
)
from status_worker import StatusWorker


SCRIPT_PATH = Path(__file__).resolve()
APPS_DIR = Path.home() / ".local" / "share" / "applications"
DESKTOP_FILE = APPS_DIR / f"{PROCESS_NAME}.desktop"
LEGACY_DESKTOP_FILE = APPS_DIR / "astro-a50-config.desktop"


def _slider_types():
    return [
        (SliderType.MIC, t("lbl_mic_level")),
        (SliderType.SIDE_TONE, t("lbl_sidetone")),
        (SliderType.STREAM_PORT_MIX_MIC, t("lbl_stream_mic")),
        (SliderType.STREAM_PORT_MIX_CHAT, t("lbl_stream_chat")),
        (SliderType.STREAM_PORT_MIX_GAME, t("lbl_stream_game")),
        (SliderType.STREAM_PORT_MIX_AUX, t("lbl_stream_aux")),
    ]


def safe(call, default=None):
    try:
        return call()
    except Exception:
        return default


class A50Window(QMainWindow):
    REFRESH_INTERVAL_MS = 5000

    _SYNC_STYLE_DIRTY = (
        "QPushButton { background-color: #FF9800; color: white; "
        "font-weight: bold; padding: 6px 14px; border-radius: 4px; border: none; } "
        "QPushButton:hover { background-color: #F57C00; } "
        "QPushButton:pressed { background-color: #E65100; }"
    )
    _SYNC_STYLE_SYNCED = (
        "QPushButton { background-color: transparent; color: #666; "
        "padding: 6px 14px; border-radius: 4px; border: 1px solid #ccc; }"
    )

    def __init__(self, device: Device):
        super().__init__()
        self.device = device
        self._loading = False
        self._dirty = False
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
        self._status_thread.start()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._trigger_async_status)
        self.refresh_timer.start(self.REFRESH_INTERVAL_MS)

        self.reload_all()

    def _build_status_group(self):
        box = QGroupBox(t("grp_status"))
        l = QHBoxLayout(box)
        self.lbl_power = QLabel("—")
        self.lbl_dock = QLabel("—")
        self.lbl_battery = QLabel("—")
        for w in (self.lbl_power, self.lbl_dock, self.lbl_battery):
            l.addWidget(w)
        l.addStretch(1)
        return box

    def _build_audio_group(self):
        box = QGroupBox(t("grp_audio"))
        l = QGridLayout(box)
        l.addWidget(QLabel(t("lbl_balance")), 0, 0)
        bal_row = QHBoxLayout()
        self.sld_balance = QSlider(Qt.Orientation.Horizontal)
        self.sld_balance.setRange(0, 255)
        self.sld_balance.setSingleStep(1)
        self.sld_balance.setPageStep(16)
        self.sld_balance.valueChanged.connect(self._on_balance_changed)
        self.lbl_balance = QLabel("—")
        self.lbl_balance.setMinimumWidth(64)
        bal_row.addWidget(self._icon_label("input-gamepad", t("lbl_game")))
        bal_row.addWidget(self.sld_balance, 1)
        bal_row.addWidget(self._icon_label("audio-input-microphone", t("lbl_voice")))
        bal_row.addWidget(self.lbl_balance)
        l.addLayout(bal_row, 0, 1)
        return box

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
        l = QGridLayout(box)
        l.addWidget(QLabel(t("lbl_noise_gate")), 0, 0)
        self.cmb_gate = QComboBox()
        gate_icons = {
            NoiseGateMode.STREAMING: ("camera-video", "media-record"),
            NoiseGateMode.NIGHT: ("weather-clear-night", "view-night"),
            NoiseGateMode.HOME: ("go-home", "user-home"),
            NoiseGateMode.TOURNAMENT: ("applications-games", "games-config-options"),
        }
        for m in NoiseGateMode:
            icon = QIcon()
            for theme_name in gate_icons.get(m, ()):
                icon = QIcon.fromTheme(theme_name)
                if not icon.isNull():
                    break
            self.cmb_gate.addItem(icon, m.name, m)
        self.cmb_gate.currentIndexChanged.connect(self._on_gate_changed)
        l.addWidget(self.cmb_gate, 0, 1)
        return box

    def _build_sliders_group(self):
        box = QGroupBox(t("grp_levels"))
        l = QGridLayout(box)
        self.slider_widgets = {}
        for row, (st, label) in enumerate(_slider_types()):
            l.addWidget(QLabel(label), row, 0)
            sld = QSlider(Qt.Orientation.Horizontal)
            sld.setRange(0, 100)
            sld.setSingleStep(1)
            sld.setPageStep(5)
            lbl = QLabel("—")
            lbl.setMinimumWidth(48)
            sld.valueChanged.connect(lambda v, s=st, lab=lbl: self._on_slider_changed(s, v, lab))
            l.addWidget(sld, row, 1)
            l.addWidget(lbl, row, 2)
            self.slider_widgets[st] = (sld, lbl)
        return box

    def _build_alert_group(self):
        box = QGroupBox(t("grp_notifications"))
        l = QGridLayout(box)
        l.addWidget(QLabel(t("lbl_alert_volume")), 0, 0)
        self.sld_alert = QSlider(Qt.Orientation.Horizontal)
        self.sld_alert.setRange(0, 100)
        self.lbl_alert = QLabel("—")
        self.lbl_alert.setMinimumWidth(48)
        self.sld_alert.valueChanged.connect(self._on_alert_changed)
        l.addWidget(self.sld_alert, 0, 1)
        l.addWidget(self.lbl_alert, 0, 2)
        return box

    def _build_menu_bar(self):
        bar = self.menuBar()
        tools = bar.addMenu(t("menu_tools"))

        act_install = QAction(t("act_install_menu"), self)
        act_install.triggered.connect(self._install_menu_entry)
        tools.addAction(act_install)

        act_remove = QAction(t("act_remove_menu"), self)
        act_remove.triggered.connect(self._remove_menu_entry)
        tools.addAction(act_remove)

        tools.addSeparator()
        act_info = QAction(t("act_base_info"), self)
        act_info.triggered.connect(self._show_base_info)
        tools.addAction(act_info)

        tools.addSeparator()
        act_quit = QAction(t("act_quit"), self)
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)
        tools.addAction(act_quit)

    def _show_base_info(self):
        try:
            with self._device_lock:
                dev_info = _raw_request(self.device, _OP_DEVICE_INFO)
                fw_info = _raw_request(self.device, _OP_FIRMWARE_INFO, b"\x01")
                base_minor = _raw_request(self.device, _OP_BASE_FW_MINOR)
                hs_major = _raw_request(self.device, _OP_HEADSET_FW_MAJOR, b"\x0a")
                hs_minor = _raw_request(self.device, _OP_HEADSET_FW_MINOR, b"\x0a")
        except Exception as e:
            QMessageBox.warning(self, t("err_title"), t("err_base_info", error=e))
            return
        lines = format_base_info(dev_info, fw_info, base_minor, hs_major, hs_minor)
        QMessageBox.information(self, t("act_base_info"), "<br>".join(lines))

    def _install_menu_entry(self):
        try:
            msg = install_entry(
                APPS_DIR, DESKTOP_FILE, LEGACY_DESKTOP_FILE,
                PROCESS_NAME, SCRIPT_PATH,
            )
            self.statusBar().showMessage(msg, 3000)
        except Exception as e:
            QMessageBox.warning(self, t("err_title"), t("err_menu_install", error=e))

    def _remove_menu_entry(self):
        msg = remove_entry(APPS_DIR, DESKTOP_FILE, LEGACY_DESKTOP_FILE)
        self.statusBar().showMessage(msg, 3000)

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
        if self._dirty:
            self.btn_save.setText(t("btn_save"))
            self.btn_save.setStyleSheet(self._SYNC_STYLE_DIRTY)
        else:
            self.btn_save.setText(t("btn_save_synced"))
            self.btn_save.setStyleSheet(self._SYNC_STYLE_SYNCED)

    def _mark_dirty(self):
        if self._loading or self._dirty:
            return
        self._dirty = True
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

            if balance is not None:
                self.sld_balance.setValue(balance)
                self.lbl_balance.setText(f"{balance}/255")

            if gate is not None:
                idx = self.cmb_gate.findData(gate)
                if idx >= 0:
                    self.cmb_gate.setCurrentIndex(idx)

            if alert is not None:
                self.sld_alert.setValue(alert)
                self.lbl_alert.setText(f"{alert}%")

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
        self._dirty = False
        self._apply_sync_style()

    def _on_eq_dirty_changed(self, is_dirty: bool):
        if is_dirty and not self._loading:
            self._mark_dirty()

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
        self.lbl_balance.setText(f"{value}/255")
        if self._loading:
            return
        self._mark_dirty()

    def _on_gate_changed(self, _):
        if self._loading:
            return
        mode = self.cmb_gate.currentData()
        if mode is None:
            return
        self.statusBar().showMessage(t("msg_gate_set", name=mode.name), 2000)
        self._mark_dirty()

    def _on_alert_changed(self, value: int):
        self.lbl_alert.setText(f"{value}%")
        if self._loading:
            return
        self._mark_dirty()

    def _on_slider_changed(self, slider_type, value: int, lbl: QLabel):
        lbl.setText(f"{value}%")
        if self._loading:
            return
        self._mark_dirty()

    def _on_save(self):
        """Push the whole UI state to the device and persist with save_values()."""
        self.btn_save.setEnabled(False)
        self.btn_save.setText(t("btn_save_busy"))
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            with self._device_lock:
                # 1. Simple scalar settings (balance, gate, alert, sliders)
                self.device.set_default_balance(self.sld_balance.value())
                gate_mode = self.cmb_gate.currentData()
                if gate_mode is not None:
                    self.device.set_noise_gate_mode(gate_mode)
                self.device.set_alert_volume(self.sld_alert.value())
                for st, (sld, _) in self.slider_widgets.items():
                    if sld.isEnabled():
                        self.device.set_slider_value(st, sld.value())
                # 2. EQ template assignments + active-slot radio
                self.eq.push_pending_to_device()
                self.device.save_values()
            self._dirty = False
            self.statusBar().showMessage(t("msg_saved"), 3000)
        except Exception as e:
            QMessageBox.warning(self, t("err_title"), t("err_save", error=e))
        finally:
            QApplication.restoreOverrideCursor()
            self.btn_save.setEnabled(True)
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
        with suppress(Exception):
            with self._device_lock:
                self.device.close()
        super().closeEvent(event)


def main():
    _set_process_name()
    _kill_previous(SCRIPT_PATH)
    PID_FILE.write_text(str(os.getpid()))
    atexit.register(_remove_pid_file)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    app = QApplication(sys.argv)
    # Help KDE / Wayland associate the window with the .desktop entry so
    # the headset icon survives across windows / taskbar / Alt-Tab.
    app.setApplicationName(PROCESS_NAME)
    app.setDesktopFileName(PROCESS_NAME)
    app_icon = QIcon.fromTheme("audio-headset")
    if app_icon.isNull():
        app_icon = QIcon.fromTheme("audio-headphones")
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    try:
        device = Device()
    except Exception as e:
        QMessageBox.critical(None, t("err_open_title"), t("err_open", error=e))
        return 1
    window = A50Window(device)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
