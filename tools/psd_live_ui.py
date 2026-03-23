#!/usr/bin/env python3

import sys
import numpy as np
from scipy.signal import find_peaks  # Añadido
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer


def main() -> int:
    app = QApplication(sys.argv)
    print(f"[PSD_LIVE_UI] python={sys.executable}")

    import pyqtgraph as pg

    # Import after QApplication creation to avoid any accidental QWidget
    # construction during module import side effects.
    from UI import PSDLiveUI

    window = PSDLiveUI()

    psd_curve = window.plot_widget.plot(pen=pg.mkPen(color="y", width=1.5), name="PSD")

    noise_floor_curve = window.plot_widget.plot(
        pen=pg.mkPen(color=(255, 80, 80), width=1.2),
        name="Noise Floor",
    )

    # Añadido: Curva para los picos (símbolos 'o' verdes, sin línea)
    peaks_curve = window.plot_widget.plot(
        pen=None, symbol='o', symbolSize=6, symbolBrush='g', name="Peaks"
    )

    def tick() -> None:
        plot_obj, x_axis, y_axis = window.acquire_plot_data(psd_curve)

        if x_axis is None or y_axis is None:
            return

        window.refresh_plot(plot_obj, x_axis, y_axis)

        noise_floor = float(np.percentile(y_axis, 10)) + (np.max(y_axis)-np.min(y_axis)) * 0.05
        noise_floor_y = np.full_like(y_axis, noise_floor, dtype=float)
        window.refresh_plot(noise_floor_curve, x_axis, noise_floor_y)

        # Añadido: Buscar picos usando el noise floor como altura mínima
        # Nota: Puedes agregar 'prominence=3' o 'distance=10' si detecta demasiado ruido
        peaks_idx, _ = find_peaks(y_axis, height=noise_floor)

        if len(peaks_idx) > 0:
            window.refresh_plot(peaks_curve, x_axis[peaks_idx], y_axis[peaks_idx])
        else:
            window.refresh_plot(peaks_curve, [], []) # Limpia los picos si no hay ninguno

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(50)

    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())