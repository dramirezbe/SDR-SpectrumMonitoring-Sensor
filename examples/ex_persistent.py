#!/usr/bin/env python3
"""
@file init_store_params.py
@brief Seeds the /dev/shm/persistent.json file with default RF parameters
       so that campaign_runner.py has data to read.
"""
import os
import json
import fcntl
import sys

# --- 1. The ShmStore Class (Copied for standalone execution) ---
class ShmStore:
    def __init__(self, filename="persistent.json"):
        """
        Initialize the storage in /dev/shm (RAM).
        Creates the file with an empty JSON object {} if it doesn't exist.
        """
        self.filepath = os.path.join("/dev/shm", filename)
        
        # Initialize file if it's missing (e.g., first run after boot)
        if not os.path.exists(self.filepath):
            self._write_file({})

    def _read_file(self):
        """Internal: Safely reads the JSON with a shared lock."""
        if not os.path.exists(self.filepath):
            return {}
            
        try:
            with open(self.filepath, 'r') as f:
                # Wait for permission to read (prevents reading during a write)
                fcntl.flock(f, fcntl.LOCK_SH) 
                try:
                    return json.load(f)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except (json.JSONDecodeError, IOError):
            return {}

    def _write_file(self, data):
        """Internal: Safely writes the JSON with an exclusive lock."""
        # Open in write mode ('w') which truncates, but we lock immediately
        with open(self.filepath, 'w') as f:
            fcntl.flock(f, fcntl.LOCK_EX) # Block others from reading/writing
            try:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno()) # Force write to RAM immediately
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def add_to_persistent(self, key, value):
        """
        Updates a specific key while keeping the rest of the data intact.
        """
        current_data = self._read_file()
        current_data[key] = value
        self._write_file(current_data)

    def consult_persistent(self, key):
        """
        Returns the value for the key, or None if key not found.
        """
        current_data = self._read_file()
        return current_data.get(key, None)

# --- 2. Configuration Data (The keys campaign_runner expects) ---
DEFAULT_RF_PARAMS = {
    "center_freq_hz": 915000000,  # 915 MHz
    "span": 20000000,             # 20 MHz
    "sample_rate_hz": 20000000,   # 20 Msps
    "rbw_hz": 10000,              # 10 kHz RBW
    "overlap": 0.5,               # 50% overlap
    "window": "hann",             # Hanning window
    "scale": 1.0,
    "lna_gain": 8,                # Low Noise Amp gain
    "vga_gain": 20,               # Variable Gain Amp
    "antenna_amp": 1,             # 1 = ON, 0 = OFF
    "antenna_port": "A",
    "ppm_error": 0
}

# --- 3. Main Execution ---
def main():
    print("--- Initializing Shared Memory Store ---")
    store = ShmStore()
    
    print(f"Target File: {store.filepath}")
    
    # Write values one by one (simulating independent updates)
    # or you could just write the whole dict if the class supported it.
    # We use the class method add_to_persistent to test the logic.
    for key, value in DEFAULT_RF_PARAMS.items():
        print(f"Writing {key} -> {value}...")
        store.add_to_persistent(key, value)
    
    print("\n--- Verification ---")
    # Read back to ensure integrity
    with open(store.filepath, 'r') as f:
        content = f.read()
        print("Raw File Content:")
        print(content)
        
    print("\n--- Test 'consult_persistent' ---")
    # Test the read method used by campaign_runner
    check_val = store.consult_persistent("center_freq_hz")
    if check_val == 915000000:
        print("SUCCESS: center_freq_hz read back correctly.")
    else:
        print(f"FAILURE: Expected 915000000, got {check_val}")

if __name__ == "__main__":
    main()