import cfg
log = cfg.set_logger()
import asyncio
import numpy as np
import json
import os
import time
import zmq
import zmq.asyncio
import logging

# --- ZMQ Classes ---

class ZmqPub:
    def __init__(self, addr, verbose=False, log=logging.getLogger(__name__)):
        self.verbose = verbose
        self._log = log
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PUB)
        self.socket.bind(addr)
        self._log.info(f"ZmqPub initialized at {addr}")

    def public_client(self, topic: str, payload: dict):
        json_msg = json.dumps(payload)
        full_msg = f"{topic} {json_msg}"
        self.socket.send_string(full_msg)
        if self.verbose:
            self._log.info(f"[ZmqPub] Sent: {full_msg}")

    def close(self):
        self.socket.close()
        self.context.term()

class ZmqSub:
    def __init__(self, addr, topic: str, verbose=False, log=logging.getLogger(__name__)):
        self.verbose = verbose
        self.topic = topic
        self._log = log
        self.context = zmq.asyncio.Context()
        self.socket = self.context.socket(zmq.SUB)
        self.socket.connect(addr)
        self.socket.subscribe(self.topic.encode('utf-8'))
        self._log.info(f"ZmqSub initialized at {addr} with topic {self.topic}")

    async def wait_msg(self):
        while True:
            full_msg = await self.socket.recv_string()
            pub_topic, json_msg = full_msg.split(" ", 1)

            if pub_topic == self.topic:
                if self.verbose:
                    print(f"[ZmqSub-{self.topic}] Received: {json_msg}")
                return json.loads(json_msg)

    async def flush(self):
        """
        Drains the ZMQ socket of any pending messages.
        This ensures the next recv() gets fresh data.
        """
        packets_dropped = 0
        try:
            while True:
                await self.socket.recv(flags=zmq.NOBLOCK)
                packets_dropped += 1
        except zmq.error.Again:
            pass
        except Exception as e:
            self._log.warning(f"Error flushing ZMQ buffer: {e}")
        
        if packets_dropped > 0:
            self._log.debug(f"Flushed {packets_dropped} old packets.")

    def close(self):
        self.socket.close()
        self.context.term()

# --- Main Logic ---

topic_data = "data"
topic_sub = "acquire"

async def run_acquisition():
    log.info("Starting Raw RBW Acquisition Sequence (Trigger Mode)...")

    # 1. Setup Directories
    output_dir = "json_spikes"
    os.makedirs(output_dir, exist_ok=True)
    log.info(f"Saving data to: {os.path.abspath(output_dir)}")

    # 2. Setup ZMQ
    pub = ZmqPub(addr=cfg.IPC_CMD_ADDR)
    sub = ZmqSub(addr=cfg.IPC_DATA_ADDR, topic=topic_data)

    # 3. Define Acquisition Parameters
    target_rbws = [5000, 10000, 50000, 100000]
    acquisitions_per_rbw = 10
    
    # Base Configuration
    base_config = {
        "rf_mode": "realtime",
        "center_freq_hz": 3000000000, 
        "span": 20000000,             
        "window": "hamming",
        "overlap": 0.5,
        "sample_rate_hz": 20000000,
        "lna_gain": 0,
        "vga_gain": 0,
        "scale": "dBm",
        "antenna_amp": False
    }

    # Wait for ZMQ warmup
    await asyncio.sleep(0.5)

    for rbw in target_rbws:
        base_config["rbw_hz"] = rbw
        log.info(f"--- Starting Sequence for RBW {rbw} Hz ---")

        # --- Acquire Samples ---
        for i in range(acquisitions_per_rbw):
            try:
                # 1. FLUSH: Remove any old data from previous triggers
                await sub.flush()

                # 2. TRIGGER: Send the config to request data
                # log.info(f"Triggering acquisition {i+1} for RBW {rbw}...")
                pub.public_client(topic_sub, base_config)

                # Optional: Tiny sleep to ensure C-engine receives cmd before we wait?
                # Usually not needed if wait_msg has a timeout, but good for stability.
                # await asyncio.sleep(0.1) 

                # 3. RECEIVE: Wait for the specific response
                raw_data = await asyncio.wait_for(sub.wait_msg(), timeout=5)
                
                # Extract Data
                raw_pxx = raw_data.get("Pxx", [])
                
                # Ensure it is a list for JSON serialization
                if isinstance(raw_pxx, np.ndarray):
                    raw_pxx = raw_pxx.tolist()
                
                # --- Save to JSON ---
                file_name = f"rbw_{rbw}_acq_{i+1}.json"
                file_path = os.path.join(output_dir, file_name)
                
                output_data = {
                    "rbw_hz": rbw,
                    "center_freq_hz": base_config["center_freq_hz"],
                    "acquisition_index": i + 1,
                    "pxx_length": len(raw_pxx),
                    "pxx_data": raw_pxx,
                    "timestamp": time.time()
                }
                
                with open(file_path, 'w') as f:
                    json.dump(output_data, f, indent=4)
                
                log.info(f"Saved {file_name} (Length: {len(raw_pxx)})")

                # Optional: Sleep between acquisitions if C-engine needs recovery time
                # await asyncio.sleep(0.1)

            except asyncio.TimeoutError:
                log.warning(f"Timeout waiting for data at RBW {rbw}, Acq {i+1} - Retrying next loop")
            except Exception as e:
                log.error(f"Error during acquisition: {e}")

    log.info("Acquisition complete.")
    
    # Cleanup
    pub.close()
    sub.close()

if __name__ == "__main__":
    asyncio.run(run_acquisition())