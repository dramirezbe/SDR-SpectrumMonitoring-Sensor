import cfg
import os
import csv
import json
import time
import asyncio
from datetime import datetime
from utils import ZmqPub, ZmqSub, RequestClient 

log = cfg.set_logger()

topic_data = "data"
topic_sub = "acquire"

# --- NEW: Metrics and File Management ---
class MetricsManager:
    def __init__(self, mac):
        self.mac = mac
        self.folder = "CSV_metrics_service"
        self.max_files = 100
        self.ensure_folder()

    def ensure_folder(self):
        if not os.path.exists(self.folder):
            os.makedirs(self.folder)

    def get_size_metrics(self, data_dict, prefix=""):
        """Calculates payload size in various units based on JSON string length."""
        try:
            # Measure size of the JSON payload as it would be sent over wire
            json_str = json.dumps(data_dict)
            size_bytes = len(json_str.encode('utf-8'))
        except Exception:
            size_bytes = 0
            
        return {
            f"{prefix}_bytes": size_bytes,
            f"{prefix}_Kb": round((size_bytes * 8) / 1000, 4), # Kilobits
            f"{prefix}_KB": round(size_bytes / 1024, 4),       # KiloBytes
            f"{prefix}_Mb": round((size_bytes * 8) / 1000000, 6), # Megabits
            f"{prefix}_MB": round(size_bytes / (1024 * 1024), 6)  # MegaBytes
        }

    def rotate_files(self):
        """Ensures we don't exceed max_files by deleting the oldest."""
        files = [os.path.join(self.folder, f) for f in os.listdir(self.folder) if f.endswith(".csv")]
        if len(files) >= self.max_files:
            # Sort by creation time (oldest first)
            files.sort(key=os.path.getctime)
            # Remove oldest files until we have space
            while len(files) >= self.max_files:
                try:
                    os.remove(files[0])
                    files.pop(0)
                except OSError as e:
                    log.error(f"Error rotating CSV files: {e}")

    def save_metrics(self, server_params, sent_params, metrics):
        self.rotate_files()

        # Create filename: timestamp(humanlike)_mac.csv
        human_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{human_time}_{self.mac}.csv"
        filepath = os.path.join(self.folder, filename)

        # Merge all data into one dictionary for the CSV row
        # 1. Base Info
        row_data = {
            "timestamp_ms": cfg.get_time_ms(),
            "mac_address": self.mac,
        }
        # 2. Add Metrics (Timing, Sizes, Array info)
        row_data.update(metrics)
        # 3. Add Server Params (Config from API)
        # Prefix keys to avoid collisions
        for k, v in server_params.items():
            row_data[f"cfg_{k}"] = v
        # 4. Add Sent Params (Data sent to API)
        for k, v in sent_params.items():
            # We skip sending the huge Pxx array to CSV to keep it readable, 
            # but we keep the metadata (freqs, etc)
            if k != "Pxx":
                row_data[f"sent_{k}"] = v

        try:
            # Write to CSV
            with open(filepath, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=row_data.keys())
                writer.writeheader()
                writer.writerow(row_data)
            log.info(f"Metrics saved to {filepath}")
        except Exception as e:
            log.error(f"Failed to save CSV: {e}")

# ----------------------------------------

def fetch_job(client):
    """
    Fetches job configuration.
    Includes safety checks to prevent int(None) crashes.
    """
    # Start Timer for Fetch
    t0 = time.perf_counter()
    rc_returned, resp = client.get(f"/{cfg.get_mac()}/configuration")
    t1 = time.perf_counter()
    
    fetch_duration_ms = (t1 - t0) * 1000

    json_payload = {}
    
    if resp is not None and resp.status_code == 200:
        try:
            json_payload = resp.json()
        except Exception:
            json_payload = {}
    
    if not json_payload:
        return {}, resp, fetch_duration_ms

    # --- FIX 1: Safety Wrappers ---
    center = int(json_payload.get("center_frequency") or 0)
    span = int(json_payload.get("span") or 0)
    # ------------------------------------------
        
    return {
        "center_freq_hz": center,
        "rbw_hz": json_payload.get("resolution_hz"),
        "port": json_payload.get("antenna_port"),
        "win": json_payload.get("window"),
        "overlap": json_payload.get("overlap"),
        "sample_rate_hz": json_payload.get("sample_rate_hz"),
        "lna_gain": json_payload.get("lna_gain"),
        "vga_gain": json_payload.get("vga_gain"),
        "antenna_amp": json_payload.get("antenna_amp"),
        "span": span
    }, resp, fetch_duration_ms

def fetch_data(payload):
    # Extract raw data from C-Engine
    Pxx = payload.get("Pxx", [])
    start_freq_hz = payload.get("start_freq_hz")
    end_freq_hz = payload.get("end_freq_hz")
    timestamp = cfg.get_time_ms()
    mac = cfg.get_mac()

    return {
        "Pxx": Pxx,
        "start_freq_hz": start_freq_hz,
        "end_freq_hz": end_freq_hz,
        "timestamp": timestamp,
        "mac": mac
    }

