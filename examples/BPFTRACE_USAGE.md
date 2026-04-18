# BPFTRACE BPF Benchmark Usage Guide

## Overview

This script (`bpftrace-execute_pfb_psd.py`) profiles the performance of the `execute_pfb_psd()` function in the SDR RF engine using **eBPF (Extended Berkeley Packet Filters)** via BCC (BPF Compiler Collection).

### What is eBPF Benchmarking?

eBPF allows attaching probes to kernel and userspace functions without recompilation. This tool uses:
- **uprobes** to hook function entry/exit points
- **Hardware performance counters** to measure:
  - **Instructions**: CPU instructions executed
  - **Cache Misses**: L1/L2/L3 cache misses
  - **Duration**: Wall-clock execution time
  - **IPS**: Instructions Per Second (calculated)
- **System metrics**: CPU frequency, temperature, thermal throttling state

### CSV Output Columns

| Column | Type | Unit | Description | Normal RPi5 Range |
|--------|------|------|-------------|-------------------|
| `timestamp_ms` | u64 | milliseconds | Epoch timestamp | - |
| `pid` | u32 | - | Process ID of rf_app | - |
| `tid` | u32 | - | Thread ID | - |
| `function` | string | - | Function name (execute_pfb_psd) | - |
| `duration_ms` | float | milliseconds | Execution time | 10-50 ms |
| `instructions` | u64 | count | CPU instructions executed | 500k-5M |
| `cache_misses` | u64 | count | Total cache misses | 1k-50k |
| `cpu_mhz` | float | MHz | Current CPU frequency | 2400-3000 |
| `temp_celsius` | float | °C | CPU temperature | 40-65 |
| `throttle_state` | hex string | - | Throttling flags | 0x0 (normal) |
| `ips` | float | instructions/sec | Instructions Per Second | 10M-100M IPS |

---

## Finding the rf_app Process

Before running the benchmark, you need to find the PID of the running `rf_app` process.

### Method 1: Using `pgrep` (Simplest)
```bash
pgrep -f rf_app
```
Output:
```
1234
```

### Method 2: Using `ps aux`
```bash
ps aux | grep rf_app | grep -v grep
```

### Method 3: Using Environment Variable
The script reads `RF_APP_PID` environment variable:
```bash
export RF_APP_PID=$(pgrep -f rf_app)
echo $RF_APP_PID
```

### Method 4: Using Script `-p` Flag (Automatic)
```bash
./bpftrace-execute_pfb_psd.py -p $(pgrep -f rf_app)
```

---

## Installation & Prerequisites

### System Requirements
- **Kernel**: Linux 4.8+ (for eBPF support)
- **RPi5**: Verified working on Raspberry Pi 5 with Ubuntu 24.04
- **Privileges**: Must run with `sudo` (kernel probe attachment)

### Install Dependencies

**Ubuntu/Debian:**
```bash
sudo apt install bpfcc-tools python3-bpfcc linux-headers-$(uname -r)
```

**With Virtual Environment (Recommended):**
```bash
# From project root
source venv/bin/activate
pip install pyyaml psutil

# Verify bcc is installed globally
python3 -c "from bcc import BPF; print('BPF ready')"
```

---

## Basic Usage

### Default Benchmark (All Processes, 60s Duration)
```bash
sudo -E ./examples/bpftrace-execute_pfb_psd.py
```

### Target Specific rf_app Process
```bash
sudo -E ./examples/bpftrace-execute_pfb_psd.py \
  -p $(pgrep -f rf_app) \
  -r benchmarks/rf_app_metrics.csv \
  --duration 120
```

### Custom Binary and Function
```bash
sudo -E ./examples/bpftrace-execute_pfb_psd.py \
  -b /path/to/custom_binary \
  -f my_function_name \
  -p 5678 \
  --output-csv results/benchmark.csv \
  --duration 30
```

---

## Advanced Usage

### Full Flag Reference
```
-b, --binary BINARY_PATH
    Path to the ELF binary to probe (default: rf_app from PROJECT_ROOT)
    
-f, --function FUNCTION_NAME
    Name of the symbol/function to trace (default: execute_pfb_psd)
    
-p, --pid TARGET_PID
    Target process ID. Use -1 or omit for all processes
    (reads RF_APP_PID env var, default: -1)
    
-r, --output-csv CSV_PATH
    Output CSV file path for collected metrics
    (default: benchmarks/bpftrace_execute_pfb_psd.csv)
    
--duration SECONDS
    Duration of the benchmark experiment in seconds (default: 60)
```

