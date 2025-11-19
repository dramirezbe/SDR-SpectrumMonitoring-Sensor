#!/usr/bin/env python3
"""
realtime_processor.py

HackRF realtime processor.
Required: frequency, sample rate, RBW.
Optional: demod, demod bandwidth, metrics.

Examples:
  realtime_processor.py -f 98000000 -s 20000000 -w 10000
  realtime_processor.py -f 98000000 -s 20000000 -w 10000 -d FM -b 200000
  realtime_processor.py -f 98000000 -s 20000000 -w 10000 -d AM -b 10000 -m
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
DEMUX_DEMOD_TARGET = 200000.0  # default decimate-to target if not otherwise provided
TYPE_DEMOD = "FM"
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
    """Return (hackrf_cmd, demod_cmd_or_None, psd_cmd). Uses short flags for consumers."""
    hackrf_cmd = f"hackrf_transfer -r - -f {freq_hz} -s {sample_rate_hz} -a {ANTENNA_GAIN} -l {LNA} -g {VGA}"

    # PSD command: required flags -f -s -w; DO NOT append -m (psd_consumer doesn't accept -m)
    psd_cmd = f"python3 app/psd_consumer.py -f {freq_plain} -s {sample_rate_plain} -w {float_to_plain(rbw)} --scale dbm"

    # Demod command: build only if demod requested
    demod_cmd = None
    if demod:
        demod_bw = bw if bw is not None else int(BW_DEMOD_DEFAULT)
        # required flags -f -s -t -b; optional -d -a -m
        demod_cmd = (
            f"python3 app/demod_consumer.py -f {freq_plain} -s {sample_rate_plain} -t {demod} -b {float_to_plain(demod_bw)}"
        )
        # decimation target (use DEMUX_DEMOD_TARGET)
        demod_cmd += f" -d {float_to_plain(DEMUX_DEMOD_TARGET)}"
        demod_cmd += f" -a {AUDIO_RATE}"
        if metrics:
            demod_cmd += " -m"

    print(f"[main] hackrf_cmd: {hackrf_cmd}")
    print(f"[main] demod_cmd: {demod_cmd}")
    print(f"[main] psd_cmd: {psd_cmd}")

    return hackrf_cmd, demod_cmd, psd_cmd


def start_consumer_with_fifo(cmd: str, fifo_path: str, verbose: bool = False) -> subprocess.Popen:
    """
    Start a consumer that reads from fifo_path.
    If verbose is True, the consumer's stdout/stderr are inherited so its prints appear in the realtime runner.
    If verbose is False, stdout/stderr are captured (PIPE) as before.
    """
    full = f"{cmd} < {shlex.quote(fifo_path)}"
    print(f"[proc] Starting consumer: {full} (verbose={verbose})")
    if verbose:
        # inherit parent's stdout/stderr so prints appear directly here
        p = subprocess.Popen(full, shell=True, preexec_fn=os.setsid)
    else:
        # capture output (existing behavior)
        p = subprocess.Popen(full, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setsid)
    return p


def start_hackrf(cmd: str) -> subprocess.Popen:
    parts = shlex.split(cmd)
    print(f"[proc] Starting hackrf_transfer: {' '.join(parts)}")
    # Use a modest pipe buffer to smooth IO bursts
    p = subprocess.Popen(parts, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setsid, bufsize=65536)
    return p


def tee_stream(src, writers: List[object], chunk_size: int = 256 * 1024):
    """
    Read from src (binary file-like) and write to all writers.
    Use large chunk_size and buffered writers to reduce syscall overhead
    and allow transient consumer slowness without immediate stalls.
    """
    try:
        while True:
            data = src.read(chunk_size)
            if not data:
                print("[tee] Source EOF")
                break
            remove = []
            for w in writers:
                try:
                    w.write(data)
                    # don't call flush every single write; rely on buffered writer,
                    # but flush occasionally in case of long inactivity
                except BrokenPipeError:
                    print("[tee] BrokenPipe: removing writer")
                    remove.append(w)
                except BlockingIOError:
                    # writer full: mark for removal (reader side may have closed)
                    print("[tee] BlockingIOError: removing writer")
                    remove.append(w)
                except Exception as e:
                    print(f"[tee] Writer exception: {e} (removing)")
                    remove.append(w)
            for r in remove:
                try:
                    r.close()
                except Exception:
                    pass
                if r in writers:
                    writers.remove(r)
            # If there are still writers, flush once per data block (keeps consumer moving)
            if writers:
                try:
                    for w in writers:
                        try:
                            w.flush()
                        except Exception:
                            pass
                except Exception:
                    pass
            else:
                # No active writers: back off for a short while to avoid tight loop
                print("[tee] No active writers remaining; sleeping briefly before retry")
                time.sleep(0.05)
                # After sleep, continue — if still no writers then loop will end when EOF
    except KeyboardInterrupt:
        print("[tee] Interrupted by user")
    except Exception as e:
        print(f"[tee] Exception in tee: {e}")
    finally:
        for w in list(writers):
            try:
                w.close()
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

    # pass verbose flag to consumers so they can forward prints when requested
    psd_proc = start_consumer_with_fifo(psd_cmd, FIFO_PSD, verbose)
    demod_proc = None
    if demod and demod_cmd:
        demod_proc = start_consumer_with_fifo(demod_cmd, FIFO_DEMOD, verbose)

    time.sleep(0.15)

    hackrf_proc = start_hackrf(hackrf_cmd)

    fifo_writers = []
    try:
                
        FIFO_USER_BUFFER = 256 * 1024
        for p in fifo_list:
            # 'wb' open with a substantial buffering value (instead of unbuffered)
            f = open(p, "wb", buffering=FIFO_USER_BUFFER)
            fifo_writers.append(f)
            print(f"[fifo] Opened buffered write-end {p} (buffer={FIFO_USER_BUFFER})")


        def _handler(sig, frame):
            print(f"[main] Received signal {sig}, exiting")
            raise KeyboardInterrupt()

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)

        if hackrf_proc.stdout is None:
            print("[main] hackrf stdout not available; exiting")
        else:
            tee_stream(hackrf_proc.stdout, fifo_writers)

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

        for w in fifo_writers:
            try:
                w.close()
            except Exception:
                pass

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
        "  realtime_processor.py -f 98000000 -s 20000000 -w 10000\n"
        "  realtime_processor.py -f 98000000 -s 20000000 -w 10000 -d FM -b 200000\n"
        "  realtime_processor.py -f 98000000 -s 20000000 -w 10000 -d AM -b 10000 -m\n"
    )
    parser = argparse.ArgumentParser(
        prog="realtime_processor.py",
        description="HackRF realtime processor.\nRequired: frequency, sample rate, RBW.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("-f", "--freq", required=True, type=numeric_type, help="Center frequency in Hz (range: 1 MHz to 6 GHz). Example: 98000000")
    parser.add_argument("-s", "--rate", required=True, type=numeric_type, help="Sample rate in Hz (range: 1 MHz to 6 GHz). Example: 20000000")
    parser.add_argument("-w", "--rbw", required=True, type=numeric_type, help="RBW resolution bandwidth in Hz (>1). Example: 10000")
    parser.add_argument("-d", "--demod", choices=["FM", "AM"], type=str.upper, help="Demodulation type. Example: FM")
    parser.add_argument("-b", "--bw", type=numeric_type, help="Demod bandwidth in Hz (must not exceed freq or samp-rate). Example: 200000")
    parser.add_argument("-m", "--metrics", action="store_true", help="Enable metrics (boolean flag). Example: -m")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose: forward psd/demod prints to realtime runner")

    # Rename optional group header to "options"
    for g in parser._action_groups:
        if g.title == "optional arguments":
            g.title = "options"

    # If no args provided print help and exit 0
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    # Validation
    if not (1e6 <= args.freq <= 6e9):
        parser.error("frequency must be between 1e6 and 6e9 Hz (1 MHz - 6 GHz)")
    if not (1e6 <= args.rate <= 6e9):
        parser.error("sample rate must be between 1e6 and 6e9 Hz (1 MHz - 6 GHz)")
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
    # cast to proper types
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
