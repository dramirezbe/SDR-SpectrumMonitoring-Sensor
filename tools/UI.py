#!/usr/bin/env python3

import sys
import asyncio
import threading
import queue
from pathlib import Path
from dataclasses import asdict
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pyqtgraph as pg
from PyQt5.QtWidgets import (
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QCheckBox,
    QComboBox,
)
from PyQt5.QtCore import Qt

import cfg
from utils import FilterConfig, ServerRealtimeConfig, ZmqPairController
from functions import AcquireDual

log = cfg.set_logger()


class PSDLiveUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PSD Realtime - PyQtGraph")
        self.resize(1000, 600)

        self._data_queue: queue.Queue[dict] = queue.Queue(maxsize=1)
        self._runtime_config: Optional[dict] = None
        self._config_lock = threading.Lock()
        self._run_event = threading.Event()
        self._stop_event = threading.Event()

        self.init_ui()
        self.start_worker()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        controls = QWidget()
        controls.setFixedWidth(250)
        grid = QGridLayout(controls)

        self.txt_cf = QLineEdit("97.5")
        self.txt_span = QLineEdit("20.0")
        self.txt_rbw = QLineEdit("100")
        self.txt_overlap = QLineEdit("0.5")

        grid.addWidget(QLabel("CF (MHz):"), 0, 0)
        grid.addWidget(self.txt_cf, 0, 1)
        grid.addWidget(QLabel("Span (MHz):"), 1, 0)
        grid.addWidget(self.txt_span, 1, 1)
        grid.addWidget(QLabel("RBW (kHz):"), 2, 0)
        grid.addWidget(self.txt_rbw, 2, 1)
        grid.addWidget(QLabel("Overlap:"), 3, 0)
        grid.addWidget(self.txt_overlap, 3, 1)

        self.sld_lna = QSlider(Qt.Horizontal)
        self.sld_lna.setMaximum(40)
        self.sld_vga = QSlider(Qt.Horizontal)
        self.sld_vga.setMaximum(62)
        grid.addWidget(QLabel("LNA Gain:"), 4, 0)
        grid.addWidget(self.sld_lna, 4, 1)
        grid.addWidget(QLabel("VGA Gain:"), 5, 0)
        grid.addWidget(self.sld_vga, 5, 1)

        self.chk_amp = QCheckBox("Antenna Amp")
        self.chk_amp.setChecked(True)
        self.cmb_port = QComboBox()
        self.cmb_port.addItems(["1", "2", "3", "4"])
        self.cmb_win = QComboBox()
        self.cmb_win.addItems(["hann", "hamming", "blackman"])

        grid.addWidget(self.chk_amp, 6, 0, 1, 2)
        grid.addWidget(QLabel("Port:"), 7, 0)
        grid.addWidget(self.cmb_port, 7, 1)
        grid.addWidget(QLabel("Window:"), 8, 0)
        grid.addWidget(self.cmb_win, 8, 1)

        self.chk_filter = QCheckBox("Enable Filter")
        self.chk_filter.setChecked(True)
        self.txt_f0 = QLineEdit("87.5")
        self.txt_f1 = QLineEdit("107.5")
        grid.addWidget(self.chk_filter, 9, 0, 1, 2)
        grid.addWidget(QLabel("Filtro Lo:"), 10, 0)
        grid.addWidget(self.txt_f0, 10, 1)
        grid.addWidget(QLabel("Filtro Hi:"), 11, 0)
        grid.addWidget(self.txt_f1, 11, 1)

        self.btn_apply = QPushButton("Apply")
        self.btn_start = QPushButton("Start")
        self.btn_stop = QPushButton("Stop")

        self.btn_apply.clicked.connect(self.apply_config)
        self.btn_start.clicked.connect(lambda: self._run_event.set())
        self.btn_stop.clicked.connect(lambda: self._run_event.clear())

        grid.addWidget(self.btn_apply, 12, 0, 1, 2)
        grid.addWidget(self.btn_start, 13, 0)
        grid.addWidget(self.btn_stop, 13, 1)

        self.plot_widget = pg.PlotWidget(title="PSD Realtime (ZMQ)")
        self.plot_widget.setLabel("left", "Potencia", units="dB")
        self.plot_widget.setLabel("bottom", "Frecuencia", units="MHz")
        self.plot_widget.showGrid(x=True, y=True)

        main_layout.addWidget(controls)
        main_layout.addWidget(self.plot_widget)

        self.apply_config()

    def _set_runtime_config(self, cfg_dict: dict) -> None:
        with self._config_lock:
            self._runtime_config = cfg_dict

    def _get_runtime_config(self) -> Optional[dict]:
        with self._config_lock:
            if self._runtime_config is None:
                return None
            return dict(self._runtime_config)

    def apply_config(self):
        try:
            f0 = float(self.txt_f0.text())
            f1 = float(self.txt_f1.text())

            filter_cfg = None
            if self.chk_filter.isChecked():
                filter_cfg = FilterConfig(start_freq_hz=int(f0 * 1e6), end_freq_hz=int(f1 * 1e6))

            cfg_obj = ServerRealtimeConfig(
                method_psd="pfb",
                center_freq_hz=int(float(self.txt_cf.text()) * 1e6),
                sample_rate_hz=int(float(self.txt_span.text()) * 1e6),
                rbw_hz=int(float(self.txt_rbw.text()) * 1e3),
                window=self.cmb_win.currentText(),
                overlap=float(self.txt_overlap.text()),
                lna_gain=self.sld_lna.value(),
                vga_gain=self.sld_vga.value(),
                antenna_amp=self.chk_amp.isChecked(),
                antenna_port=int(self.cmb_port.currentText()),
                ppm_error=0,
                demodulation=None,
                filter=filter_cfg,
            )
            self._set_runtime_config(asdict(cfg_obj))
        except Exception as exc:
            log.error(f"Config inválida: {exc}")

    def start_worker(self):
        self.worker_thread = threading.Thread(target=self._run_worker, daemon=True)
        self.worker_thread.start()

    def _run_worker(self):
        asyncio.run(self._acquisition_loop())

    async def _acquisition_loop(self):
        controller = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False)
        async with controller as zmq_ctrl:
            acquirer = AcquireDual(controller=zmq_ctrl, log=log)
            while not self._stop_event.is_set():
                runtime_config = self._get_runtime_config()
                if not self._run_event.is_set() or not runtime_config:
                    await asyncio.sleep(0.1)
                    continue

                try:
                    payload = await acquirer.get_corrected_data(runtime_config)
                    if payload and payload.get("Pxx"):
                        if self._data_queue.full():
                            self._data_queue.get_nowait()
                        self._data_queue.put_nowait(payload)
                except Exception as exc:
                    log.error(f"Error DSP: {exc}")
                    await asyncio.sleep(0.1)

    def acquire_plot_data(self, plot_obj):
        try:
            payload = self._data_queue.get_nowait()
        except queue.Empty:
            return plot_obj, None, None

        pxx = np.asarray(payload.get("Pxx", []), dtype=float)
        start_f = float(payload.get("start_freq_hz", 0.0)) / 1e6
        end_f = float(payload.get("end_freq_hz", 0.0)) / 1e6

        if pxx.size == 0:
            return plot_obj, None, None

        if end_f > start_f:
            x_axis = np.linspace(start_f, end_f, pxx.size)
        else:
            x_axis = np.arange(pxx.size, dtype=float)

        return plot_obj, x_axis, pxx

    @staticmethod
    def refresh_plot(plot_obj, x_axis, y_axis):
        if plot_obj is None or x_axis is None or y_axis is None:
            return
        plot_obj.setData(x_axis, y_axis)

    def closeEvent(self, event):
        self._stop_event.set()
        event.accept()
