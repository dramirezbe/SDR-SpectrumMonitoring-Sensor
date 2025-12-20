#!/usr/bin/env python3
# debug_timer.py

import cfg
import time
import sys
import asyncio
from utils import RequestClient

async def run_latency_monitor():
    # Setup standard logging and client
    log = cfg.set_logger()
    client = RequestClient(
        cfg.API_URL, 
        mac_wifi=cfg.get_mac(), 
        timeout=(5, 15), 
        verbose=True, # Set to False to keep output clean for our custom prints
        logger=log
    )

    print("\n" + "="*60)
    print(f"NETWORK LATENCY DEBUGGER - Target: {cfg.API_URL}")
    print("Press Ctrl+C to stop.")
    print("="*60 + "\n")

    iteration = 0
    while True:
        iteration += 1
        
        # 1. Start Handshake/Request Timer
        start_wall = time.perf_counter()
        
        try:
            # 2. Execute Request
            # We use the REALTIME_URL for the probe
            rc, resp = client.get(cfg.REALTIME_URL)
            
            # 3. Stop Wall Timer
            end_wall = time.perf_counter()
            
            # Calculations
            total_rtt_ms = (end_wall - start_wall) * 1000
            
            if resp is not None:
                # server_time_ms is the time the server took to process the request 
                # (Time to first byte - captured by the 'requests' library internally)
                server_time_ms = resp.elapsed.total_seconds() * 1000
                network_fly_ms = total_rtt_ms - server_time_ms
                status = resp.status_code
            else:
                server_time_ms = 0
                network_fly_ms = total_rtt_ms
                status = "FAILED/TIMEOUT"

            # 4. Output Metrics
            print(f"[{iteration:04d}] Status: {status}")
            print(f"  >> Total Fly Time (RTT): {total_rtt_ms:8.2f} ms")
            print(f"  >> Server Processing:    {server_time_ms:8.2f} ms")
            print(f"  >> Network/Handshake:    {network_fly_ms:8.2f} ms")
            print("-" * 40)

        except Exception as e:
            print(f"[{iteration:04d}] Critical Error: {e}")

        # 5. Infinite Cooldown (1 second)
        await asyncio.sleep(1.0)

if __name__ == "__main__":
    try:
        asyncio.run(run_latency_monitor())
    except KeyboardInterrupt:
        print("\nDebugger stopped by user.")
        sys.exit(0)