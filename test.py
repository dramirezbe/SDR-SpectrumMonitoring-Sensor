#!/usr/bin/env python3
# realtime_standalone.py

import cfg
import asyncio
import time
import sys
from utils import RequestClient, ZmqPairController, ShmStore
from functions import format_data_for_upload, GlobalSys, SysState

# --- HARDCODED CONFIGURATION ---
# Replace these values with your desired hardware settings
HARDCODED_CONFIG = {
    "rf_mode": "realtime",
    "center_freq_hz": 98000000,  # 2.4 GHz
    "sample_rate_hz": 20000000,    # 20 MHz
    "rbw_hz": 100000,
    "window": "hamming",
    "scale": "dbm",
    "overlap": 0.5,
    "lna_gain": 36,
    "vga_gain": 10,
    "antenna_amp": True,
    "antenna_port": 1,
    "span": 20000000,
    "ppm_error": 0
}

async def run_standalone_realtime():
    log = cfg.set_logger()
    store = ShmStore()
    client = RequestClient(cfg.API_URL, mac_wifi=cfg.get_mac(), timeout=(5, 15), verbose=False)
    
    log.info("🚀 Starting Standalone Realtime Logic (Hardcoded Params)")
    GlobalSys.set(SysState.REALTIME)

    # Initialize the ZMQ Controller
    controller = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False)

    try:
        async with controller as zmq_ctrl:
            log.info(f"Connected to C-Engine at {cfg.IPC_ADDR}")
            
            while True:
                # 1. Start timer for C-Engine Petition
                start_time = time.perf_counter()

                # 2. Send the hardcoded petition
                await zmq_ctrl.send_command(HARDCODED_CONFIG)

                try:
                    # 3. Wait for Data response
                    # Note: timeout should be slightly longer than your expected sweep time
                    raw_payload = await asyncio.wait_for(zmq_ctrl.wait_for_data(), timeout=10)
                    
                    # 4. Stop timer immediately upon receipt
                    end_time = time.perf_counter()
                    duration_ms = (end_time - start_time) * 1000

                    # 5. Process and Upload
                    data_dict = format_data_for_upload(raw_payload)
                    
                    # Log the performance metric
                    print(f"⏱️  C-Engine Response Time: {duration_ms:.2f} ms")

                    rc, resp = client.post_json("/data", data_dict)
                    if rc != 0:
                        log.error(f"Upload failed: {resp}")

                except asyncio.TimeoutError:
                    log.warning("⚠️  TIMEOUT: C-Engine did not respond within 10s.")
                except Exception as e:
                    log.error(f"Processing error: {e}")

                # Small breather to prevent CPU pinning
                await asyncio.sleep(0.01)

    except Exception as e:
        log.error(f"🔥 Critical Failure: {e}")
    finally:
        GlobalSys.set(SysState.IDLE)
        log.info("Closed Realtime Standalone.")

if __name__ == "__main__":
    try:
        asyncio.run(run_standalone_realtime())
    except KeyboardInterrupt:
        sys.exit(0)