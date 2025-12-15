import cfg

def format_data_for_upload(payload):
    Pxx = payload.get("Pxx", [])
    start_freq_hz = payload.get("start_freq_hz")
    end_freq_hz = payload.get("end_freq_hz")
    timestamp = cfg.get_time_ms()
    mac = cfg.get_mac()

    return {
        "Pxx": Pxx,
        "start_freq_hz": int(start_freq_hz),
        "end_freq_hz": int(end_freq_hz),
        "timestamp": timestamp,
        "mac": mac
    }