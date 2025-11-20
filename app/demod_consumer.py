#!/usr/bin/env python3
"""
demod_consumer.py

Consume signed int8 IQ from stdin (I,Q,I,Q,...) and demodulate FM or AM using csdr + play.

Usage examples:
  demod_consumer.py -f 98000000 -s 20000000 -t FM -b 200000
  demod_consumer.py -f 98000000 -s 20000000 -t FM -b 200000 -d 2000000 -a 48000 -m
"""
from __future__ import annotations

import argparse
import logging
import signal
import subprocess
import sys
import time
from typing import Optional, Union
import numpy as np

import cfg

log = cfg.set_logger()

def float_to_plain(value: Union[str, float, int]) -> str:
    try:
        v = float(value)
    except Exception:
        return str(value)
    if v.is_integer():
        return str(int(v))
    s = format(v, "f").rstrip("0").rstrip(".")
    return s or "0"


class Demodulator:
    def __init__(
        self,
        input_rate: float,
        rf_bandwidth: float,
        demod_type: str,
        decimate_to: Optional[float] = None,
        audio_rate: int = 48000,
        logger: Optional[logging.Logger] = None,
    ):
        self.input_rate = float(input_rate)
        self.rf_bandwidth = float(rf_bandwidth)
        self.demod_type = demod_type.lower()
        self.decimate_to = float(decimate_to) if decimate_to is not None else None
        self.audio_rate = int(audio_rate)
        self.logger = logger

    def _calc_decimation_from_bw(self) -> int:
        decimation = max(1, int(round(self.input_rate / self.rf_bandwidth)))
        return decimation

    def _calc_decimation_to_target(self) -> int:
        if self.decimate_to is None:
            return 1
        dec = max(1, int(round(self.input_rate / self.decimate_to)))
        return dec

    def _fir_cutoff_for_decimation(self, decimation: int) -> float:
        # normalized cutoff for csdr fir_decimate_cc (0..0.5)
        return 0.5 / decimation

    def build_fm_pipeline(self) -> str:
        if self.decimate_to is not None:
            dec = self._calc_decimation_to_target()
        else:
            dec = self._calc_decimation_from_bw()

        decimated_rate = int(self.input_rate / dec)
        cutoff = self._fir_cutoff_for_decimation(dec)
        cutoff_str = ("{:.6f}".format(cutoff)).rstrip("0").rstrip(".")
        audio_rate_k = f"{self.audio_rate // 1000}k"

        pipeline = (
            "csdr convert_s8_f | "
            f"csdr fir_decimate_cc {dec} {cutoff_str} | "
            "csdr fmdemod_quadri_cf | "
            f"csdr deemphasis_wfm_ff {decimated_rate} 5.0e-5 | "
            f"play -t f32 -r {decimated_rate} -c 1 - rate {audio_rate_k}"
        )
        return pipeline

    def build_am_pipeline(self) -> str:
        if self.decimate_to is not None:
            dec = self._calc_decimation_to_target()
        else:
            dec = self._calc_decimation_from_bw()

        decimated_rate = int(self.input_rate / dec)
        cutoff = self._fir_cutoff_for_decimation(dec)
        cutoff_str = ("{:.6f}".format(cutoff)).rstrip("0").rstrip(".")
        audio_rate_k = f"{self.audio_rate // 1000}k"

        pipeline = (
            "csdr convert_s8_f | "
            f"csdr fir_decimate_cc {dec} {cutoff_str} | "
            "csdr amdemod_cf | "
            "csdr dcblock_ff | "
            f"play -t f32 -r {decimated_rate} -c 1 - rate {audio_rate_k}"
        )
        return pipeline

    def build_pipeline(self) -> str:
        if self.demod_type == "fm":
            return self.build_fm_pipeline()
        elif self.demod_type == "am":
            return self.build_am_pipeline()
        else:
            raise ValueError("Unknown demod type: must be 'fm' or 'am'")

    def run(self, verbose: bool = False, metrics: bool = False, metrics_interval: float = 10.0) -> int:
        """
        If metrics=True, compute:
         - FM: instantaneous-frequency metrics (peak-to-peak, peak deviation, rms AC).
         - AM: envelope metrics (peak-to-peak amplitude, modulation depth, rms AC).
        and print them every `metrics_interval` seconds to stderr. Audio pipeline runs unchanged.
        """
        pipeline_cmd = self.build_pipeline()
        if verbose:
            sys.stderr.write(f"[demod_consumer] pipeline:\n{pipeline_cmd}\n")

        # Start the demod/play pipeline and feed its stdin via a PIPE so we can duplicate the bytes
        proc = subprocess.Popen(pipeline_cmd, shell=True, stdin=subprocess.PIPE)

        # streaming parameters
        bytes_per_sample = 2  # int8 I + Q (one byte each)
        # chunk duration chosen small to avoid big memory; ~50ms per chunk
        chunk_dur = 0.05
        chunk_samples = max(1024, int(self.input_rate * chunk_dur))
        chunk_bytes = chunk_samples * bytes_per_sample

        # Metrics state
        last_complex = None  # used by FM to preserve phase continuity
        last_amp = None      # used by AM to preserve envelope continuity
        last_report_time = time.time()
        # downsample factor for metrics to keep CPU/memory sane
        desired_metric_rate = 200_000.0
        metric_down = max(1, int(round(self.input_rate / desired_metric_rate)))
        eff_metric_rate = self.input_rate / metric_down

        # running statistics (avoid storing big arrays)
        run_max = -float("inf")
        run_min = float("inf")
        run_sumsq = 0.0
        run_sum = 0.0            # sum for mean calculation
        run_count = 0
        run_raw_abs_peak = 0.0

        try:
            while True:
                b = sys.stdin.buffer.read(chunk_bytes)
                if not b:
                    # EOF: close pipeline stdin and break; let pipeline finish
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
                    break

                # write raw bytes to pipeline stdin (audio branch)
                try:
                    proc.stdin.write(b)
                except BrokenPipeError:
                    # pipeline ended; stop reading/feeding
                    break

                # compute metrics on the copy of bytes (but only if metrics enabled)
                if metrics:
                    # drop trailing odd byte if present
                    if len(b) % 2 != 0:
                        b = b[:-1]
                    if len(b) < 2:
                        pass
                    else:
                        a = np.frombuffer(b, dtype=np.int8).astype(np.float32)
                        if a.size >= 2:
                            i = a[0::2]
                            q = a[1::2]
                            iq = (i + 1j * q).astype(np.complex64)

                            # downsample for metrics
                            if metric_down > 1:
                                iq = iq[::metric_down]

                            if self.demod_type == "fm":
                                # preserve continuity across chunks for phase unwrap
                                if last_complex is not None:
                                    iq = np.concatenate((np.array([last_complex], dtype=np.complex64), iq))
                                if iq.size >= 2:
                                    angles = np.angle(iq)
                                    dph = np.diff(np.unwrap(angles))
                                    inst_freq = (dph * eff_metric_rate) / (2.0 * np.pi)  # Hz
                                    if inst_freq.size > 0:
                                        cmax = float(np.max(inst_freq))
                                        cmin = float(np.min(inst_freq))
                                        run_max = max(run_max, cmax)
                                        run_min = min(run_min, cmin)
                                        run_sumsq += float(np.sum(inst_freq * inst_freq))
                                        run_sum += float(np.sum(inst_freq))
                                        run_count += int(inst_freq.size)
                                        run_raw_abs_peak = max(run_raw_abs_peak, float(np.max(np.abs(inst_freq))))
                                last_complex = iq[-1] if iq.size > 0 else last_complex

                            elif self.demod_type == "am":
                                # compute envelope
                                envelope = np.abs(iq).astype(np.float64)
                                # preserve continuity across chunks for envelope min/max
                                if last_amp is not None:
                                    envelope = np.concatenate((np.array([last_amp], dtype=np.float64), envelope))
                                if envelope.size > 0:
                                    cmax = float(np.max(envelope))
                                    cmin = float(np.min(envelope))
                                    run_max = max(run_max, cmax)
                                    run_min = min(run_min, cmin)
                                    run_sumsq += float(np.sum(envelope * envelope))
                                    run_sum += float(np.sum(envelope))
                                    run_count += int(envelope.size)
                                    run_raw_abs_peak = max(run_raw_abs_peak, float(np.max(envelope)))
                                last_amp = envelope[-1] if envelope.size > 0 else last_amp

                # periodic report
                now = time.time()
                if metrics and (now - last_report_time) >= metrics_interval:
                    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
                    if run_count > 0:
                        mean_val = run_sum / run_count
                        mean_sq = run_sumsq / run_count
                        # rms AC (subtract DC/mean). Guard for small negative due to float noise.
                        rms_ac = (mean_sq - mean_val * mean_val) ** 0.5 if mean_sq > mean_val * mean_val else 0.0

                        if self.demod_type == "fm":
                            p2p = run_max - run_min
                            # peak deviation about the mean (carrier-centered)
                            peak_dev = max(abs(run_max - mean_val), abs(run_min - mean_val))
                            # rms_ac in Hz
                            print(
                                f"{ts}  FM excursion p2p: {p2p:.1f} Hz  peak_dev: {peak_dev:.1f} Hz  rms_ac: {rms_ac:.1f} Hz",
                                file=sys.stderr,
                                flush=True,
                            )
                        else:  # AM
                            Amax = run_max
                            Amin = run_min
                            denom = (Amax + Amin)
                            if denom > 0:
                                m = (Amax - Amin) / denom
                            else:
                                m = 0.0
                            depth_pct = m * 100.0
                            # rms_ac in envelope units
                            print(
                                f"{ts}  AM envelope p2p: {Amax - Amin:.3f}  depth: {depth_pct:.1f}%  rms_ac: {rms_ac:.3f}",
                                file=sys.stderr,
                                flush=True,
                            )
                    else:
                        print(f"{ts}  (no metric samples)", file=sys.stderr, flush=True)

                    # reset running stats
                    run_max = -float("inf")
                    run_min = float("inf")
                    run_sumsq = 0.0
                    run_sum = 0.0
                    run_count = 0
                    run_raw_abs_peak = 0.0
                    last_report_time = now

            # close stdin properly and wait
            try:
                proc.stdin.close()
            except Exception:
                pass
            rc = proc.wait()
            if rc != 0:
                sys.stderr.write(f"[demod_consumer] pipeline exited with code {rc}\n")
            return rc

        except KeyboardInterrupt:
            sys.stderr.write("[demod_consumer] Interrupted by user\n")
            try:
                proc.terminate()
            except Exception:
                pass
            return 0
        except Exception as exc:
            sys.stderr.write(f"[demod_consumer] Exception running pipeline: {exc}\n")
            try:
                proc.kill()
            except Exception:
                pass
            return 1


