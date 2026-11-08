"""Advanced EQ settings: each band's centre frequency and, for bands 2-4, its
bandwidth. The meter edits the gains."""
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

import astroeq
from i18n import t


class AdvancedEqDialog(QDialog):
    """Frequency and bandwidth of every band of one EQ slot."""

    def __init__(self, slot: int, bands: list[tuple[int, int]], parent=None):
        """`bands`: (frequency, raw bandwidth) of bands 1-5, as eh-fifty stores them."""
        super().__init__(parent)
        self.setWindowTitle(t("dlg_advanced_title", n=slot))
        self._original = dict(enumerate(bands, start=1))
        self._freq: dict[int, QSpinBox] = {}
        self._bw: dict[int, QDoubleSpinBox] = {}
        self._shown: dict[int, tuple[int, float]] = {}
        grid = QGridLayout()
        for col, key in enumerate(("col_band", "col_freq", "col_bw")):
            grid.addWidget(QLabel(f"<b>{t(key)}</b>"), 0, col)
        for b, (freq, bw) in self._original.items():
            grid.addWidget(QLabel(t("band_n", n=b)), b, 0)
            spin = QSpinBox()
            spin.setRange(*astroeq.FREQ_RANGE)
            # The arrows and the wheel step by a power of ten below the value
            # (10 Hz around 100, 100 Hz around 2000): 1 Hz steps up to 15000
            # would take forever.
            spin.setStepType(QAbstractSpinBox.StepType.AdaptiveDecimalStepType)
            spin.setSuffix(" Hz")
            spin.setValue(freq)
            spin.setToolTip(t("tip_freq"))
            grid.addWidget(spin, b, 1)
            self._freq[b] = spin
            if b in astroeq.SHELVES:
                shelf = QLabel("—")
                shelf.setToolTip(t("tip_shelf"))
                grid.addWidget(shelf, b, 2)
                continue
            width = QDoubleSpinBox()
            width.setRange(*astroeq.BANDWIDTH_RANGE)
            width.setDecimals(2)
            width.setSingleStep(0.1)
            width.setValue(bw / astroeq.BANDWIDTH_SCALE)
            width.setToolTip(t("tip_bw"))
            grid.addWidget(width, b, 2)
            self._bw[b] = width
        # What the boxes show, once clamped to the limits and rounded to their
        # decimals: a box still showing it was left untouched.
        self._shown = {b: (self._freq[b].value(), self._bw[b].value() if b in self._bw else 0)
                       for b in self._original}
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(grid)
        layout.addWidget(buttons)

    def values(self) -> dict[int, tuple[int, int]]:
        """(frequency, raw bandwidth) per band, 0 for the shelves' bandwidth.

        A value left as shown keeps its exact original, so pressing OK without
        touching anything changes nothing, even where the box rounds it
        (eh-fifty's minimum, 409, shows as 0.10) or clamps it to the limits."""
        values = {}
        for b, (freq, bw) in self._original.items():
            shown_freq, shown_bw = self._shown[b]
            new_freq = self._freq[b].value()
            if b not in self._bw:
                bw = 0
            elif self._bw[b].value() != shown_bw:
                bw = astroeq.raw_bandwidth(self._bw[b].value())
            values[b] = (freq if new_freq == shown_freq else new_freq, bw)
        return values
