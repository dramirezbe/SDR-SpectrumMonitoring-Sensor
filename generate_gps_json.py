import cfg
import json
import time
import random
import sys
from pathlib import Path

# Initialize Logger
log = cfg.set_logger()

# Constants
DIR_GPS_JSONS = cfg.PROJECT_ROOT / "gps_jsons"
SAMPLE_COUNT = 25
SAMPLE_INTERVAL_SEC = 1

# Initial Dummy Coordinates (Approx. Manizales, Colombia)
# You can change these to whatever starting point you prefer
START_LAT = 5.06889
START_LNG = -75.51738
START_ALT = 2160.0

def ensure_output_dir():
    """Ensures the output directory exists to prevent IOErrors."""
    DIR_GPS_JSONS.mkdir(parents=True, exist_ok=True)

def get_next_gps_position(current_lat, current_lng, current_alt):
    """
    Generates the next GPS position by applying small random drifts 
    to the current position to simulate realistic movement.
    """
    # Simulate drift:
    # ~0.00001 degrees is roughly 1 meter
    lat_drift = random.uniform(-0.00002, 0.00002) 
    lng_drift = random.uniform(-0.00002, 0.00002)
    alt_drift = random.uniform(-0.5, 0.5)

    new_lat = current_lat + lat_drift
    new_lng = current_lng + lng_drift
    new_alt = current_alt + alt_drift

    data_dict = {
        "mac": cfg.get_mac(),
        "lat": round(new_lat, 6),
        "lng": round(new_lng, 6),
        "alt": round(new_alt, 2),
        "timestamp": cfg.get_time_ms()
    }
    
    return data_dict, new_lat, new_lng, new_alt

def save_json(data: dict, filename: Path) -> int:
    """Saves dictionary data to a JSON file."""
    try:
        with filename.open('w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        return 0
    except IOError as e:
        log.error(f"Failed to write to {filename}: {e}")
        return 1

def main() -> int:
    ensure_output_dir()
    
    # Initialize state variables
    current_lat = START_LAT
    current_lng = START_LNG
    current_alt = START_ALT

    log.info(f"Starting GPS simulation: {SAMPLE_COUNT} samples.")

    for i in range(SAMPLE_COUNT):
        # 1. Generate Data (updates position for next loop)
        gps_dict, current_lat, current_lng, current_alt = get_next_gps_position(
            current_lat, current_lng, current_alt
        )
        
        # 2. Define Filename
        timestamp = gps_dict["timestamp"]
        filename = DIR_GPS_JSONS / f"{timestamp}.json"
        
        # 3. Save Data
        err = save_json(gps_dict, filename)
        if err:
            return err
            
        log.info(f"[{i+1}/{SAMPLE_COUNT}] Saved {filename.name} | Lat: {gps_dict['lat']} Lng: {gps_dict['lng']}")
        
        time.sleep(SAMPLE_INTERVAL_SEC)
        
    return 0

if __name__ == "__main__":
    # Compatible with your existing cfg wrapper
    if hasattr(cfg, 'run_and_capture'):
        rc = cfg.run_and_capture(main, cfg.LOG_FILES_NUM)
        sys.exit(rc)
    else:
        sys.exit(main())