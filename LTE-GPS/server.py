import cfg
log = cfg.set_logger()
from utils import ZmqPub, ZmqSub, RequestClient 
import asyncio

topic_data = "data"
topic_sub = "acquire"

def fetch_job(client):
    # ... (Same as before) ...
    rc_returned, resp = client.get(f"/{cfg.get_mac()}/configuration")
    
    json_payload = {}
    
    if resp is not None and resp.status_code == 200:
        try:
            json_payload = resp.json()
        except Exception:
            json_payload = {}
    
    if not json_payload:
        return {}, resp

    start = json_payload.get("start_freq_hz")
    end = json_payload.get("end_freq_hz")
    
    try:
        center = ((int(end) - int(start)) / 2) + int(start)
    except (TypeError, ValueError):
        return {}, resp
    
    # This is the span requested by the User/API
    desired_span = int(end) - int(start)
        
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
        "desired_span": desired_span  # Renamed for clarity
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

            # This is what the user WANTS to see
            desired_span = int(json_dict.get("desired_span"))
            log.info(f"Target Span: {desired_span}")

            pub.public_client(topic_sub, json_dict)
            log.info("Waiting for PSD data from C engine (15s Timeout)...")

            try:
                # 1. Get Data from C-Engine (Usually Full Bandwidth/Sample Rate)
                raw_data = await asyncio.wait_for(sub.wait_msg(), timeout=3)
                
                # 2. Format into Dictionary
                data_dict = fetch_data(raw_data)
                
                # --- SPAN LOGIC START ---
                raw_pxx = data_dict.get('Pxx')
                
                if raw_pxx and len(raw_pxx) > 0:
                    # Current frequencies coming from C-Engine
                    current_start = float(data_dict.get('start_freq_hz'))
                    current_end = float(data_dict.get('end_freq_hz'))
                    current_bw = current_end - current_start
                    
                    len_Pxx = len(raw_pxx)

                    # Only chop if the desired span is actually smaller than what we got
                    # (and avoid division by zero)
                    if current_bw > 0 and desired_span < current_bw:
                        
                        # Calculate center frequency
                        center_freq = current_start + (current_bw / 2)

                        # Calculate how many bins we need to KEEP
                        # Ratio = Desired / Current
                        ratio = desired_span / current_bw
                        bins_to_keep = int(len_Pxx * ratio)

                        # Make sure we don't crash if calculation is weird
                        if bins_to_keep > len_Pxx: bins_to_keep = len_Pxx
                        if bins_to_keep < 1: bins_to_keep = 1

                        # Calculate Indices (Center Crop)
                        start_idx = int((len_Pxx - bins_to_keep) // 2)
                        end_idx = start_idx + bins_to_keep

                        # Apply Slice
                        data_dict['Pxx'] = raw_pxx[start_idx : end_idx]
                        
                        # IMPORTANT: Update the start/end freq so the graph X-axis is correct!
                        data_dict['start_freq_hz'] = center_freq - (desired_span / 2)
                        data_dict['end_freq_hz'] = center_freq + (desired_span / 2)

                        log.info(f"Chopped Pxx: {len_Pxx} bins -> {len(data_dict['Pxx'])} bins")
                
                # --- SPAN LOGIC END ---

                # Preview
                Pxx_preview = data_dict.get('Pxx')
                if isinstance(Pxx_preview, list) and len(Pxx_preview) > 5:
                    Pxx_preview = Pxx_preview[:5]

                log.info(f"--- PSD Data Ready ---")
                log.info(f"Points:    {len(data_dict.get('Pxx'))}")
                log.info(f"Freq:      {data_dict.get('start_freq_hz')} - {data_dict.get('end_freq_hz')}")
                log.info("----------------------")

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