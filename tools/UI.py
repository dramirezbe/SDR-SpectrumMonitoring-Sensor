#!/usr/bin/env python3

import sys
import asyncio
import threading
import queue
import socket
import struct
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

try:
    import gi
    gi.require_version("Gst", "1.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gst, GLib
    Gst.init(None)
    GST_AVAILABLE = True
except Exception:
    Gst = None
    GLib = None
    GST_AVAILABLE = False

log = cfg.set_logger()


class LocalOpusAudioBridge:
    HDR_FMT = "!IIIHH"
    HDR_SIZE = struct.calcsize(HDR_FMT)
    MAGIC = 0x4F505530
    DEFAULT_FRAME_MS = 20

    def __init__(self, host: str = "127.0.0.1", port: int = 9000):
        self.host = host
        self.port = port
        self._running = False
        self._server_sock = None
        self._tcp_thread = None
        self._glib_thread = None
        self._glib_loop = None
        self._pipe = None
        self._appsrc = None
        self._pts = 0
        self._client_connected = False
        self._frames_rx = 0
        self._bytes_rx = 0
        self._bad_magic = 0
        self._last_seq = None
        self._last_sr = None
        self._last_ch = None

    @property
    def enabled(self) -> bool:
        return GST_AVAILABLE

    def start(self) -> None:
        if not GST_AVAILABLE:
            log.error("[AUDIO_LOCAL] GStreamer no disponible; puente local deshabilitado.")
            return
        if self._running:
            return

        self._glib_loop = GLib.MainLoop()
        self._glib_thread = threading.Thread(target=self._glib_loop.run, daemon=True)
        self._glib_thread.start()

        pipeline_desc = (
            "appsrc name=opussrc is-live=true format=time do-timestamp=true ! "
            "queue ! opusparse ! opusdec ! audioconvert ! audioresample ! autoaudiosink sync=false"
        )
        self._pipe = Gst.parse_launch(pipeline_desc)
        self._appsrc = self._pipe.get_by_name("opussrc")

        caps = Gst.Caps.from_string(
            "audio/x-opus, rate=(int)48000, channels=(int)1, channel-mapping-family=(int)0"
        )
        self._appsrc.set_property("caps", caps)
        self._pipe.set_state(Gst.State.PLAYING)

        self._running = True
        self._tcp_thread = threading.Thread(target=self._tcp_server_loop, daemon=True)
        self._tcp_thread.start()
        log.info(f"[AUDIO_LOCAL] Bridge activo en {self.host}:{self.port} | gst={GST_AVAILABLE}")

    def stop(self) -> None:
        if not self._running:
            return

        self._running = False

        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None

        if self._tcp_thread and self._tcp_thread.is_alive():
            self._tcp_thread.join(timeout=1.5)

        if self._pipe:
            self._pipe.set_state(Gst.State.NULL)
            self._pipe = None
            self._appsrc = None

        if self._glib_loop and self._glib_loop.is_running():
            GLib.idle_add(self._glib_loop.quit)
        if self._glib_thread and self._glib_thread.is_alive():
            self._glib_thread.join(timeout=1.5)

        self._glib_loop = None
        self._glib_thread = None
        self._tcp_thread = None
        self._pts = 0
        self._client_connected = False
        log.info("[AUDIO_LOCAL] Bridge detenido")

    def status_snapshot(self) -> dict:
        return {
            "running": self._running,
            "client_connected": self._client_connected,
            "frames_rx": self._frames_rx,
            "bytes_rx": self._bytes_rx,
            "bad_magic": self._bad_magic,
            "last_seq": self._last_seq,
            "last_sr": self._last_sr,
            "last_ch": self._last_ch,
            "host": self.host,
            "port": self.port,
        }

    def _recv_exact(self, conn: socket.socket, n: int) -> bytes:
        data = bytearray()
        while len(data) < n and self._running:
            chunk = conn.recv(n - len(data))
            if not chunk:
                break
            data.extend(chunk)
        return bytes(data)

    def _push_opus_frame(self, opus_bytes: bytes) -> None:
        if not self._running or not self._appsrc:
            return

        dur_ns = int(self.DEFAULT_FRAME_MS * 1e6)
        appsrc = self._appsrc
        pts_now = self._pts
        self._pts += dur_ns

        def _do_push():
            if not self._running or appsrc is None:
                return False
            buf = Gst.Buffer.new_allocate(None, len(opus_bytes), None)
            buf.fill(0, opus_bytes)
            buf.pts = buf.dts = pts_now
            buf.duration = dur_ns
            appsrc.emit("push-buffer", buf)
            return False

        GLib.idle_add(_do_push)

    def _handle_client(self, conn: socket.socket) -> None:
        conn.settimeout(1.0)
        while self._running:
            try:
                hdr = self._recv_exact(conn, self.HDR_SIZE)
                if len(hdr) != self.HDR_SIZE:
                    break

                magic, _seq, _sr, _ch, plen = struct.unpack(self.HDR_FMT, hdr)
                if magic != self.MAGIC:
                    self._bad_magic += 1
                    if self._bad_magic <= 5 or (self._bad_magic % 25) == 0:
                        log.warning(f"[AUDIO_LOCAL] bad magic ({magic:#x}) count={self._bad_magic}")
                    continue

                self._last_seq = int(_seq)
                self._last_sr = int(_sr)
                self._last_ch = int(_ch)

                payload = self._recv_exact(conn, int(plen))
                if len(payload) != int(plen):
                    break

                self._frames_rx += 1
                self._bytes_rx += len(payload)
                if self._frames_rx <= 5 or (self._frames_rx % 100) == 0:
                    log.debug(
                        "[AUDIO_LOCAL] frame_rx=%d seq=%d sr=%d ch=%d bytes=%d",
                        self._frames_rx,
                        self._last_seq,
                        self._last_sr,
                        self._last_ch,
                        len(payload),
                    )

                self._push_opus_frame(payload)
            except socket.timeout:
                continue
            except Exception:
                break

    def _tcp_server_loop(self) -> None:
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self.host, self.port))
            srv.listen(1)
            srv.settimeout(1.0)
            self._server_sock = srv
        except Exception as exc:
            log.error(f"[AUDIO_LOCAL] No se pudo abrir {self.host}:{self.port}: {exc}")
            self._running = False
            return

        log.info(f"[AUDIO_LOCAL] TCP listen ok on {self.host}:{self.port}")

        while self._running:
            try:
                conn, _addr = srv.accept()
            except socket.timeout:
                continue
            except Exception:
                break

            self._client_connected = True
            log.info("[AUDIO_LOCAL] TCP client connected")
            with conn:
                self._handle_client(conn)
            self._client_connected = False
            log.warning("[AUDIO_LOCAL] TCP client disconnected")


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
        self._demod_mode: Optional[str] = None
        self._audio_bridge = LocalOpusAudioBridge(host="127.0.0.1", port=9000)
        self._dbg_acq_count = 0
        self._dbg_last_mode = None

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

        self.cmb_demod = QComboBox()
        self.cmb_demod.addItems(["fm", "am"])
        self.btn_demod_start = QPushButton("Demod Start")
        self.btn_demod_stop = QPushButton("Demod Stop")

        self.btn_apply.clicked.connect(self.apply_config)
        self.btn_start.clicked.connect(lambda: self._run_event.set())
        self.btn_stop.clicked.connect(lambda: self._run_event.clear())
        self.btn_demod_start.clicked.connect(self.start_demod)
        self.btn_demod_stop.clicked.connect(self.stop_demod)

        grid.addWidget(self.btn_apply, 12, 0, 1, 2)
        grid.addWidget(self.btn_start, 13, 0)
        grid.addWidget(self.btn_stop, 13, 1)
        grid.addWidget(QLabel("Demod:"), 14, 0)
        grid.addWidget(self.cmb_demod, 14, 1)
        grid.addWidget(self.btn_demod_start, 15, 0)
        grid.addWidget(self.btn_demod_stop, 15, 1)

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
                demodulation=self._demod_mode,
                filter=filter_cfg,
            )
            cfg_dict = asdict(cfg_obj)
            self._set_runtime_config(cfg_dict)
            log.info(
                "[UI] apply_config cf=%.3fMHz fs=%.3fMHz rbw=%dkHz demod=%s filter=%s",
                cfg_dict["center_freq_hz"] / 1e6,
                cfg_dict["sample_rate_hz"] / 1e6,
                int(cfg_dict["rbw_hz"] / 1000),
                cfg_dict.get("demodulation"),
                bool(cfg_dict.get("filter")),
            )
        except Exception as exc:
            log.error(f"Config inválida: {exc}")

    def start_demod(self):
        mode = self.cmb_demod.currentText().strip().lower()
        if mode not in ("fm", "am"):
            log.error(f"[UI] Demod inválida: {mode}")
            return
        self._demod_mode = mode
        self._run_event.set()
        self.apply_config()
        self._audio_bridge.start()
        log.info(f"[UI] Demod START ({mode.upper()}) run_event={self._run_event.is_set()}")

    def stop_demod(self):
        self._demod_mode = None
        self.apply_config()
        self._audio_bridge.stop()
        log.info(f"[UI] Demod STOP run_event={self._run_event.is_set()}")

    def start_worker(self):
        self.worker_thread = threading.Thread(target=self._run_worker, daemon=True)
        self.worker_thread.start()

    def _run_worker(self):
        asyncio.run(self._acquisition_loop())

    async def _acquisition_loop(self):
        controller = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False)
        
        DEMOD_CFG_SENT = False
        RESET_DEMOD_CFG = False
        
        async with controller as zmq_ctrl:
            acquirer = AcquireDual(controller=zmq_ctrl, log=log)
            log.info(f"[UI] ZMQ connected addr={cfg.IPC_ADDR}")
            while not self._stop_event.is_set():
                runtime_config = self._get_runtime_config()
                if not self._run_event.is_set() or not runtime_config:
                    await asyncio.sleep(0.1)
                    continue

                try:
                    # --- Máquina de estados de Demodulación ---
                    is_demod = bool(runtime_config.get("demodulation"))

                    if self._dbg_last_mode != runtime_config.get("demodulation"):
                        self._dbg_last_mode = runtime_config.get("demodulation")
                        bridge = self._audio_bridge.status_snapshot()
                        log.info(
                            "[UI] mode_change demod=%s run=%s bridge_running=%s client=%s host=%s:%s",
                            self._dbg_last_mode,
                            self._run_event.is_set(),
                            bridge["running"],
                            bridge["client_connected"],
                            bridge["host"],
                            bridge["port"],
                        )

                    if is_demod:
                        DEMOD_CFG_SENT = True
                    else:
                        if DEMOD_CFG_SENT:
                            RESET_DEMOD_CFG = True
                            DEMOD_CFG_SENT = False

                    # Enviar comando de detención al motor C si se apagó la demodulación
                    if RESET_DEMOD_CFG:
                        log.info("[UI] sending RESET_DEMOD_CFG ({})")
                        await zmq_ctrl.send_command({})
                        RESET_DEMOD_CFG = False

                    # --- Adquisición ---
                    self._dbg_acq_count += 1
                    if self._dbg_acq_count <= 5 or (self._dbg_acq_count % 20) == 0:
                        log.debug(
                            "[UI] acquire #%d demod=%s cf=%s fs=%s rbw=%s",
                            self._dbg_acq_count,
                            runtime_config.get("demodulation"),
                            runtime_config.get("center_freq_hz"),
                            runtime_config.get("sample_rate_hz"),
                            runtime_config.get("rbw_hz"),
                        )

                    payload = await acquirer.get_corrected_data(runtime_config)
                    
                    if payload and payload.get("Pxx"):
                        pxx_len = len(payload.get("Pxx", []))
                        depth = payload.get("depth")
                        exc = payload.get("excursion_hz")
                        if self._dbg_acq_count <= 5 or (self._dbg_acq_count % 20) == 0:
                            bridge = self._audio_bridge.status_snapshot()
                            log.debug(
                                "[UI] payload ok bins=%d depth=%s exc=%s bridge(frames=%s,client=%s,last_seq=%s,sr=%s)",
                                pxx_len,
                                depth,
                                exc,
                                bridge["frames_rx"],
                                bridge["client_connected"],
                                bridge["last_seq"],
                                bridge["last_sr"],
                            )

                        if self._data_queue.full():
                            self._data_queue.get_nowait()
                        self._data_queue.put_nowait(payload)
                    else:
                        bridge = self._audio_bridge.status_snapshot()
                        log.warning(
                            "[UI] empty payload demod=%s bridge_running=%s client=%s frames=%s",
                            runtime_config.get("demodulation"),
                            bridge["running"],
                            bridge["client_connected"],
                            bridge["frames_rx"],
                        )
                        
                except Exception as exc:
                    bridge = self._audio_bridge.status_snapshot()
                    log.error(
                        "Error DSP: %s | demod=%s run=%s bridge_running=%s client=%s frames=%s",
                        exc,
                        runtime_config.get("demodulation") if runtime_config else None,
                        self._run_event.is_set(),
                        bridge["running"],
                        bridge["client_connected"],
                        bridge["frames_rx"],
                    )
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
        self._audio_bridge.stop()
        event.accept()
