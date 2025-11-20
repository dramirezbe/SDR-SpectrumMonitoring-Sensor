#!/usr/bin/env python3
"""
realtime_runner.py

HackRF realtime processor.
Required: frequency, sample rate, RBW.
Optional: demod, demod bandwidth, metrics.
"""
from __future__ import annotations

import argparse
import os
import shlex
import signal
import subprocess
import sys
import time
from typing import List, Optional, Tuple, Union
from utils import get_persist_var
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


# Defaults / constants
LNA = 24
VGA = 40
ANTENNA_GAIN = 0
DEMUX_DEMOD_TARGET = 200000.0
AUDIO_RATE = 48000
BW_DEMOD_DEFAULT = 200e3

FIFO_PSD = "/tmp/iq_psd"
FIFO_DEMOD = "/tmp/iq_demod"


class FifoManager:
    def __init__(self, paths: List[str]) -> None:
        self.paths = paths

    def create(self) -> None:
        for p in self.paths:
            try:
                os.mkfifo(p)
                print(f"[fifo] Created {p}")
            except FileExistsError:
                print(f"[fifo] Already exists: {p}")

    def unlink(self) -> None:
        for p in self.paths:
            try:
                os.unlink(p)
                print(f"[fifo] Unlinked {p}")
            except FileNotFoundError:
                pass


def build_cmds(
    freq_hz: int,
    sample_rate_hz: int,
    freq_plain: str,
    sample_rate_plain: str,
    rbw: int,
    demod: Optional[str],
    bw: Optional[int],
    metrics: bool,
) -> Tuple[str, Optional[str], str]:
    hackrf_cmd = f"hackrf_transfer -r - -f {freq_hz} -s {sample_rate_hz} -a {ANTENNA_GAIN} -l {LNA} -g {VGA}"
    psd_cmd = f"python3 app/psd_consumer.py -f {freq_plain} -s {sample_rate_plain} -w {float_to_plain(rbw)} --scale dbm"
    demod_cmd = None
    if demod:
        demod_bw = bw if bw is not None else int(BW_DEMOD_DEFAULT)
        demod_cmd = (
            f"python3 app/demod_consumer.py -f {freq_plain} -s {sample_rate_plain} -t {demod} -b {float_to_plain(demod_bw)}"
        )
        demod_cmd += f" -d {float_to_plain(DEMUX_DEMOD_TARGET)}"
        demod_cmd += f" -a {AUDIO_RATE}"
        if metrics:
            demod_cmd += " -m"

    print(f"[main] hackrf_cmd: {hackrf_cmd}")
    print(f"[main] demod_cmd: {demod_cmd}")
    print(f"[main] psd_cmd: {psd_cmd}")

    return hackrf_cmd, demod_cmd, psd_cmd


def start_consumer_with_fifo(cmd: str, fifo_path: str, verbose: bool = False) -> subprocess.Popen:
    full = f"{cmd} < {shlex.quote(fifo_path)}"
    print(f"[proc] Starting consumer: {full} (verbose={verbose})")
    if verbose:
        p = subprocess.Popen(full, shell=True, preexec_fn=os.setsid)
    else:
        p = subprocess.Popen(full, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setsid)
    return p


def start_hackrf(cmd: str) -> subprocess.Popen:
    parts = shlex.split(cmd)
    print(f"[proc] Starting hackrf_transfer: {' '.join(parts)}")
    p = subprocess.Popen(parts, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setsid, bufsize=65536)
    return p