def numeric_type(x):
    try:
        return float(x)
    except Exception:
        raise argparse.ArgumentTypeError(f"Invalid numeric value: {x}")


def build_parser() -> argparse.ArgumentParser:
    epilog = (
        "Examples:\n"
        "  demod_consumer.py -f 98000000 -s 20000000 -t FM -b 200000\n"
        "  demod_consumer.py -f 98000000 -s 20000000 -t AM -b 200000\n"
        "  demod_consumer.py -f 98000000 -s 20000000 -t FM -b 200000 -d 2000000 -a 48000 -m\n"
    )
    p = argparse.ArgumentParser(
        prog="demod_consumer.py",
        description="PSD consumer.\nRequired: frequency, sample rate, type, bandwidth.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    p.add_argument("-f", "--freq", required=True, type=numeric_type, help="Center frequency in Hz (range: 1 MHz to 6 GHz). Example: 98000000")
    p.add_argument("-t", "--type", required=True, choices=["FM", "fm", "AM", "am"], type=str.upper, help="Demodulation type. Example: FM, AM")
    p.add_argument("-s", "--rate", required=True, type=numeric_type, help="Sample rate in Hz (range: 1 MHz to 6 GHz). Example: 20000000")
    p.add_argument("-b", "--bw", required=True, type=numeric_type, help="Demod bandwidth in Hz (must not exceed freq or samp-rate). Example: 200000")
    p.add_argument("-d", "--dec", dest="dec", type=numeric_type, default=2000000, help="Optional: target sample rate after decimation (Hz), default: 2000000")
    p.add_argument("-a", "--aud-rate", dest="aud_rate", type=int, default=48000, help="Optional: Audio output rate for 'play' (Hz). Example: 48000, default: 48000")
    p.add_argument("-m", "--metrics", action="store_true", help="Optional: Enable metrics (boolean flag)")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose / print pipeline")

    # rename optional arguments group to "options"
    for g in p._action_groups:
        if g.title == "optional arguments":
            g.title = "options"

    # If no args print help and exit 0
    if len(sys.argv) == 1:
        p.print_help()
        sys.exit(0)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # validation
    if not (1e6 <= args.freq <= 6e9):
        parser.error("frequency must be between 1e6 and 6e9 Hz (1 MHz - 6 GHz)")
    if not (1e6 <= args.rate <= 6e9):
        parser.error("sample rate must be between 1e6 and 6e9 Hz (1 MHz - 6 GHz)")
    if not (args.bw > 0):
        parser.error("bw must be positive")
    if args.bw > args.rate:
        parser.error("bw must not exceed sample rate")
    if args.bw > args.freq:
        parser.error("bw must not exceed center frequency (Hz)")

    # normalize types
    args.freq = float(args.freq)
    args.rate = float(args.rate)
    args.bw = float(args.bw)
    args.dec = float(args.dec)
    args.aud_rate = int(args.aud_rate)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s: %(message)s")

    demodulator = Demodulator(
        input_rate=args.rate,
        rf_bandwidth=args.bw,
        demod_type=args.type,
        decimate_to=args.dec,
        audio_rate=args.aud_rate,
        logger=log,
    )

    if args.verbose:
        decimation = demodulator._calc_decimation_to_target() if demodulator.decimate_to is not None else demodulator._calc_decimation_from_bw()
        decimated_rate = int(args.rate / max(1, decimation))
        log.debug("--- Demodulator Configuration ---")
        log.debug("Input Rate: %s Hz", float_to_plain(args.rate))
        log.debug("Target IF Rate: %s Hz (Decimation: %d)", float_to_plain(decimated_rate), decimation)
        log.debug("RF Bandwidth: %s Hz", float_to_plain(args.bw))
        log.debug("Type: %s", args.type)
        log.debug("Audio Rate: %s Hz", args.aud_rate)
        log.debug("---------------------------------")
        log.debug("Pipeline: %s", demodulator.build_pipeline())

    def _signal_handler(signum, frame):
        log.info("Signal %d received, exiting", signum)
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # metrics_interval fixed at 10s per your request
    exit_code = demodulator.run(verbose=args.verbose, metrics=args.metrics, metrics_interval=10.0)
    return exit_code


if __name__ == "__main__":
    rc = cfg.run_and_capture(main, cfg.LOG_FILES_NUM)
    sys.exit(rc)