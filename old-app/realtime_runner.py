#!/usr/bin/env python3
"""
realtime_runner.py

HackRF realtime processor.
Fetches configuration from the API 'realtime' endpoint.
Required: frequency range, sample rate, RBW, gains.
Optional: demodulation parameters.

manages persistence "current_mode":
1. Sets "realtime" on start.
2. Monitors "current_mode" during execution; exits if changed externally.
3. Resets to "idle" on exit (graceful or crash).
"""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from typing import List, Optional, Tuple, Union, Dict, Any

from utils import get_persist_var, modify_persist, RequestClient
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
AUDIO_RATE = 48000
BW_DEMOD_DEFAULT = 200e3

FIFO_PSD = "/tmp/iq_psd"
FIFO_DEMOD = "/tmp/iq_demod"

PSD_FILE = (cfg.APP_DIR / "psd_consumer.py").resolve()
DEMOD_FILE = (cfg.APP_DIR / "demod_consumer.py").resolve()


class FifoManager:
    def __init__(self, paths: List[str]) -> None:
        self.paths = paths

    def create(self) -> None:
        for p in self.paths:
            try:
                os.mkfifo(p)
                log.info(f"[fifo] Created {p}")
            except FileExistsError:
                log.info(f"[fifo] Already exists: {p}")

    def unlink(self) -> None:
        for p in self.paths:
            try:
                os.unlink(p)
                log.info(f"[fifo] Unlinked {p}")
            except FileNotFoundError:
                pass


def build_cmds(
    center_freq_hz: int,
    sample_rate_hz: int,
    rbw: int,
    lna_gain: int,
    vga_gain: int,
    amp_enable: bool,
    antenna_enable: int,
    demod_conf: Optional[Dict[str, Any]],
) -> Tuple[str, Optional[str], str]:
    
    # Strings for commands
    freq_plain = float_to_plain(center_freq_hz)
    sample_rate_plain = float_to_plain(sample_rate_hz)
    rbw_plain = float_to_plain(rbw)
    
    # HackRF Command
    # -f freq, -s rate, -l lna, -g vga, -a amp_enable (1/0), -p antenna_enable (port power 1/0)
    amp_val = 1 if amp_enable else 0
    # Ensure antenna_enable is 1 or 0
    ant_val = 1 if antenna_enable else 0
    
    hackrf_cmd = (
        f"hackrf_transfer -r - -f {center_freq_hz} -s {sample_rate_hz} "
        f"-l {lna_gain} -g {vga_gain} -a {amp_val} -p {ant_val}"
    )

    # PSD Command
    psd_cmd = (
        f"{cfg.PYTHON_EXEC} {PSD_FILE} -f {freq_plain} -s {sample_rate_plain} "
        f"-w {rbw_plain} --scale dbm"
    )

    # Demod Command
    demod_cmd = None
    if demod_conf:
        d_type = demod_conf.get("type", "FM")
        d_bw = demod_conf.get("bandwidth_hz", BW_DEMOD_DEFAULT)
        d_center = demod_conf.get("center_freq_hz", center_freq_hz)
        d_metrics = demod_conf.get("with_metrics", False)
        
        # Calculate offset for the consumer: target_freq - sdr_center
        # The consumer needs to shift the spectrum by this amount to center the target
        offset = d_center - center_freq_hz
        
        demod_cmd = (
            f"{cfg.PYTHON_EXEC} {DEMOD_FILE} -f {freq_plain} -s {sample_rate_plain} "
            f"-t {d_type} -b {float_to_plain(d_bw)}"
        )
        # Pass the calculated offset to -d
        demod_cmd += f" -d {float_to_plain(offset)}"
        demod_cmd += f" -a {AUDIO_RATE}"
        if d_metrics:
            demod_cmd += " -m"

    log.info(f"hackrf_cmd: {hackrf_cmd}")
    log.info(f"demod_cmd: {demod_cmd}")
    log.info(f"psd_cmd: {psd_cmd}")

    return hackrf_cmd, demod_cmd, psd_cmd


def start_consumer_with_fifo(cmd: str, fifo_path: str, verbose: bool = False) -> subprocess.Popen:
    full = f"{cmd} < {shlex.quote(fifo_path)}"
    log.info(f"[proc] Starting consumer: {full} (verbose={verbose})")
    if verbose:
        p = subprocess.Popen(full, shell=True, preexec_fn=os.setsid)
    else:
        p = subprocess.Popen(full, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setsid)
    return p


