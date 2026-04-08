import time
import os
import csv
import psutil
import subprocess
import threading
from datetime import datetime
from typing import Any, cast

class BenchmarkCSV:
    def start(self, folder_path, csv_name, duration, interval=0.5):
        os.makedirs(folder_path, exist_ok=True)
        
        # Generar timestamp humano y concatenarlo al nombre
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_csv_name = f"{ts}_{csv_name}"
        
        self.file = open(os.path.join(folder_path, final_csv_name), 'w', newline='')
        self.writer = csv.writer(self.file)

        num_cores = os.cpu_count()
        headers = ["Time_Human", "Time_Unix_ms", "CPU_Load_1m", "CPU_Freq_MHz"] + [
            f"Core_{i}_%" for i in range(num_cores)
        ] + ["RAM_Used_%", "Swap_Used_%", "Disk_Write_MBps", "Temp_C", "Throttled"]
        self.writer.writerow(headers)

        self.duration = duration
        self.interval = interval
        self.running = True
        self.last_disk_write = cast(Any, psutil.disk_io_counters()).write_bytes
        self.last_time = time.time()

        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def _run_loop(self):
        end_t = time.time() + self.duration
        while self.running and time.time() < end_t:
            start_loop = time.time()
            self.save_data()
            
            elapsed = time.time() - start_loop
            wait = self.interval - elapsed
            if wait > 0:
                time.sleep(wait)
        self.stop()

    def save_data(self):
        now = time.time()
        load = psutil.getloadavg()[0]
        
        try: freq = int(psutil.cpu_freq().current)
        except: freq = 0
            
        cores = psutil.cpu_percent(percpu=True)
        ram = psutil.virtual_memory().percent
        swap = psutil.swap_memory().percent

        d_write = cast(Any, psutil.disk_io_counters()).write_bytes
        t_diff = now - self.last_time
        mbps = ((d_write - self.last_disk_write) / t_diff) / (1024 * 1024) if t_diff > 0 else 0
        self.last_disk_write, self.last_time = d_write, now

        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temp = round(int(f.read()) / 1000, 1)
        except: temp = "N/A"
            
        try:
            throttled = subprocess.check_output(
                ['vcgencmd', 'get_throttled'], text=True, stderr=subprocess.DEVNULL
            ).strip().split('=')[1]
        except: throttled = "N/A"

        self.writer.writerow([
            datetime.fromtimestamp(now).strftime("%H:%M:%S.%f")[:-3],
            int(now * 1000), round(load, 2), freq
        ] + cores + [ram, swap, round(mbps, 3), temp, throttled])
        
        self.file.flush()
        os.fsync(self.file.fileno()) # <-- Esto asegura la escritura física en disco

    def stop(self):
        self.running = False
        if not self.file.closed:
            self.file.close()