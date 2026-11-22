"""Background worker for HID status polls.

Each poll reads the game/voice balance and the active EQ slot, which the
headset buttons change: the base sends nothing when they are pressed, so
polling is the only way to follow them (issue #16). The headset and battery
status change slowly and the status is slow to read (it goes over the air,
about 180 ms against 50 ms for the others), so they are read every few polls.

Runs on a QThread so the main UI thread never blocks on USB I/O during the
periodic refresh. The shared lock serialises access to the device between this
thread and the main thread's explicit writes.
"""
import threading

import usb.core
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from device_handle import DeviceHandle
from vendor.eh_fifty import DeviceNotConnected


class StatusWorker(QObject):
    """Polls the headset/battery status, the balance and the active EQ slot
    on demand from another thread."""

    statusReady = pyqtSignal(object, object)  # (HeadsetStatus|None, BatteryStatus|None)
    liveValuesReady = pyqtSignal(object, object)  # (balance|None, active EQ slot|None)
    reconnected = pyqtSignal()  # the handle was reopened after a USB failure

    # The headset and battery status are read on the first poll and then
    # once every this many polls: every 5 s at the window's 250 ms.
    STATUS_EVERY = 20

    def __init__(self, device: DeviceHandle, lock: threading.RLock):
        super().__init__()
        self._device = device
        self._lock = lock
        self._polls = 0

    @pyqtSlot()
    def refresh(self) -> None:
        with_status = self._polls % self.STATUS_EVERY == 0
        self._polls += 1
        with self._lock:
            reads, usb_error = self._poll(with_status)
            reopened = usb_error and self._device.reopen()
            if reopened:
                with_status = True
                reads, _ = self._poll(with_status)
        balance, active, status, battery = reads
        self.liveValuesReady.emit(balance, active)
        if with_status:
            self.statusReady.emit(status, battery)
        if reopened:
            self.reconnected.emit()

    def _poll(self, with_status: bool):
        """Each read in turn; a failed or skipped one gives None."""
        reads = [None] * 4
        usb_error = False
        calls = [self._device.get_balance, self._device.get_active_eq_preset]
        if with_status:
            calls += [self._device.get_headset_status, self._device.get_battery_status]
        for index, read in enumerate(calls):
            try:
                reads[index] = read()
            except Exception as e:
                usb_error = usb_error or _is_usb_failure(e)
        return reads, usb_error


def _is_usb_failure(error: Exception) -> bool:
    """The handle itself is dead (unplug, suspend, reset after a timeout) or
    the base is still absent, as opposed to an ERROR answer from a live base."""
    return isinstance(error, (usb.core.USBError, DeviceNotConnected))