def start_hackrf(cmd: str) -> subprocess.Popen:
    parts = shlex.split(cmd)
    log.info(f"[proc] Starting hackrf_transfer: {' '.join(parts)}")
    p = subprocess.Popen(parts, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setsid, bufsize=65536)
    return p


def tee_stream(src, fifo_paths: List[str], chunk_size: int = 256 * 1024):
    """
    Read from src and write to all fifo_paths. 
    Monitors 'current_mode' from persistence. If it is not 'realtime', stops.
    """
    # mapping path -> open file object or None
    writers = {p: None for p in fifo_paths}
    last_open_try = {p: 0.0 for p in fifo_paths}
    OPEN_RETRY_INTERVAL = 0.5  # seconds

    try:
        while True:
            # --- Persistence Guard ---
            try:
                key = get_persist_var("current_mode", cfg.PERSIST_FILE)
            except Exception as e:
                log.info(f"[tee] Warning reading current_mode: {e}")
                key = None # Fail safe, don't break yet
            
            # Strict check: We only run if mode is "realtime". 
            # If it changed to "idle", "campaign", or "error", we yield.
            if str(key) != "realtime":
                log.info(f"[tee] Persistence change detected (mode='{key}'). Exiting loop.")
                # Do NOT set to idle here. If it changed to 'campaign', we shouldn't overwrite it.
                break
            # -------------------------

            # Ensure writer fds are open (or try to open them periodically)
            now = time.time()
            for p in fifo_paths:
                if writers[p] is None and (now - last_open_try[p]) >= OPEN_RETRY_INTERVAL:
                    last_open_try[p] = now
                    try:
                        # open for writing in binary buffered mode
                        f = open(p, "wb", buffering=256 * 1024)
                        writers[p] = f
                        log.info(f"[fifo] Opened write-end {p}")
                    except FileNotFoundError:
                        # FIFO might have been removed externally; warn and continue
                        log.info(f"[tee] FIFO not found when opening {p}")
                    except OSError as e:
                        # No reader yet or other OS error
                        log.info(f"[tee] Could not open {p} for writing yet: {e}")

            # read chunk from source
            data = src.read(chunk_size)
            if not data:
                log.info("[tee] Source EOF")
                break

            # write to all writers; on BrokenPipe/OSError close and mark None (will be reopened later)
            for p, f in list(writers.items()):
                if f is None:
                    continue
                try:
                    f.write(data)
                except BrokenPipeError:
                    log.error(f"[tee] BrokenPipe writing to {p}; will try to reopen later")
                    try:
                        f.close()
                    except Exception:
                        pass
                    writers[p] = None
                except OSError as e:
                    # other write error (e.g., errno=EINVAL if FIFO gone)
                    log.error(f"[tee] OSError writing to {p}: {e}; closing and will reopen")
                    try:
                        f.close()
                    except Exception:
                        pass
                    writers[p] = None
                except Exception as e:
                    log.error(f"[tee] Unexpected write error to {p}: {e}; closing and will reopen")
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
        log.info("[tee] Interrupted by user")
    except Exception as e:
        log.error(f"[tee] Exception in tee: {e}")
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
            log.info(f"[proc] Terminating {name} (pid {proc.pid})")
            proc.terminate()
            time.sleep(0.2)
            if proc.poll() is None:
                log.info(f"[proc] Killing {name} (pid {proc.pid})")
                proc.kill()
    except Exception as e:
        log.error(f"[proc] Error terminating {name}: {e}")


def fetch_configuration() -> Optional[Dict[str, Any]]:
    """
    Fetches the realtime configuration from the API.
    """
    log.info("Fetching configuration from API...")
    client = RequestClient(cfg.API_URL, api_key=cfg.get_mac())
    
    # Assuming endpoint is 'realtime' based on user comments
    rc, resp = client.get(cfg.REALTIME_URL)
    
    if rc != 0 or not resp:
        log.error(f"Failed to fetch configuration. rc={rc}")
        return None
    
    try:
        return resp.json()
    except Exception as e:
        log.error(f"Failed to parse JSON: {e}")
        return None