### Scenario 1: Monitor rf_app for 5 Minutes
```bash
RF_APP_PID=$(pgrep -f rf_app) && \
sudo -E ./examples/bpftrace-execute_pfb_psd.py \
  -p $RF_APP_PID \
  --duration 300 \
  -r benchmarks/long_run.csv
```

### Scenario 2: Profile Under SDR Load
```bash
# In terminal 1: Start SDR workload
./orchestrator.py --live

# In terminal 2: Run benchmark (find rf_app PID automatically)
sudo -E ./examples/bpftrace-execute_pfb_psd.py \
  -p $(pgrep -f rf_app) \
  --duration 120
```

### Scenario 3: Multiple Functions
```bash
# Benchmark a different function in the same binary
sudo -E ./examples/bpftrace-execute_pfb_psd.py \
  -f fft_compute \
  -r benchmarks/fft_metrics.csv
```

---

## Understanding throttle_state (RPi5 Undervoltage Detection)

The `throttle_state` field is a **hexadecimal bitmask** indicating thermal and power issues.

### Throttle State Bit Flags
```
Hex Flag | Decimal | Meaning
---------+---------+-------------------------------------------
0x0      | 0       | Normal operation (no throttling)
0x1      | 1       | UNDERVOLTAGE detected (critical!)
0x2      | 2       | ARM frequency capped
0x4      | 4       | Currently throttled
0x8      | 8       | Soft temp limit active
0x10     | 16      | Currently in soft limit
0x20     | 32      | Soft temp limit has been reached
0x40     | 64      | Hard temp limit active
0x80     | 128     | Currently in hard limit
```

### Interpreting Values

| Value | Status | Action |
|-------|--------|--------|
| `0x0` | ✅ Normal | No issues detected |
| `0x1` | ⚠️ **CRITICAL** | **Undervoltage!** Check PSU, voltage regulator, USB cable |
| `0x2` - `0x8` | ⚠️ Warning | Thermal throttling active (heat management) |
| `0x80` | ❌ Hard Throttle | Temperature too high, performance severely limited |

### Example Parsing
```python
throttle = "0x5"  # From CSV
flags = int(throttle, 16)

if flags & 0x1:
    print("UNDERVOLTAGE DETECTED!")
if flags & 0x4:
    print("Currently throttled")
if flags & 0x80:
    print("Hard temperature limit reached")
```

---

## CSV Analysis Example

### Python Script to Detect Issues

```python
#!/usr/bin/env python3
import csv
import sys
from pathlib import Path

def analyze_benchmark(csv_file):
    """Analyze BPF benchmark CSV for anomalies."""
    df = []
    
    with open(csv_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            df.append(row)
    
    if not df:
        print("No data in CSV")
        return
    
    print(f"Analyzed {len(df)} events")
    print()
    
    # Detect undervoltage
    throttles = [int(row['throttle_state'], 16) for row in df]
    undervolts = [i for i, t in enumerate(throttles) if t & 0x1]
    
    if undervolts:
        print(f"⚠️  UNDERVOLTAGE detected in {len(undervolts)} events!")
        print(f"   First occurrence at event #{undervolts[0]}")
    else:
        print("✅ No undervoltage detected")
    
    # Analyze performance
    durations = [float(row['duration_ms']) for row in df]
    ips_vals = [float(row['ips']) for row in df]
    temps = [float(row['temp_celsius']) for row in df]
    
    print(f"\nExecution Time:")
    print(f"  Min: {min(durations):.2f} ms")
    print(f"  Max: {max(durations):.2f} ms")
    print(f"  Avg: {sum(durations)/len(durations):.2f} ms")
    
    print(f"\nInstructions Per Second (IPS):")
    print(f"  Min: {min(ips_vals)/1e6:.1f}M IPS")
    print(f"  Max: {max(ips_vals)/1e6:.1f}M IPS")
    print(f"  Avg: {sum(ips_vals)/len(ips_vals)/1e6:.1f}M IPS")
    
    print(f"\nTemperature:")
    print(f"  Min: {min(temps):.1f}°C")
    print(f"  Max: {max(temps):.1f}°C")
    print(f"  Avg: {sum(temps)/len(temps):.1f}°C")
    
    # Detect thermal throttling
    throttled = sum(1 for t in throttles if t & 0x4)
    if throttled:
        print(f"\n⚠️  Thermal throttling in {throttled}/{len(throttles)} events ({100*throttled/len(throttles):.1f}%)")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 analyze_benchmark.py <csv_file>")
        sys.exit(1)
    analyze_benchmark(sys.argv[1])
```