async def run_server():
    log.info("Starting server loop...")
    
    # Initialize Metrics Manager using cfg.get_mac()
    metrics_mgr = MetricsManager(cfg.get_mac())

    pub = ZmqPub(addr=cfg.IPC_CMD_ADDR)
    sub = ZmqSub(addr=cfg.IPC_DATA_ADDR, topic=topic_data)

    await asyncio.sleep(0.5)
    client = RequestClient(cfg.API_URL, verbose=True, logger=log)
    
    while True:
        try:
            log.info("Fetching job configuration...")
            
            # 1. Fetch Job (Returns dict, response object, and duration)
            json_dict, resp, fetch_time_ms = fetch_job(client)

            if resp is None or resp.status_code != 200 or not json_dict:
                log.warning("Fetch failed or empty. Retrying in 5s...")
                await asyncio.sleep(5)
                continue

            # Calculate Server Package Weight (Incoming Config)
            config_size_metrics = metrics_mgr.get_size_metrics(json_dict, prefix="server_pkg")

            # --- LOGGING REQ 1: SERVER PARAMS ---
            log.info("----SERVER PARAMS-----")
            for key, val in json_dict.items():
                log.info(f"{key:<18}: {val}")
            # ------------------------------------

            # --- FIX 2: Variable Naming & Logic ---
            desired_span = int(json_dict.get("span", 0))
            
            if desired_span <= 0:
                log.warning(f"Invalid span received ({desired_span}). Skipping cycle.")
                await asyncio.sleep(5)
                continue

            # 2. Send Petition to ZMQ
            t_before_zmq = time.perf_counter()
            pub.public_client(topic_sub, json_dict)
            t_after_zmq = time.perf_counter() # Time when petition sent
            
            log.info("Waiting for PSD data from C engine (5s Timeout)...")

            try:
                # 3. Wait for C-Engine Response
                raw_data = await asyncio.wait_for(sub.wait_msg(), timeout=5)
                t_zmq_response = time.perf_counter() # Time when data received

                # 4. Format into Dictionary
                data_dict = fetch_data(raw_data)
                
                # --- SPAN LOGIC START ---
                raw_pxx = data_dict.get('Pxx')
                
                if raw_pxx and len(raw_pxx) > 0:
                    current_start = float(data_dict.get('start_freq_hz'))
                    current_end = float(data_dict.get('end_freq_hz'))
                    current_bw = current_end - current_start
                    
                    len_Pxx = len(raw_pxx)

                    if current_bw > 0 and desired_span < current_bw:
                        center_freq = current_start + (current_bw / 2)
                        ratio = desired_span / current_bw
                        bins_to_keep = int(len_Pxx * ratio)

                        if bins_to_keep > len_Pxx: bins_to_keep = len_Pxx
                        if bins_to_keep < 1: bins_to_keep = 1

                        start_idx = int((len_Pxx - bins_to_keep) // 2)
                        end_idx = start_idx + bins_to_keep

                        data_dict['Pxx'] = raw_pxx[start_idx : end_idx]
                        
                        data_dict['start_freq_hz'] = center_freq - (desired_span / 2)
                        data_dict['end_freq_hz'] = center_freq + (desired_span / 2)

                        log.info(f"Chopped Pxx: {len_Pxx} -> {len(data_dict['Pxx'])} bins")
                # --- SPAN LOGIC END ---

                # --- METRICS COLLECTION ---
                final_pxx = data_dict.get('Pxx', [])
                
                # Calculate Outgoing Package Weight
                outgoing_size_metrics = metrics_mgr.get_size_metrics(data_dict, prefix="outgoing_pkg")

                # Timing Calculations
                # ZMQ Send Duration: t_after_zmq - t_before_zmq
                zmq_send_duration_ms = (t_after_zmq - t_before_zmq) * 1000
                
                # Client Response Time (C-Engine): t_zmq_response - t_after_zmq
                c_engine_response_ms = (t_zmq_response - t_after_zmq) * 1000

                metrics_snapshot = {
                    "fetch_duration_ms": round(fetch_time_ms, 2),
                    "zmq_send_duration_ms": round(zmq_send_duration_ms, 2),
                    "c_engine_response_ms": round(c_engine_response_ms, 2),
                    "pxx_len": len(final_pxx) if isinstance(final_pxx, list) else 0,
                    "pxx_type": type(final_pxx).__name__,
                }
                
                # Add Size Metrics
                metrics_snapshot.update(config_size_metrics)
                metrics_snapshot.update(outgoing_size_metrics)
                # ---------------------------

                # --- LOGGING REQ 2: DATATOSEND ---
                log.info("----DATATOSEND--------")
                pxx_preview = final_pxx[:5] if isinstance(final_pxx, list) else []
                log.info(f"Pxx (First 5)     : {pxx_preview}")
                
                for key, val in data_dict.items():
                    if key != "Pxx":
                        log.info(f"{key:<18}: {val}")
                log.info("----------------------")
                # ----------------------------------

                # 5. POST to Server
                t_post_start = time.perf_counter()
                client.post_json("/data", data_dict)
                t_post_end = time.perf_counter()
                
                # Add final metric
                metrics_snapshot["upload_duration_ms"] = round((t_post_end - t_post_start) * 1000, 2)

                # SAVE TO CSV
                metrics_mgr.save_metrics(json_dict, data_dict, metrics_snapshot)

            except asyncio.TimeoutError:
                log.warning("TIMEOUT: No data received.")
                continue 
            
        except Exception as e:
            log.error(f"Unexpected error: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        pass