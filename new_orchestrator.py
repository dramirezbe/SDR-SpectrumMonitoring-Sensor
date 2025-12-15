import asyncio
import zmq
import zmq.asyncio
import json
import os
import numpy as np

# --- Your Exact Class ---
class ZmqPairController:
    def __init__(self, addr, is_server=True, verbose=False):
        self.verbose = verbose
        self.context = zmq.asyncio.Context()
        self.socket = self.context.socket(zmq.PAIR)
        if is_server:
            # Clean up previous socket file if it exists (Linux specific)
            if addr.startswith("ipc://"):
                path = addr.replace("ipc://", "")
                if os.path.exists(path):
                    os.remove(path)
            
            self.socket.bind(addr)
        else:
            self.socket.connect(addr)

    async def send_command(self, payload: dict):
        msg = json.dumps(payload)
        await self.socket.send_string(msg)
        if self.verbose:
            print(f"[PY] >> Sent CMD")

    async def wait_for_data(self):
        # This awaits until C actually sends something back
        msg = await self.socket.recv_string()
        if self.verbose:
            print(f"[PY] << Received Payload")
        return json.loads(msg)

# --- Payload Parser ---
def parse_rf_payload(data: dict):
    """
    Analyzes the JSON response from the C Engine.
    """
    # Case A: C returned a PSD Analysis
    if "Pxx" in data:
        start_f = data.get("start_freq_hz", 0)
        end_f = data.get("end_freq_hz", 0)
        pxx = data["Pxx"]
        
        # Basic Stats
        count = len(pxx)
        peak_val = max(pxx)
        
        # Calculate Frequency of the Peak
        if count > 1:
            step = (end_f - start_f) / (count - 1)
            peak_idx = pxx.index(peak_val)
            peak_freq = start_f + (peak_idx * step)
        else:
            peak_freq = start_f

        print(f"   [ANALYSIS] Range: {start_f/1e6:.1f}-{end_f/1e6:.1f} MHz")
        print(f"   [ANALYSIS] Peak Power: {peak_val:.2f} dBm @ {peak_freq/1e6:.3f} MHz")
        return True

    # Case B: C returned a Status/Error message
    elif "status" in data:
        print(f"   [STATUS] C Engine says: {data['status']}")
        return True
    
    return False

# --- Main Logic Loop ---
async def main():
    ipc_addr = "ipc:///tmp/rf_engine"
    
    # 1. Init Controller
    # Python is server (binds), C is client (connects)
    controller = ZmqPairController(ipc_addr, is_server=True, verbose=True)
    
    print("--- Python Master Active ---")
    print("--- Waiting for C Engine to connect... ---")
    
    # Simple list of frequencies to cycle through
    scan_frequencies = [98000000, 100000000, 105000000] 
    
    try:
        idx = 0
        while True:
            current_freq = scan_frequencies[idx % len(scan_frequencies)]
            
            # 2. Prepare Command
            rf_config = {
                "rf_mode": "realtime",
                "center_freq_hz": current_freq,
                "sample_rate_hz": 20000000, # 20 MSps
                "window": "hann",
                "scale": "dBm",
                "overlap": 0.5,
                "lna_gain": 0,
                "vga_gain": 0,
                "antenna_amp": False,
                "antenna_port": 2,
                "span": 10000000
            }
            
            print(f"\n[STEP {idx+1}] Requesting Scan @ {current_freq/1e6} MHz...")
            
            # 3. Send and WAIT
            await controller.send_command(rf_config)
            
            # The code pauses here until C responds (could be 100ms, could be 5s)
            response = await controller.wait_for_data()
            
            # 4. Process Response
            parse_rf_payload(response)
            
            # 5. Decision Logic
            # Wait a bit before next request (or process data further)
            await asyncio.sleep(2)
            idx += 1

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        controller.context.term()

if __name__ == "__main__":
    asyncio.run(main())