**Usage:**
```bash
python3 analyze_benchmark.py benchmarks/bpftrace_execute_pfb_psd.csv
```

**Output Example:**
```
Analyzed 245 events

✅ No undervoltage detected
Execution Time:
  Min: 12.34 ms
  Max: 45.67 ms
  Avg: 28.50 ms

Instructions Per Second (IPS):
  Min: 15.2M IPS
  Max: 82.5M IPS
  Avg: 42.1M IPS

Temperature:
  Min: 42.3°C
  Max: 58.9°C
  Avg: 51.2°C

⚠️  Thermal throttling in 12/245 events (4.9%)
```

---

## Troubleshooting

### Error: "No se pudo importar bcc"
**Problem**: BCC Python bindings not installed
**Solution**:
```bash
# System-wide installation
sudo apt install python3-bpfcc

# Or from venv with distro-package access
source venv/bin/activate
# The script will search /usr/lib/python*
```

### Error: "Permission denied" attaching uprobes
**Problem**: Must run with sudo
**Solution**: Always use `sudo -E`:
```bash
sudo -E ./examples/bpftrace-execute_pfb_psd.py -p $(pgrep -f rf_app)
```

### Error: "Function symbol not found in binary"
**Problem**: The function doesn't exist or binary isn't stripped
**Solution**:
```bash
# List available symbols
nm rf_app | grep execute_pfb_psd

# Verify binary path
file rf_app
```

### CSV File Not Created
**Problem**: Binary path or function name wrong, or insufficient permissions
**Solution**:
```bash
# Check output directory exists
mkdir -p benchmarks

# Verify binary is executable
ls -la rf_app

# Run with verbose output
sudo -E ./examples/bpftrace-execute_pfb_psd.py -p 1234 -r benchmarks/test.csv
```

### No Events Captured
**Problem**: Process finished before benchmark started, or probes failed
**Solution**:
```bash
# Ensure rf_app is running long enough
# Use longer duration
sudo -E ./examples/bpftrace-execute_pfb_psd.py \
  -p $(pgrep -f rf_app) \
  --duration 300  # 5 minutes

# Check system logs for BPF errors
sudo dmesg | tail -20
```

### Kernel Too Old
**Problem**: "Function not found" with kernel < 4.8
**Solution**:
- Upgrade kernel to 5.x or newer
- Or use timing-only fallback (automatically used if perf counters unavailable)

---

## Integration with Continuous Monitoring

### Automated Daily Benchmark
```bash
#!/bin/bash
# benchmarks/daily_bench.sh
set -e

RF_APP_PID=$(pgrep -f rf_app || echo "")
if [ -z "$RF_APP_PID" ]; then
    echo "rf_app not running"
    exit 1
fi

DATE=$(date +"%Y%m%d_%H%M%S")
CSV="benchmarks/daily_${DATE}.csv"

echo "Benchmarking PID $RF_APP_PID..."
sudo -E ./examples/bpftrace-execute_pfb_psd.py \
    -p $RF_APP_PID \
    -r "$CSV" \
    --duration 600  # 10 minutes

echo "Results: $CSV"
python3 analyze_benchmark.py "$CSV"
```

**Add to crontab:**
```bash
crontab -e
# Add: 0 2 * * * cd /home/javastral/GIT/SDR-SpectrumMonitoring-Sensor && bash benchmarks/daily_bench.sh >> benchmarks/cron.log 2>&1
```

---

## Performance Expectations (RPi5)

### Healthy System
- **Duration**: 10-50 ms per call
- **IPS**: 20-100M instructions/sec
- **CPU Freq**: 2400-3000 MHz
- **Temp**: 40-65°C
- **Throttle**: 0x0 (normal)
- **Cache Misses**: 1k-50k per call

### Degraded Performance
- **Signs**: IPS drops, duration increases, temp > 70°C
- **Cause**: Usually thermal throttling or power supply issue
- **Fix**: Improve cooling, check PSU voltage (5V ±5%)

### Critical Undervoltage
- **Signs**: throttle_state with bit 0x1 set
- **Cause**: Weak power supply or bad USB cable
- **Fix**: Use official RPi5 PSU (27W recommended) and high-quality USB-C cable

---

## See Also
- [BCC Documentation](https://github.com/iovisor/bcc/blob/master/docs/reference_guide.md)
- [eBPF Overview](https://ebpf.io/)
- [RPi5 Thermal Management](https://www.raspberrypi.com/documentation/computers/raspberry-pi-5/)
