#!/usr/bin/env python3

import math
import sys
import numpy as np
#from scipy.signal import find_peaks
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
    debug_state = {
        "tick_count": 0,
        "last_signature": None,
        "last_bins": None,
    }

    def _format_samples(values: np.ndarray, edge_size: int = 6) -> str:
        if values.size == 0:
            return "[]"

        flat = np.asarray(values, dtype=float).reshape(-1)
        if flat.size <= edge_size * 2:
            return np.array2string(flat, precision=3, separator=", ", suppress_small=False)

        head = np.array2string(flat[:edge_size], precision=3, separator=", ", suppress_small=False)
        tail = np.array2string(flat[-edge_size:], precision=3, separator=", ", suppress_small=False)
        return f"{head} ... {tail}"

    def _expected_psd_bins() -> int | None:
        runtime_config = window._get_runtime_config()
        if not runtime_config:
            return None

        sample_rate_hz = float(runtime_config.get("sample_rate_hz") or 0.0)
        rbw_hz = float(runtime_config.get("rbw_hz") or 0.0)
        window_name = str(runtime_config.get("window") or "").strip().lower()
        enbw_factor = {
            "hann": 1.500,
            "hamming": 1.363,
            "blackman": 1.730,
        }.get(window_name, 1.363)

        if sample_rate_hz <= 0.0 or rbw_hz <= 0.0:
            return None

        required_bins = max(1.0, enbw_factor * sample_rate_hz / rbw_hz)
        return max(256, 1 << int(math.ceil(math.log2(required_bins))))

    def _should_log(signature: tuple, has_issue: bool, tick_no: int) -> bool:
        if tick_no <= 5:
            return True
        if signature != debug_state["last_signature"]:
            return True
        if has_issue:
            return (tick_no % 10) == 0
        return (tick_no % 40) == 0

    """
    noise_floor_curve = window.plot_widget.plot(
        pen=pg.mkPen(color=(255, 80, 80), width=1.2),
        name="Noise Floor",
    )
    # Añadido: Curva para los picos (símbolos 'o' verdes, sin línea)
    peaks_curve = window.plot_widget.plot(
        pen=None, symbol='o', symbolSize=6, symbolBrush='g', name="Peaks"
    )
    """

    def tick() -> None:
        debug_state["tick_count"] += 1
        tick_no = debug_state["tick_count"]
        plot_obj, x_axis, y_axis = window.acquire_plot_data(psd_curve)

        if x_axis is None or y_axis is None:
            signature = (
                "no_data",
                x_axis is None,
                y_axis is None,
                window._data_queue.qsize(),
                window._run_event.is_set(),
            )
            if _should_log(signature, has_issue=True, tick_no=tick_no):
                print(
                    "[PSD_DEBUG] "
                    f"tick={tick_no} buffer_complete=False plot_ready=False visible=False "
                    f"reason=no_plot_data x_none={x_axis is None} y_none={y_axis is None} "
                    f"queue_depth={window._data_queue.qsize()} run={window._run_event.is_set()}",
                    flush=True,
                )
                debug_state["last_signature"] = signature
            return

        try:
            x_arr = np.asarray(x_axis, dtype=float)
            y_arr = np.asarray(y_axis, dtype=float)
        except (TypeError, ValueError) as exc:
            signature = ("cast_error", str(exc))
            if _should_log(signature, has_issue=True, tick_no=tick_no):
                print(
                    f"[PSD_DEBUG] tick={tick_no} buffer_complete=False plot_ready=False "
                    f"visible=False reason=array_cast_failed error={exc}",
                    flush=True,
                )
                debug_state["last_signature"] = signature
            return

        issues: list[str] = []
        notes: list[str] = []
        expected_bins = _expected_psd_bins()

        if x_arr.ndim != 1:
            issues.append(f"x_ndim={x_arr.ndim}")
        if y_arr.ndim != 1:
            issues.append(f"y_ndim={y_arr.ndim}")
        if x_arr.size == 0:
            issues.append("x_empty")
        if y_arr.size == 0:
            issues.append("y_empty")
        if x_arr.size != y_arr.size:
            issues.append(f"len_mismatch:x={x_arr.size},y={y_arr.size}")
        if expected_bins is not None and y_arr.size != expected_bins:
            notes.append(f"expected_bins={expected_bins},got={y_arr.size}")
        if debug_state["last_bins"] is not None and y_arr.size != debug_state["last_bins"]:
            notes.append(f"bin_count_changed:{debug_state['last_bins']}->{y_arr.size}")
        debug_state["last_bins"] = y_arr.size

        x_flat = x_arr.reshape(-1)
        y_flat = y_arr.reshape(-1)
        x_isfinite = np.isfinite(x_flat)
        y_isfinite = np.isfinite(y_flat)
        x_finite = x_flat[x_isfinite]
        y_finite = y_flat[y_isfinite]

        x_nan = int(np.isnan(x_flat).sum())
        y_nan = int(np.isnan(y_flat).sum())
        x_inf = int(np.isinf(x_flat).sum())
        y_inf = int(np.isinf(y_flat).sum())

        if x_nan or x_inf:
            issues.append(f"x_nonfinite:nan={x_nan},inf={x_inf}")
        if y_nan or y_inf:
            issues.append(f"y_nonfinite:nan={y_nan},inf={y_inf}")
        if x_finite.size == 0:
            issues.append("x_no_finite_values")
        if y_finite.size == 0:
            issues.append("y_no_finite_values")

        x_min = float(np.min(x_finite)) if x_finite.size else float("nan")
        x_max = float(np.max(x_finite)) if x_finite.size else float("nan")
        y_min = float(np.min(y_finite)) if y_finite.size else float("nan")
        y_max = float(np.max(y_finite)) if y_finite.size else float("nan")
        y_mean = float(np.mean(y_finite)) if y_finite.size else float("nan")
        y_std = float(np.std(y_finite)) if y_finite.size else float("nan")
        y_p2p = float(np.ptp(y_finite)) if y_finite.size else float("nan")

        x_diff = np.diff(x_finite) if x_finite.size > 1 else np.array([], dtype=float)
        if x_diff.size:
            if np.any(x_diff < 0.0):
                issues.append("x_not_monotonic")
            if np.any(x_diff == 0.0):
                notes.append("x_has_duplicate_bins")
            x_step_min = float(np.min(x_diff))
            x_step_max = float(np.max(x_diff))
        else:
            x_step_min = float("nan")
            x_step_max = float("nan")
            if x_arr.size == 1:
                notes.append("single_point_buffer")

        if y_finite.size and y_p2p == 0.0:
            notes.append("y_flatline")
        if y_finite.size and (y_max > 1000.0 or y_min < -1000.0 or y_p2p > 2000.0):
            issues.append("y_extreme_magnitude_possible_corruption")
        elif y_finite.size and (y_max > 100.0 or y_min < -250.0):
            notes.append("y_outside_typical_db_range")

        structural_ok = (
            x_arr.ndim == 1
            and y_arr.ndim == 1
            and x_arr.size > 0
            and y_arr.size > 0
            and x_arr.size == y_arr.size
        )

        finite_pair_count = 0
        plot_x = x_arr
        plot_y = y_arr
        if structural_ok:
            finite_pair_mask = np.isfinite(x_arr) & np.isfinite(y_arr)
            finite_pair_count = int(np.count_nonzero(finite_pair_mask))
            if finite_pair_count == 0:
                issues.append("no_finite_xy_pairs")
            elif finite_pair_count != x_arr.size:
                notes.append(f"finite_pairs={finite_pair_count}/{x_arr.size}")
                plot_x = x_arr[finite_pair_mask]
                plot_y = y_arr[finite_pair_mask]

        try:
            y_view_min = float(window.sld_ymin.value())
            y_view_max = float(window.sld_ymax.value())
        except Exception:
            y_view_min = float("nan")
            y_view_max = float("nan")

        buffer_complete = structural_ok and finite_pair_count == x_arr.size and not (x_nan or x_inf or y_nan or y_inf)
        plot_ready = structural_ok and finite_pair_count > 0

        visible_points = 0
        visible_values = plot_y if plot_ready else y_finite
        if visible_values.size and np.isfinite(y_view_min) and np.isfinite(y_view_max):
            visible_mask = (visible_values >= y_view_min) & (visible_values <= y_view_max)
            visible_points = int(np.count_nonzero(visible_mask))
            if visible_points == 0:
                notes.append(f"outside_y_view:[{y_view_min:.1f},{y_view_max:.1f}]")

        visible = plot_ready and (visible_points > 0)

        signature = (
            buffer_complete,
            plot_ready,
            visible,
            tuple(x_arr.shape),
            tuple(y_arr.shape),
            expected_bins,
            x_nan,
            y_nan,
            x_inf,
            y_inf,
            tuple(issues),
            tuple(notes),
        )
        has_issue = (not buffer_complete) or (not plot_ready) or (not visible) or bool(issues)

        if _should_log(signature, has_issue=has_issue, tick_no=tick_no):
            print(
                "[PSD_DEBUG] "
                f"tick={tick_no} buffer_complete={buffer_complete} plot_ready={plot_ready} visible={visible} "
                f"x_shape={x_arr.shape} y_shape={y_arr.shape} "
                f"x_finite={x_finite.size}/{x_arr.size} y_finite={y_finite.size}/{y_arr.size} "
                f"finite_pairs={finite_pair_count}/{x_arr.size if x_arr.ndim == 1 else x_arr.size} "
                f"x_min={x_min:.6f} x_max={x_max:.6f} "
                f"y_min={y_min:.3f} y_max={y_max:.3f} y_mean={y_mean:.3f} y_std={y_std:.3f} y_p2p={y_p2p:.3f} "
                f"x_step_min={x_step_min:.9f} x_step_max={x_step_max:.9f} "
                f"visible_points={visible_points} y_view=[{y_view_min:.1f},{y_view_max:.1f}] "
                f"expected_bins={expected_bins} "
                f"issues={issues if issues else ['none']} notes={notes if notes else ['none']}",
                flush=True,
            )
            if has_issue or tick_no <= 3:
                print(
                    "[PSD_DEBUG] "
                    f"tick={tick_no} samples x={_format_samples(x_flat)} y={_format_samples(y_flat)}",
                    flush=True,
                )
            debug_state["last_signature"] = signature

        if not plot_ready:
            return

        window.refresh_plot(plot_obj, plot_x, plot_y)
        """
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
        """


    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(50)

    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
