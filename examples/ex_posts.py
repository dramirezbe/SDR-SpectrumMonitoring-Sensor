import cfg
log = cfg.set_logger()
from utils import RequestClient
import numpy as np

t = np.linspace(0, 10, 100)
signal = (np.sin(t) + np.random.randn(1) - 20).tolist()

cli = RequestClient(cfg.API_URL, mac_wifi=cfg.get_mac(), timeout=(5, 15), verbose=True, logger=log)

post_dict_gps = {
    "lat": 37.7749,
    "lng": -122.4194,
    "alt": 100,
    "timestamp": cfg.get_time_ms(),
    "mac": cfg.get_mac()
}
post_dict_status = {
    "mac": cfg.get_mac(),
    "cpu_0": 25.5,
    "cpu_1": 30.2,
    "cpu_2": 28.7,
    "cpu_3": 22.1,
    "ram_mb": 2048,
    "swap_mb": 512,
    "disk_mb": 5120,
    "temp_c": 45.5,
    "total_ram_mb": 8192,
    "total_swap_mb": 2048,
    "total_disk_mb": 32768,
    "delta_t_ms": 1000,
    "ping_ms": 25.3,
    "timestamp_ms": cfg.get_time_ms(),
    "last_kal_ms": cfg.get_time_ms(),
    "last_ntp_ms": cfg.get_time_ms(),
    "logs": "System running normally"
}

post_dict_data = {
    "mac": cfg.get_mac(),
    #"campaign_id": 123,
    "Pxx": signal,
    "start_freq_hz": 1000,
    "end_freq_hz": 2000,
    "timestamp": cfg.get_time_ms(),
    "excursion": {
        "unit": "hz",
        "peak_to_peak_hz": 123.4,
        "peak_deviation_hz": 235123.4,
        "rms_deviation_hz": 123.4
    },
    "depth": {
        "unit": "percent",
        "peak_to_peak": 15.23,
        "peak_deviation": 100.0,
        "rms_deviation":21.32
    }
}

#log.info(f"to send: {post_dict_gps}")
#rc, resp = cli.post_json("/gps", post_dict_gps)

#log.info(f"to send: {post_dict_status}")
#rc, resp = cli.post_json("/status", post_dict_status)

log.info(f"to send: {post_dict_data}")
rc, resp = cli.post_json("/data", post_dict_data)

log.info(f"rc={rc} resp={resp}")
log.info(f"string json={resp.json()}")