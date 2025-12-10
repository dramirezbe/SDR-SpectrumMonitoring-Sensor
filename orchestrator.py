import cfg
log = cfg.set_logger()
from utils import ZmqPub, ZmqSub, RequestClient 
import asyncio

topic_data = "data"
topic_sub = "acquire"

def fetch_job(client):
    """
    Fetches job configuration.
    Includes safety checks to prevent int(None) crashes.
    """
    rc_returned, resp = client.get(f"/{cfg.get_mac()}/configuration")
    
    json_payload = {}
    
    if resp is not None and resp.status_code == 200:
        try:
            json_payload = resp.json()
        except Exception:
            json_payload = {}
    
    if not json_payload:
        return {}, resp

    # --- FIX 1: Safety Wrappers ---
    # We use ( .get() or 0 ) to ensure we don't try to int(None)
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
        "span": span  # mapped correctly now
    }, resp

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
    pub = ZmqPub(addr=cfg.IPC_CMD_ADDR)
    sub = ZmqSub(addr=cfg.IPC_DATA_ADDR, topic=topic_data)

    await asyncio.sleep(0.5)
    client = RequestClient(cfg.API_URL, verbose=True, logger=log)
    
    while True:
        try:
            log.info("Fetching job configuration...")
            json_dict, resp = fetch_job(client)

            if resp is None or resp.status_code != 200 or not json_dict:
                log.warning("Fetch failed or empty. Retrying in 5s...")
                await asyncio.sleep(5)
                continue

            # --- LOGGING REQ 1: SERVER PARAMS ---
            log.info("----SERVER PARAMS-----")
            for key, val in json_dict.items():
                log.info(f"{key:<18}: {val}")
            # ------------------------------------

            # --- FIX 2: Variable Naming & Logic ---
            # Use 'span' key which we ensured exists in fetch_job
            desired_span = int(json_dict.get("span", 0))
            
            if desired_span <= 0:
                log.warning(f"Invalid span received ({desired_span}). Skipping cycle.")
                await asyncio.sleep(5)
                continue

            pub.public_client(topic_sub, json_dict)
            log.info("Waiting for PSD data from C engine (5s Timeout)...")

            try:
                # 1. Get Data from C-Engine
                raw_data = await asyncio.wait_for(sub.wait_msg(), timeout=10)
                
                # 2. Format into Dictionary
                data_dict = fetch_data(raw_data)
                
                # --- SPAN LOGIC START ---
                raw_pxx = data_dict.get('Pxx')
                
                if raw_pxx and len(raw_pxx) > 0:
                    current_start = float(data_dict.get('start_freq_hz'))
                    current_end = float(data_dict.get('end_freq_hz'))
                    current_bw = current_end - current_start
                    
                    len_Pxx = len(raw_pxx)

                    # Only chop if valid bandwidths
                    if current_bw > 0 and desired_span < current_bw:
                        
                        center_freq = current_start + (current_bw / 2)
                        ratio = desired_span / current_bw
                        bins_to_keep = int(len_Pxx * ratio)

                        # Bounds check
                        if bins_to_keep > len_Pxx: bins_to_keep = len_Pxx
                        if bins_to_keep < 1: bins_to_keep = 1

                        # Slicing
                        start_idx = int((len_Pxx - bins_to_keep) // 2)
                        end_idx = start_idx + bins_to_keep

                        data_dict['Pxx'] = raw_pxx[start_idx : end_idx]
                        
                        # Update headers
                        data_dict['start_freq_hz'] = center_freq - (desired_span / 2)
                        data_dict['end_freq_hz'] = center_freq + (desired_span / 2)

                        log.info(f"Chopped Pxx: {len_Pxx} -> {len(data_dict['Pxx'])} bins")
                # --- SPAN LOGIC END ---

                # --- LOGGING REQ 2: DATATOSEND ---
                log.info("----DATATOSEND--------")
                
                # 1. Print first 5 points of Pxx
                final_pxx = data_dict.get('Pxx', [])
                pxx_preview = final_pxx[:5] if isinstance(final_pxx, list) else []
                log.info(f"Pxx (First 5)     : {pxx_preview}")
                
                # 2. Print rest of data (excluding the full Pxx array to save space)
                for key, val in data_dict.items():
                    if key != "Pxx":
                        log.info(f"{key:<18}: {val}")
                log.info("----------------------")
                # ----------------------------------

                client.post_json("/data", data_dict)

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