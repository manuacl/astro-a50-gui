"""Background worker for HID status polls.

Runs on a QThread so the main UI thread never blocks on USB I/O during the
periodic refresh. The shared lock serialises access to the device between this
thread and the main thread's explicit writes.
"""
import threading
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from vendor.eh_fifty import Device


class StatusWorker(QObject):
    """Polls the headset/battery status on demand from another thread."""

    statusReady = pyqtSignal(object, object)  # (HeadsetStatus|None, BatteryStatus|None)

    def __init__(self, device: Device, lock: threading.Lock):
        super().__init__()
        self._device = device
        self._lock = lock

    @pyqtSlot()
    def refresh(self) -> None:
        status: Optional[object] = None
        battery: Optional[object] = None
        with self._lock:
            try:
                status = self._device.get_headset_status()
            except Exception:
                status = None
            try:
                battery = self._device.get_battery_status()
            except Exception:
                battery = None
        self.statusReady.emit(status, battery)