def tee_stream(src, fifo_paths: List[str], chunk_size: int = 256 * 1024):
    """
    Read from src and write to all fifo_paths. If a FIFO's reader disappears (BrokenPipe),
    try to re-open that FIFO periodically instead of removing it forever.
    Also check persistent 'current_mode' each loop; exit if not 'realtime'.
    """
    # mapping path -> open file object or None
    writers = {p: None for p in fifo_paths}
    last_open_try = {p: 0.0 for p in fifo_paths}
    OPEN_RETRY_INTERVAL = 0.5  # seconds

    try:
        while True:
            # Check persistent mode
            try:
                key = get_persist_var("current_mode", cfg.PERSIST_FILE)
            except Exception as e:
                print(f"[tee] Warning reading current_mode: {e}")
                key = None
            if str(key) != "realtime":
                print(f"[tee] current_mode != 'realtime' ({key!r}) -> exiting tee_stream loop")
                break

            # Ensure writer fds are open (or try to open them periodically)
            now = time.time()
            for p in fifo_paths:
                if writers[p] is None and (now - last_open_try[p]) >= OPEN_RETRY_INTERVAL:
                    last_open_try[p] = now
                    try:
                        # open for writing in binary buffered mode
                        f = open(p, "wb", buffering=256 * 1024)
                        writers[p] = f
                        print(f"[fifo] Opened write-end {p}")
                    except FileNotFoundError:
                        # FIFO might have been removed externally; warn and continue
                        print(f"[tee] FIFO not found when opening {p}")
                    except OSError as e:
                        # No reader yet or other OS error; keep trying
                        # errno=ENXIO when no reader for O_WRONLY open in non-blocking; but here open() blocks until reader,
                        # so we just catch and retry gracefully
                        print(f"[tee] Could not open {p} for writing yet: {e}")

            # read chunk from source
            data = src.read(chunk_size)
            if not data:
                print("[tee] Source EOF")
                break

            # write to all writers; on BrokenPipe/OSError close and mark None (will be reopened later)
            for p, f in list(writers.items()):
                if f is None:
                    continue
                try:
                    f.write(data)
                except BrokenPipeError:
                    print(f"[tee] BrokenPipe writing to {p}; will try to reopen later")
                    try:
                        f.close()
                    except Exception:
                        pass
                    writers[p] = None
                except OSError as e:
                    # other write error (e.g., errno=EINVAL if FIFO gone)
                    print(f"[tee] OSError writing to {p}: {e}; closing and will reopen")
                    try:
                        f.close()
                    except Exception:
                        pass
                    writers[p] = None
                except Exception as e:
                    print(f"[tee] Unexpected write error to {p}: {e}; closing and will reopen")
                    try:
                        f.close()
                    except Exception:
                        pass
                    writers[p] = None

            # flush remaining alive writers
            for p, f in list(writers.items()):
                if f is None:
                    continue
                try:
                    f.flush()
                except Exception:
                    # on flush error, close and mark None
                    try:
                        f.close()
                    except Exception:
                        pass
                    writers[p] = None

            # If no writers are currently open, back off briefly to avoid tight-loop
            if all(f is None for f in writers.values()):
                time.sleep(0.05)

    except KeyboardInterrupt:
        print("[tee] Interrupted by user")
    except Exception as e:
        print(f"[tee] Exception in tee: {e}")
    finally:
        for f in list(writers.values()):
            try:
                if f:
                    f.close()
            except Exception:
                pass


def terminate_process(proc: Optional[subprocess.Popen], name: str) -> None:
    if not proc:
        return
    try:
        if proc.poll() is None:
            print(f"[proc] Terminating {name} (pid {proc.pid})")
            proc.terminate()
            time.sleep(0.2)
            if proc.poll() is None:
                print(f"[proc] Killing {name} (pid {proc.pid})")
                proc.kill()
    except Exception as e:
        print(f"[proc] Error terminating {name}: {e}")


