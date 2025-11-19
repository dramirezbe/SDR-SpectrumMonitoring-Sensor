#!/usr/bin/env python3
"""
psd_consumer.py

Minimal PSD consumer using WelchEstimator from utils.

CLI:
  psd_consumer.py -f FREQ -s RATE -w RBW [--scale SCALE]

Behavior:
 - Uses WelchEstimator (from utils) to compute PSD.
 - r_ant is passed as 50.0 so impedance correction is active in the estimator.
 - Writes/overwrites 'psd_out.png' every second.
"""
from __future__ import annotations

import argparse
import logging
import os
import threading
import time
import warnings
from typing import Optional, Tuple, Union

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from utils import WelchEstimator  # <- user-provided class


OUTPUT_FILE = "psd_out.png"


def plot_psd_png(freqs: np.ndarray, psd_vals: np.ndarray, scale: str, fs: float, nperseg: int, center_freq: float):
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(freqs / 1e6, psd_vals)
    title = f"PSD (scale: {scale}, fc: {center_freq/1e6:.6f} MHz, fs: {fs/1e6:.6f} MHz, nperseg: {nperseg})"
    ax.set_title(title)
    ax.set_xlabel("Frequency [MHz]")
    ax.set_ylabel(f"PSD [{scale}]")
    ax.grid(True)
    fig.tight_layout()
    fig.savefig(OUTPUT_FILE, format="png", dpi=150)
    plt.close(fig)


class RingBuffer:
    def __init__(self, capacity: int) -> None:
        self._buf = np.zeros(capacity, dtype=np.complex128)
        self.capacity = capacity
        self._write_pos = 0
        self.count = 0
        self.lock = threading.Lock()
        self.cv = threading.Condition(self.lock)

    def write(self, data: np.ndarray) -> None:
        n = len(data)
        with self.lock:
            end = self._write_pos + n
            if end <= self.capacity:
                self._buf[self._write_pos:end] = data
            else:
                part1 = self.capacity - self._write_pos
                self._buf[self._write_pos:] = data[:part1]
                self._buf[: n - part1] = data[part1:]
            self._write_pos = (self._write_pos + n) % self.capacity
            self.count = min(self.capacity, self.count + n)
            self.cv.notify_all()

    def read_latest(self, amount: int) -> Optional[np.ndarray]:
        with self.lock:
            if self.count < amount:
                return None
            start = (self._write_pos - amount) % self.capacity
            if start + amount <= self.capacity:
                return self._buf[start : start + amount].copy()
            part1 = self.capacity - start
            return np.concatenate((self._buf[start:], self._buf[: amount - part1])).copy()


class PSDWorker(threading.Thread):
    def __init__(self, ring_buffer: RingBuffer, estimator: WelchEstimator, scale: str, samples: int, logger: logging.Logger):
        super().__init__(daemon=True)
        self._rb = ring_buffer
        self._est = estimator
        self._scale = scale
        self._samples = samples
        self._logger = logger

    def run(self) -> None:
        self._logger.info("[Worker] started, waiting for data...")
        while True:
            with self._rb.lock:
                while self._rb.count < self._samples:
                    self._rb.cv.wait(timeout=1.0)

            iq = self._rb.read_latest(self._samples)
            if iq is None:
                continue

            # call estimator; let warnings flow via warnings.warn; catch real exceptions only
            try:
                freqs, pxx = self._est.execute_welch(iq, scale=self._scale)
            except Exception as exc:
                self._logger.exception("WelchEstimator error: %s", exc)
                continue

            plot_psd_png(freqs, pxx, self._scale, self._est.fs, self._est.desired_nperseg, self._est.freq)
            self._logger.info("PSD saved: %s", OUTPUT_FILE)

            # fixed cadence: 1 second
            try:
                time.sleep(1.0)
            except Exception:
                break


def build_arg_parser() -> argparse.ArgumentParser:
    epilog = "Examples:\n  psd_consumer.py -f 98000000 -s 20000000 -w 10000\n"
    p = argparse.ArgumentParser(
        prog="psd_consumer.py",
        description="PSD consumer.\nRequired: frequency, sample rate, RBW.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    p.add_argument("-f", "--freq", required=True, type=float, help="Center frequency in Hz (1e6 - 6e9)")
    p.add_argument("-s", "--rate", required=True, type=float, help="Sample rate in Hz (1e6 - 6e9)")
    p.add_argument("-w", "--rbw", required=True, type=float, help="RBW resolution bandwidth in Hz (>1)")
    p.add_argument("--scale", type=str, default="dbfs", choices=["dbfs", "dbm", "db", "v2/hz"], help="PSD units (default: dbfs)")

    # rename optional arguments group to "options"
    for g in p._action_groups:
        if g.title == "optional arguments":
            g.title = "options"

    # print help and exit 0 when no args
    if len(os.sys.argv) == 1:
        p.print_help()
        os.sys.exit(0)

    return p


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if not (1e6 <= args.freq <= 6e9):
        parser.error("frequency must be between 1e6 and 6e9 Hz (1 MHz - 6 GHz)")
    if not (1e6 <= args.rate <= 6e9):
        parser.error("sample rate must be between 1e6 and 6e9 Hz (1 MHz - 6 GHz)")
    if not (args.rbw > 1):
        parser.error("rbw must be greater than 1 Hz")

    # instantiate estimator and force impedance correction by passing r_ant=50.0
    estimator = WelchEstimator(freq=int(args.freq), fs=int(args.rate), desired_rbw=int(args.rbw), r_ant=50.0, overlap=0.5, window="hamming")

    # buffer size: use estimator.desired_nperseg and samples = nperseg * 4
    nperseg = estimator.desired_nperseg
    samples = int(nperseg * 16)

    # build ringbuffer and worker
    ring_capacity = samples * 4
    rb = RingBuffer(ring_capacity)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    logger = logging.getLogger("psd_consumer")

    logger.info("--- PSD Real-Time Configuration ---")
    logger.info("  Sample rate (Fs): %.6f MHz", args.rate / 1e6)
    logger.info("  Center frequency (Fc): %.6f MHz", args.freq / 1e6)
    logger.info("  RBW: %.0f Hz", args.rbw)
    logger.info("  nperseg (est): %d", nperseg)
    logger.info("  samples (buffer): %d", samples)
    logger.info("----------------------------------")
    logger.info("Waiting for int8 IQ from stdin (I,Q,I,Q,...)...")

    worker = PSDWorker(rb, estimator, args.scale, samples, logger)
    worker.start()

    try:
        while True:
            data = os.read(0, 16384)
            if not data:
                break
            arr = np.frombuffer(data, dtype=np.int8)
            if len(arr) % 2 != 0:
                arr = arr[:-1]
            I = arr[0::2].astype(np.float64)
            Q = arr[1::2].astype(np.float64)
            complex_samples = I + 1j * Q
            rb.write(complex_samples)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt: exiting")
    except Exception as exc:
        logger.exception("Unhandled exception: %s", exc)

    logger.info("stdin closed. Worker thread will finish soon (daemon).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