def main() -> int:
    # 1. SET MODE TO REALTIME STARTUP
    log.info("Setting persistent mode to 'realtime'")
    modify_persist("current_mode", "realtime", cfg.PERSIST_FILE)

    config = fetch_configuration()
    if not config:
        log.error("No configuration available. Exiting.")
        modify_persist("current_mode", "idle", cfg.PERSIST_FILE)
        return 1

    # Parse Configuration
    try:
        start_freq = int(config.get("start_freq_hz", 0))
        end_freq = int(config.get("end_freq_hz", 0))
        
        if start_freq == 0 or end_freq == 0:
            raise ValueError("Invalid start/end frequency")

        center_freq_hz = int((start_freq + end_freq) / 2)
        sample_rate_hz = int(config.get("sample_rate_hz", 20000000))
        rbw = int(config.get("resolution_hz", 10000))
        
        lna_gain = int(config.get("lna_gain", 24))
        vga_gain = int(config.get("vga_gain", 20))
        
        amp_enable = bool(config.get("antenna_amp", False))
        # "antenna_port": 1 implies antenna power enable in HackRF terms
        antenna_enable = int(config.get("antenna_port", 0))
        
        demod_conf = config.get("demodulation") # Can be None/Null

    except Exception as e:
        log.error(f"Configuration error: {e}")
        modify_persist("current_mode", "idle", cfg.PERSIST_FILE)
        return 1

    log.info(f"Configured: Center={center_freq_hz}Hz Rate={sample_rate_hz}Hz LNA={lna_gain} VGA={vga_gain}")

    fifo_list = [FIFO_PSD]
    if demod_conf:
        fifo_list.append(FIFO_DEMOD)

    fm = FifoManager(fifo_list)
    fm.create()

    hackrf_cmd, demod_cmd, psd_cmd = build_cmds(
        center_freq_hz, sample_rate_hz, rbw, 
        lna_gain, vga_gain, amp_enable, antenna_enable, 
        demod_conf
    )

    psd_proc = start_consumer_with_fifo(psd_cmd, FIFO_PSD, verbose=False)
    demod_proc = None
    if demod_conf and demod_cmd:
        demod_proc = start_consumer_with_fifo(demod_cmd, FIFO_DEMOD, verbose=False)

    time.sleep(0.15)

    hackrf_proc = start_hackrf(hackrf_cmd)

    try:
        # trap signals and convert to KeyboardInterrupt
        def _handler(sig, frame):
            log.info(f"Received signal {sig}, exiting")
            raise KeyboardInterrupt()

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)

        if hackrf_proc.stdout is None:
            log.info("hackrf stdout not available; exiting")
        else:
            # pass list of fifo paths to tee_stream which manages open/reopen logic
            tee_stream(hackrf_proc.stdout, fifo_list)

    except KeyboardInterrupt:
        log.error("Interrupted by user, shutting down")
    except Exception as exc:
        log.error(f"Unexpected exception: {exc}")
    finally:
        log.info("Cleaning up processes and FIFOs...")

        # 2. RESET MODE TO IDLE ON SHUTDOWN (Only if it's currently realtime)
        try:
            current = get_persist_var("current_mode", cfg.PERSIST_FILE)
            if str(current) == "realtime":
                log.info("Releasing lock: Setting persistent mode to 'idle'")
                modify_persist("current_mode", "idle", cfg.PERSIST_FILE)
            else:
                log.info(f"Mode is '{current}', not resetting to idle (assuming external change)")
        except Exception as e:
            log.error(f"Error resetting persistent mode: {e}")

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
                        log.error(f"[{label}] stdout (truncated): {out[:200]!r}")
                    if err:
                        log.error(f"[{label}] stderr (truncated): {err[:400]!r}")
                except subprocess.TimeoutExpired:
                    log.error(f"[{label}] did not exit quickly; killing")
                    try:
                        proc.kill()
                    except Exception:
                        pass
                except Exception as e:
                    print(f"[{label}] communicate error: {e}")

        fm.unlink()

    log.info("Done.")
    return 0


if __name__ == "__main__":
    rc = cfg.run_and_capture(main, cfg.LOG_FILES_NUM)
    sys.exit(rc)