def run_pipeline(freq: float, rate: float, rbw: float, demod: Optional[str], bw: Optional[float], metrics: bool, verbose: bool) -> int:
    FREQ = freq
    FREQ_HACK = int(FREQ)
    FREQ_PLAIN = float_to_plain(FREQ)

    HACKRF_SAMPLE_RATE = rate
    SAMPLE_RATE_HACK = int(HACKRF_SAMPLE_RATE)
    SAMPLE_RATE_PLAIN = float_to_plain(HACKRF_SAMPLE_RATE)

    fifo_list = [FIFO_PSD]
    if demod:
        fifo_list.append(FIFO_DEMOD)

    fm = FifoManager(fifo_list)
    fm.create()

    hackrf_cmd, demod_cmd, psd_cmd = build_cmds(FREQ_HACK, SAMPLE_RATE_HACK, FREQ_PLAIN, SAMPLE_RATE_PLAIN, rbw, demod, bw, metrics)

    psd_proc = start_consumer_with_fifo(psd_cmd, FIFO_PSD, verbose)
    demod_proc = None
    if demod and demod_cmd:
        demod_proc = start_consumer_with_fifo(demod_cmd, FIFO_DEMOD, verbose)

    time.sleep(0.15)

    hackrf_proc = start_hackrf(hackrf_cmd)

    try:
        # trap signals and convert to KeyboardInterrupt
        def _handler(sig, frame):
            print(f"[main] Received signal {sig}, exiting")
            raise KeyboardInterrupt()

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)

        if hackrf_proc.stdout is None:
            print("[main] hackrf stdout not available; exiting")
        else:
            # pass list of fifo paths to tee_stream which manages open/reopen logic
            tee_stream(hackrf_proc.stdout, fifo_list)

    except KeyboardInterrupt:
        print("[main] Interrupted by user, shutting down")
    except Exception as exc:
        print(f"[main] Unexpected exception: {exc}")
    finally:
        print("[main] Cleaning up processes and FIFOs...")

        terminate_process(hackrf_proc, "hackrf")
        terminate_process(psd_proc, "psd_consumer")
        terminate_process(demod_proc, "demod_consumer")

        try:
            if hackrf_proc and hackrf_proc.stdout:
                hackrf_proc.stdout.close()
        except Exception:
            pass

        time.sleep(0.2)
        for label, proc in (("psd", psd_proc), ("demod", demod_proc), ("hackrf", hackrf_proc)):
            if proc:
                try:
                    out, err = proc.communicate(timeout=0.1)
                    if out:
                        print(f"[{label}] stdout (truncated): {out[:200]!r}")
                    if err:
                        print(f"[{label}] stderr (truncated): {err[:400]!r}")
                except subprocess.TimeoutExpired:
                    print(f"[{label}] did not exit quickly; killing")
                    try:
                        proc.kill()
                    except Exception:
                        pass
                except Exception as e:
                    print(f"[{label}] communicate error: {e}")

        fm.unlink()

    print("[main] Done.")
    return 0


def numeric_type(x):
    try:
        return float(x)
    except Exception:
        raise argparse.ArgumentTypeError(f"Invalid numeric value: {x}")


def parse_args() -> argparse.Namespace:
    epilog = (
        "Examples:\n"
        "  realtime_runner.py -f 98000000 -s 20000000 -w 10000\n"
        "  realtime_runner.py -f 98000000 -s 20000000 -w 10000 -d FM -b 200000\n"
    )
    parser = argparse.ArgumentParser(
        prog="realtime_runner.py",
        description="HackRF realtime processor.\nRequired: frequency, sample rate, RBW.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("-f", "--freq", required=True, type=numeric_type, help="Center frequency in Hz")
    parser.add_argument("-s", "--rate", required=True, type=numeric_type, help="Sample rate in Hz")
    parser.add_argument("-w", "--rbw", required=True, type=numeric_type, help="RBW resolution bandwidth in Hz")
    parser.add_argument("-d", "--demod", choices=["FM", "AM"], type=str.upper, help="Demodulation type")
    parser.add_argument("-b", "--bw", type=numeric_type, help="Demod bandwidth in Hz")
    parser.add_argument("-m", "--metrics", action="store_true", help="Enable metrics")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose")

    for g in parser._action_groups:
        if g.title == "optional arguments":
            g.title = "options"

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    if not (1e6 <= args.freq <= 6e9):
        parser.error("frequency must be between 1e6 and 6e9 Hz")
    if not (1e6 <= args.rate <= 6e9):
        parser.error("sample rate must be between 1e6 and 6e9 Hz")
    if not (args.rbw > 1):
        parser.error("rbw must be greater than 1 Hz")
    if args.bw is not None:
        if args.bw <= 0:
            parser.error("bw must be positive")
        if args.bw > args.rate:
            parser.error("bw must not exceed sample rate")
        if args.bw > args.freq:
            parser.error("bw must not exceed center frequency (Hz)")

    return args


def main() -> int:
    args = parse_args()
    freq = float(args.freq)
    rate = float(args.rate)
    rbw = float(args.rbw)
    demod = args.demod
    bw = float(args.bw) if args.bw is not None else None
    metrics = bool(args.metrics)
    verbose = bool(args.verbose)
    return run_pipeline(freq, rate, rbw, demod, bw, metrics, verbose)


if __name__ == "__main__":
    rc = main()
    sys.exit(rc)
