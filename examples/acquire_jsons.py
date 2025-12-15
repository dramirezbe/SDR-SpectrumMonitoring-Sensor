import cfg
log = cfg.set_logger()
from utils import ZmqPub, ZmqSub
import asyncio
import numpy as np
import json
import os
import time
from datetime import datetime

# --- Main Logic ---

topic_data = "data"
topic_sub = "acquire"

async def run_acquisition():
    log.info("Starting Sweeping Acquisition Sequence...")

    output_dir = "offline_orchestrator"
    os.makedirs(output_dir, exist_ok=True)

    # Setup ZMQ
    pub = ZmqPub(addr=cfg.IPC_CMD_ADDR)
    sub = ZmqSub(addr=cfg.IPC_DATA_ADDR, topic=topic_data)

    # Frequencies: 98.1 MHz to 99.1 MHz
    start_freq = 98100000
    end_freq =   99100000
    step_freq =    200000
    
    target_freqs = list(range(start_freq, end_freq + 1, step_freq))
    target_rbws = [1000, 10000, 100000] 
    acquisitions_per_setting = 1
    
    # Base Configuration
    # NOTE: The C-Engine now processes 'span'. 
    # If span < sample_rate, the output JSON will contain cropped data.
    base_config = {
        "rf_mode": "realtime",
        "span": 20000000,             
        "window": "hamming",
        "overlap": 0.5,
        "sample_rate_hz": 20000000,
        "lna_gain": 0,
        "vga_gain": 0,
        "scale": "dBm",
        "antenna_amp": False,
        "antenna_port": 1
    }

    await asyncio.sleep(0.5)
    
    for freq in target_freqs:
        for rbw in target_rbws:
            
            base_config["center_freq_hz"] = freq
            base_config["rbw_hz"] = rbw
            
            log.info(f"--- Freq {freq/1e6} MHz | RBW {rbw} Hz ---")

            for i in range(acquisitions_per_setting):
                while True:
                    try:
                        # Trigger
                        pub.public_client(topic_sub, base_config)

                        # Wait for Data
                        raw_data = await asyncio.wait_for(sub.wait_msg(), timeout=5)
                        
                        raw_pxx = raw_data.get("Pxx", [])
                        if isinstance(raw_pxx, np.ndarray):
                            raw_pxx = raw_pxx.tolist()
                        
                        # Save
                        human_date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                        file_name = f"freq{freq}_rbw{rbw}_idx{i+1}_{human_date}.json"
                        file_path = os.path.join(output_dir, file_name)
                        
                        output_data = base_config.copy()
                        output_data.update({
                            "acquisition_index": i + 1,
                            "pxx_length": len(raw_pxx), # Will reflect C-engine cropping
                            "pxx_data": raw_pxx,
                            "timestamp_epoch": time.time(),
                            "human_date": human_date
                        })
                        
                        with open(file_path, 'w') as f:
                            json.dump(output_data, f, indent=4)
                        
                        log.info(f"Saved {file_name} (Bins: {len(raw_pxx)})")
                        break 

                    except asyncio.TimeoutError:
                        log.warning(f"Timeout at Freq {freq}. Retrying...")
                    except Exception as e:
                        log.error(f"Error: {e}. Retrying...")
                        await asyncio.sleep(1)

    log.info("Acquisition sequence complete.")
    pub.close()
    sub.close()

if __name__ == "__main__":
    asyncio.run(run_acquisition())