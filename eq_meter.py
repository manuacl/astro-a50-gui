"""Interactive 5-band EQ bargraph widget (ACC-style)."""
from PyQt6.QtCore import Qt, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPalette
from PyQt6.QtWidgets import QWidget


class _EqMeter(QWidget):
    """Interactive 5-band EQ bargraph (ACC style).

    Bands are draggable vertically to set the gain. Default colour is grey;
    bands modified by the user (or marked as modified externally) render in
    orange to match the sync button's "dirty" colour.
    """

    _MAX_GAIN = 7  # matches _EQ_PRESET_MAX_GAIN in eh_fifty
    _COLOR_DEFAULT = QColor(150, 150, 150)
    _COLOR_MODIFIED = QColor("#FF9800")
    _MARGIN_TOP = 20
    _MARGIN_BOTTOM = 22

    bandModified = pyqtSignal(int, int)  # (band 1..5, new_gain dB)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bands: list[tuple[int, int]] = []  # (freq_hz, gain_db)
        self._modified: set[int] = set()
        self._dragging_band: int | None = None
        self.setMinimumHeight(160)
        self.setMinimumWidth(220)

    def set_state(self, bands: list[tuple[int, int]], modified: set[int]) -> None:
        self._bands = list(bands)
        self._modified = set(modified)
        self.update()

    def clear(self) -> None:
        self._bands = []
        self._modified = set()
        self.update()

    def _band_zone(self) -> tuple[float, float]:
        """Return (center_y, half_height) of the drawing area in pixels."""
        h = self.height()
        top = self._MARGIN_TOP
        bottom = h - self._MARGIN_BOTTOM
        return (top + bottom) / 2, (bottom - top) / 2

    def _band_at_x(self, x: float) -> int | None:
        if not self._bands:
            return None
        w = self.width()
        cell = w / 5
        i = int(x // cell)
        if 0 <= i < 5:
            return i + 1
        return None

    def _gain_at_y(self, y: float) -> int:
        center_y, half = self._band_zone()
        normalized = (center_y - y) / half if half > 0 else 0.0
        return round(max(-1.0, min(1.0, normalized)) * self._MAX_GAIN)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        band = self._band_at_x(event.position().x())
        if band is None:
            return
        self._dragging_band = band
        self._apply_drag(event.position().y())

    def mouseMoveEvent(self, event):
        if self._dragging_band is None:
            return
        self._apply_drag(event.position().y())

    def mouseReleaseEvent(self, event):
        self._dragging_band = None

    def _apply_drag(self, y: float) -> None:
        band = self._dragging_band
        if band is None:
            return
        new_gain = self._gain_at_y(y)
        i = band - 1
        if not self._bands or self._bands[i][1] == new_gain:
            return
        freq, _old = self._bands[i]
        self._bands[i] = (freq, new_gain)
        self._modified.add(band)
        self.update()
        self.bandModified.emit(band, new_gain)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        center_y, half = self._band_zone()
        bar_w = max(8, (w / 5) * 0.45)
        # Theme-aware text color (works on both light and dark KDE themes)
        text_color = self.palette().color(QPalette.ColorRole.WindowText)
        muted_color = QColor(text_color)
        muted_color.setAlpha(140)
        # Center line
        p.setPen(muted_color)
        p.drawLine(0, int(center_y), w, int(center_y))
        if not self._bands:
            p.setPen(muted_color)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "—")
            return
        label_font = QFont(self.font())
        label_font.setBold(True)
        p.setFont(label_font)
        for i, (freq, gain) in enumerate(self._bands):
            band_no = i + 1
            color = self._COLOR_MODIFIED if band_no in self._modified else self._COLOR_DEFAULT
            x_center = w * (i + 0.5) / 5
            normalized = max(-1.0, min(1.0, gain / self._MAX_GAIN))
            bar_h = abs(normalized) * half
            if gain >= 0:
                top = center_y - bar_h
            else:
                top = center_y
            p.fillRect(QRectF(x_center - bar_w / 2, top, bar_w, bar_h), color)
            p.setPen(text_color)
            p.drawText(
                QRectF(x_center - 28, 0, 56, self._MARGIN_TOP),
                Qt.AlignmentFlag.AlignCenter,
                f"{gain:+d}",
            )
            label = f"{freq / 1000:.1f}k" if freq >= 1000 else f"{freq}"
            p.drawText(
                QRectF(x_center - 32, h - self._MARGIN_BOTTOM, 64, self._MARGIN_BOTTOM),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )
