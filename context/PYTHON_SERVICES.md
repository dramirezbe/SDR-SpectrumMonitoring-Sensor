# Python Services & Orchestration — Architecture Reference

## Overview

The Python control plane runs as a set of **systemd-managed services** on a Raspberry Pi 5. It coordinates all high-level sensor operations: fetching configurations from the remote API, driving the C RF engine over ZMQ IPC, uploading spectral data and status, managing cron-based campaign schedules, streaming demodulated audio via WebRTC, and retrying failed uploads from a persistent queue.

The architecture enforces a **global state machine** (`GlobalSys` in `functions.py`) with mutually exclusive states: only one of `IDLE`, `REALTIME`, `CAMPAIGN`, or `KALIBRATING` is active at any time. Deadlocks and concurrent acquisitions are prevented at the application level.

---

## Entrypoint & Configuration (`cfg.py`)

Central configuration module imported by every Python entrypoint.

### Environment

Loaded from `.env` via `python-dotenv`. All settings have sensible defaults:

| Variable | Default | Purpose |
|---|---|---|
| `API_URL` | `https://rsm.ane.gov.co:12443/api/sensor` | Base URL for REST API |
| `DEBUG` | `false` | Enables DEBUG console + file logging |
| `VERBOSE` | `false` | Enables INFO console logging (without DEBUG noise) |
| `DEVELOPMENT` | `false` | Uses `DUMMY_MAC` instead of hardware MAC |
| `IPC_ADDR` | `ipc:///tmp/rf_engine` | ZMQ socket address for C engine |
| `INTERVAL_REQUEST_REALTIME_S` | `5` | Polling interval for realtime config |
| `INTERVAL_REQUEST_CAMPAIGNS_S` | `60` | Polling interval for campaign sync |
| `INTERVAL_STATUS_S` | `30` | Reporting interval for system status |
| `INTERVAL_RETRY_QUEUE_S` | `300` | Retry queue processing interval |
| `LOG_FILES_NUM` | `10` | Maximum retained log files |
| `LOG_ROTATION_LINES` | `100` | Lines per log file before rotation |

Endpoints are appended to `API_URL`: `/data`, `/status`, `/campaigns`, `/realtime`, `/gps`.

### MAC Detection

`get_mac()` scans `/sys/class/net/` for physical interfaces, prioritizing `wlan*`, skipping virtual interfaces (lo, docker, veth, tun, etc.). Returns `DUMMY_MAC` in development mode.

### Timestamps

`get_time_ms()` returns the current Unix timestamp in milliseconds minus a fixed 5-hour offset (Colombia time, UTC−5). All timestamps sent to the API use this scheme.

### Logging Subsystem

- **`AtomicRotator`**: Append-only log writer using `atomic_write_bytes`. Creates timestamped files in `Logs/`. Rotates when line count exceeds `LOG_ROTATION_LINES`. Capped at `LOG_FILES_NUM` files — oldest are deleted.
- **`HandlerLevelFilter`**: Per-handler severity gating — console only shows WARNING+ by default (INFO if `VERBOSE`, DEBUG if `DEBUG`), file always stores INFO+ (DEBUG if enabled).
- **`set_logger()`**: Configures a module-level logger with dual handlers (console + file). Called at the top of every entrypoint.
- **`run_and_capture(func)`**: Universal entrypoint wrapper. Initializes the rotator and logger, then calls `func()` (sync or async). Catches `KeyboardInterrupt`, `SystemExit`, and unhandled exceptions. Returns exit code `0` or `1`.

---

## Utility Modules (`utils/`)

### I/O & Shared Memory (`io_util.py`)

**`atomic_write_bytes(path, data)`** — writes to a temp file in the target directory, `fsync()`s, then atomically replaces the target via `Path.replace()`. The only sanctioned file-writing mechanism in the project.

**`ShmStore`** — manages `/dev/shm/persistent.json` (tmpfs-based shared state) with `fcntl` file locking:

| Method | Behaviour |
|---|---|
| `add_to_persistent(key, value)` | Exclusive-locked read-modify-write for a single key |
| `consult_persistent(key)` | Shared-locked read of one key |
| `update_from_dict(dict)` | Merges a dict under exclusive lock |
| `clear_persistent()` | Writes `{}`, effectively wiping all state |

All reads use `LOCK_SH` (concurrent-safe), all writes use `LOCK_EX` (exclusive) with `fsync`. **Never** read or write this file directly — always go through `ShmStore`.

**`ElapsedTimer`** — simple non-blocking countdown timer. `init_count(seconds)` sets a deadline; `time_elapsed()` returns `True` when it passes. Used for periodic polling without `asyncio.sleep`.

---

### HTTP & ZMQ Communication (`request_util.py`)

**`ServerRealtimeConfig`** and **`FilterConfig`** — dataclasses with `__post_init__` validation that enforce hardware constraints: center frequency 1 MHz–6 GHz, sample rate 2–20 MSps, antenna port 1–4, PSD method `"pfb"` or `"welch"`, demodulation `"am"`, `"fm"`, or `None`.

**`RequestClient`** — thin wrapper around `requests` with a unified return-code convention:

| RC | Meaning |
|---|---|
| `0` | HTTP 2xx success |
| `1` | Network error (timeout, connection refused, DNS, 4xx/5xx) |
| `2` | Internal error (JSON serialization failure, unhandled exception) |

The client auto-prefixes endpoints with the sensor MAC (`/{MAC}/data`), validates the MAC format, and classifies connection errors by type for logging.

**`ZmqPairController`** — async context manager for the C engine IPC channel. Despite its name, it uses `zmq.REQ` (not PAIR), implementing a strict 1:1 request/reply cycle:

| Feature | Detail |
|---|---|
| Timeout | 15 s for both send and receive |
| Socket options | `LINGER=0`, `IMMEDIATE=1`, `SNDHWM=1`, `RCVHWM=1` — prevents queue buildup |
| Socket recycling | On any timeout or `ZMQError`, destroys and recreates the socket (`_reopen_socket`), resetting the REQ/REP state machine |
| IPC cleanup | Removes stale IPC socket files before binding, sets mode `0o777` |
| Guard | `_awaiting_reply` flag prevents sending a second request before the first reply arrives |

Usage: `async with ZmqPairController(addr) as zmq_ctrl: data = await zmq_ctrl.request(payload)`.

---

### System Status (`status_util.py`)

**`StatusDevice`** — reads Linux virtual filesystems for hardware metrics:

| Metric | Source | Retry Logic |
|---|---|---|
| CPU per-core | `/proc/stat` — two reads with 1 s interval, differential calculation | Up to 5 attempts with exponential backoff |
| RAM / Swap | `/proc/meminfo` — MemTotal/MemAvailable/SwapTotal/SwapFree | 3 attempts, 50 ms delay |
| Disk usage | `os.statvfs` — blocks × frsize | Single read |
| Temperature | `/sys/class/thermal/thermal_zone0/temp` | 3 attempts, 50 ms delay |
| Ping latency | `subprocess` → `ping -c 1 -W 1` | Single attempt, returns `-1` on failure |
| Logs | Most recent `.log` files in `Logs/`, last 50 KB of the latest | Filters out `payload` and `[[OK]]` lines to prevent log-recursion |

**`StatusPost`** — DTO that stores CPU loads as an internal list, flattened to `cpu_0`, `cpu_1`, … keys on serialization. Matches the API contract exactly.

`get_status_snapshot()` assembles the full payload dict and returns it through `StatusPost.from_dict().to_dict()` to guarantee schema compliance.

---

### DSP Primitives (`libs_DSP.py`)

Low-level NumPy-based functions shared by DC spike detection and removal:

- **`SignalProcessingUtils`** — moving average (edge-padded), robust MAD scale estimation (`1.4826 × median(|x − median|)`), first and second discrete differences
- **`WindowReconstructionUtils`** — `fit_local_polynomial_reconstruction()` (np.polyfit on support bins on both sides of the gap, optional Gaussian noise injection) and `fit_linear_reconstruction()` (straight-line interpolation between gap edges)

---

### DC Spike Detection (`dc_spike_detection.py`)

**`detect_dc_spike_region_by_symmetric_slope()`** — analyzes the PSD from the center (DC bin) outward on both sides:

1. Builds left/right symmetric profiles from the smoothed PSD
2. Computes first-derivative (slope) and second-derivative (curvature) profiles, smoothed
3. Estimates robust thresholds from the outer 65% of the analysis region (MAD-based)
4. Iterates outward from `min_half_width`, evaluating three termination criteria with consecutive-confirmation (default 4 consecutive bins):
   - **Noise floor**: both slopes near zero → flat baseline reached
   - **Emission/asymmetry**: slopes diverge asymmetrically → another signal starts
   - **Abrupt transition**: curvature spike → sharp transition at DC skirt edge

Returns `detected_half_width` and full debug info (thresholds, per-bin metrics, termination mode).

---

### DC Spike Removal (`dc_spike_removal.py`)

**`remove_dc_spike_adaptive_symmetric()`** — full pipeline:

1. **Detect** — calls the slope-based detector for initial half-width
2. **Expand** — if `enable_low_content_expansion` and the surrounding spectrum is deemed empty (see spectral content analysis), expands the removal window by `low_content_expand_factor` (default 3×)
3. **Reconstruct** — chooses strategy based on `termination_mode` of the detector:
   - `"noise_floor"` → linear interpolation between gap edges (adds shaped noise)
   - `"emission_or_asymmetry"` or `"abrupt_transition"` → local polynomial fit using support bins from both sides

Returns the filtered PSD array, repair window indices, and full debug metadata.

---

### Spectral Content Analysis (`spectral_content_analysis.py`)

**`detect_low_spectral_content_by_histogram_mean_median()`** — determines whether the spectrum around the DC region is effectively empty (noise floor) or contains real signals:

1. Extracts a centered fraction of the PSD, excluding a window around DC
2. Builds a histogram (Freedman-Diaconis bins, clamped to 24–96 bins) and computes histogram-based mean and median
3. **Stage 2** — evaluates proximity of mean to median (< 0.11 dB) and low high-tail fraction (< 2.5% exceeding `median + 2.5σ`)
4. **Stage 3 (conditional)** — if Stage 2 passes, analyzes lateral slopes in the expanded region beyond the DC window. If slopes are near-zero (flat), confirms low content

The combined result controls whether the DC removal window should be expanded and which reconstruction strategy is appropriate.

---

### Benchmarking (`benchmarking.py`)

Standalone profiling module (not used in production flows). Provides:
- **`SystemMonitor`** — captures CPU%, memory RSS, disk I/O counters, thread count, context switches via `psutil.Process`
- **`BenchmarkDecorator`** — wrap any function to measure execution time and resource deltas; saves results as JSON or CSV
- **`benchmark_context`** — context manager for profiling code blocks

---

## Shared Logic & State (`functions.py`)

### Global State Machine

**`SysState`** enum — `IDLE`, `CAMPAIGN`, `REALTIME`, `KALIBRATING`, `ERROR`.

**`GlobalSys`** class — class-level singleton enforcing mutual exclusion:
- `set(new_state)` — logs the transition if changing
- `is_idle()` — returns `True` only if `current == IDLE`
- All orchestration entry points check `is_idle()` before proceeding

### Campaign Scheduling

**`CronSchedulerCampaign`** — bridges the API campaign list to the system crontab:

- `sync_jobs(campaigns, current_time_ms, store)` — called on each poll cycle:
  1. Filters campaigns: must be in a valid status (not canceled/error/finished) and within its `[start − interval, end − interval]` time window
  2. **Clears ALL `CAMPAIGN_*` cron jobs** (atomic reset)
  3. If any valid candidates exist, selects the one with the highest `campaign_id`
  4. Writes all campaign parameters to `ShmStore` (center_freq, sample_rate, gains, etc.)
  5. Creates a single cron entry: `*/N * * * * systemd-cat -t CAMPAIGN_RUNNER {venv_python} -u campaign_runner.py`
  6. Returns `True` if a campaign is active

The cron period is derived from `acquisition_period_s` (minimum 1 minute). In development mode, writes to a mock crontab file instead of the system crontab.

### Upload Formatting

**`format_data_for_upload(payload, log)`** — builds the POST data dict with keys `Pxx`, `start_freq_hz`, `end_freq_hz`, `timestamp`, `mac`, plus optionally `excursion_hz` (FM) or `depth` (AM).

### Acquisition Strategy

**`AcquireDual`** — the class that bridges Python orchestration to the C engine. Despite its name implying dual acquisition (which was the original DC offset strategy), the current implementation uses a different approach:

- `_single_acquire(rf_params)` — sends a JSON config dict to the C engine via ZMQ, awaits the PSD reply, sleeps 50 ms for PLL settling
- `get_corrected_data(rf_params)` — acquires once, then applies the DC spike removal pipeline (`DCSpikeRemovalPipeline.remove_dc_spike_adaptive_symmetric`) to the returned PSD array. Attaches correction metadata (occupancy mode, detection metrics, reconstruction details) to the result
- `just_acquire(rf_params)` — raw acquisition without DC correction (rarely used)

Error handling: if the C engine returns `{"status": "error"}`, the error reason is stored in `last_error_reason` for the orchestrator to inspect (and trigger HackRF backoff if needed).

---

## Orchestrator (`orchestrator.py`)

The central event loop that runs indefinitely. It is the only service with **daemon** behavior (always-on, `Restart=always` in systemd).

### Process Management

Infrastructure for starting/stopping child processes (used for the WebRTC server):

- **`ManagedProc`** — holds subprocess handle, name, and log pump thread
- **`start_managed_process(name, argv, env, log)`** — cleans stale processes, creates a subprocess with `preexec_fn=os.setsid` (new session + process group), pipes stdout/stderr, starts a daemon thread that pumps output into the logger
- **`stop_managed_process(mp, log, timeout=2.0)`** — sends SIGTERM to the process group, waits `timeout` seconds, then SIGKILL if still alive
- **`cleanup_stale_processes(match_terms, log)`** — scans `/proc/*/cmdline` for matching processes, kills them with escalating force. Used on startup and during state transitions to prevent ghost instances

### Main Loop

`main()` runs an infinite `while True` loop:

1. If `IDLE` and the realtime timer (5 s) has elapsed → `run_realtime_logic()`
2. If `IDLE` and the campaign timer (60 s) has elapsed → `run_campaigns_logic()`
3. Sleep 100 ms

Only one branch executes per iteration, and both are gated on `GlobalSys.is_idle()`.

### Realtime Mode

`run_realtime_logic(client, store)`:

1. Fetch config from `GET /{MAC}/realtime` — validates the response; if invalid/empty/zero-center-freq, returns immediately
2. Set state to `REALTIME`
3. Open ZMQ controller
4. **Main acquisition loop** (runs until config becomes invalid or 300 s rotation timer fires):
   - If demodulation is requested and no WebRTC process is running, start `server_webrtc.py` as a managed child
   - If demodulation is not requested and WebRTC is running, stop it
   - Call `AcquireDual.get_corrected_data(next_config)` → receive PSD with metadata
   - `POST /{MAC}/data` with the formatted payload
   - If the C engine reports `hackrf_open_failed` or `hackrf_unavailable`, delay next GET by 5 s
   - Fetch new config; if empty, break the loop
5. In `finally`: stop WebRTC, clean stale processes, revert to `IDLE`

### Campaign Mode

`run_campaigns_logic(client, store, scheduler)`:

1. `GET /{MAC}/campaigns` — fetches the campaign list
2. Calls `scheduler.sync_jobs()` — if a campaign is active in-window, writes params to ShmStore and creates the cron entry
3. **If active**: optionally runs calibration (`_perform_calibration_sequence` — sends `{"calibrate": true}` to the C engine, waits for PPM result), then sets state to `CAMPAIGN`
4. Blocks in a loop sleeping `INTERVAL_REQUEST_CAMPAIGNS_S` (60 s) between re-checks, re-syncing cron each cycle
5. When `sync_jobs` returns `False` (window closed), exits campaign mode
6. In `finally`: reverts to `IDLE`

The orchestrator itself does not execute campaign acquisitions — those are triggered by cron calling `campaign_runner.py`.

### Calibration Sequence

`_perform_calibration_sequence()`:
1. Sets state to `KALIBRATING`
2. Sends `{"calibrate": true}` to the C engine over ZMQ (up to 15 s timeout)
3. On success, the C engine internally runs the 3-stage calibration (wideband sweep → FM pilot detection → fine symmetry search) and persists `ppm_error` to ShmStore
4. Restores previous state, or `IDLE` if it was `KALIBRATING`

---

## Campaign Runner (`campaign_runner.py`)

A **one-shot** script invoked by cron. Runs a single acquisition cycle and exits.

**`CampaignRunner`** class:

- **Guard**: reads `campaign_runner_running` from ShmStore — if already `True`, exits immediately to prevent overlapping executions
- **Expiration check**: reads `campaign_id` and `expires_at_ms` from ShmStore; if current time exceeds the deadline, exits without touching hardware
- **RF params**: reads all campaign configuration keys from ShmStore (`center_freq_hz`, `sample_rate_hz`, `rbw_hz`, etc.) and normalizes types/values
- **Acquisition**: opens ZMQ, calls `AcquireDual.get_corrected_data(rf_cfg)`, sets `campaign_runner_running = False` in `finally`
- **Upload**: `POST /{MAC}/data` with additional `campaign_id` field
- **Post-processing**:
  - If upload fails → save payload as `{timestamp}.json` to `Queue/` (capped at 50 files)
  - If upload succeeds → update `delta_t_ms` in ShmStore, save to `Historic/`
  - **Disk management**: if disk usage > 80%, deletes the 10 oldest Historic files; only saves to Historic if usage < 90%
- Disk usage calculated from `StatusDevice.get_disk() / get_total_disk()`

---

## Status Reporter (`status.py`)

Triggered by a systemd **timer** (oneshot service, 30 s interval).

`main()`:
1. Queries ShmStore for `delta_t_ms`, `last_kal_ms`
2. Reads NTP sync time from `/var/lib/systemd/timesync/clock` mtime (adjusted to Colombia time)
3. Calls `StatusDevice.get_status_snapshot()` for hardware metrics
4. `POST /{MAC}/status` — retries up to 10 times with 0.5 s delay

The service logs payload contents at DEBUG level and uses `run_and_capture` for error containment.

---

## Retry Queue (`retry_queue.py`)

Triggered by a systemd **timer** (oneshot service, 300 s interval).

**`retry_queue(cli)`**:
1. Lists all `.json` files in `Queue/`, sorted by `st_mtime` (oldest first)
2. For each file: reads and validates JSON, then attempts to POST to `/data`
3. **Up to 2 attempts per file**, 5 s between retries
4. Response classification:
   - `2xx` → success, delete file, continue to next
   - `4xx` → permanent client error (invalid data), delete file
   - `5xx` / network error → transient, retry
   - Corrupt JSON / not a dict → delete file
5. **Halts processing** on the first file that exhausts all retries — preserves chronological ordering

---

## WebRTC Audio Server (`server_webrtc.py`)

A standalone async process that bridges the C engine's Opus TCP stream into a browser-compatible WebRTC peer connection. Managed by the orchestrator as a child process.

### Architecture

Two concurrent tasks running in the same asyncio event loop:

**1. WebSocket Signaling** (`run_signaling_session()`):
- Connects to `wss://rsm.ane.gov.co:12443/ws/signal/{SENSOR_ID}`
- Registers as role `"sensor"`
- Instantiates the GStreamer `Publisher`, which builds a pipeline: `appsrc → opusparse → rtpopuspay → webrtcbin`
- Handles SDP negotiation: creates offer → sends over WS → receives answer → sets remote description
- Exchanges ICE candidates bidirectionally
- Runs indefinitely; on connection failure, the outer loop retries after 5 s

**2. TCP Server** (`tcp_reader_task()`):
- Listens on `0.0.0.0:9000` (SO_REUSEADDR + SO_REUSEPORT for fast rebinding)
- Accepts one connection at a time from the C engine (`rf_app`)
- Reads frames with custom binary header: `struct("!IIIHH")` — magic (0x4F505530), sequence number, sample rate, channels, payload length
- Pushes Opus payload bytes into the GStreamer pipeline via `Publisher.push_opus_frame()`

### GStreamer Pipeline

```
appsrc (audio/x-opus, rate=48000, channels=1)
  → queue
  → opusparse
  → rtpopuspay (pt=96)
  → queue
  → webrtcbin (stun-server, bundle-policy=max-bundle)
```

The GLib main loop runs in a daemon thread. `push_opus_frame()` schedules buffer injection via `GLib.idle_add()`, which is thread-safe. PTS is tracked manually with 20 ms frame duration.

### Shutdown

SIGINT/SIGTERM set the global `shutdown_event`. This unblocks the TCP server and the WS retry loop. The cleanup sequence:
1. Set shutdown event
2. Stop publisher (set GStreamer pipeline to NULL, quit GLib loop)
3. Cancel all asyncio tasks
4. Gather tasks with `return_exceptions=True` to absorb cancellation errors

---

## System Initialization (`init_sys.py`)

Provisioning script run once at install time (and on major resets).

`main()`:
1. Creates directories: `Queue/`, `Logs/`, `Historic/`, `daemons/`
2. Removes all cron jobs whose comment starts with `CAMPAIGN_`
3. Clears ShmStore (writes `{}`)
4. Generates 7 systemd unit files into `daemons/`:

| File | Type | Description |
|---|---|---|
| `rf-ane2.service` | Service | C RF engine, `Restart=always`, flock-guarded |
| `ltegps-ane2.service` | Service | C LTE/GPS engine, `Restart=always` |
| `orchestrator-ane2.service` | Service | Python orchestrator, `Restart=always`, pre-checks internet via ping |
| `status-ane2.service` | Oneshot | Status reporter, invoked by timer |
| `status-ane2.timer` | Timer | Fires `OnUnitInactiveSec=INTERVAL_STATUS_S` (30 s) + 1 min after boot |
| `retry-queue-ane2.service` | Oneshot | Queue processor, invoked by timer |
| `retry-queue-ane2.timer` | Timer | Fires `OnUnitInactiveSec=INTERVAL_RETRY_QUEUE_S` (300 s) + 1 min after boot |

All services run as user `anepi` with working directory set to the project root. Binary services use `/usr/bin/flock -n` for singleton enforcement. `ExecStartPre` pings Google to delay start until network is available.

---

## Deployment (`build.sh` & `install.sh`)

### Build (`build.sh`)

CMake wrapper producing C binaries:

| Mode | Command | Targets | Flags |
|---|---|---|---|
| Production | `./build.sh` | `rf_app`, `ltegps_app` | No special flags (gpiod included) |
| Development | `./build.sh -dev` | `rf_app` only | `-DBUILD_STANDALONE=ON` (stubs gpiod) |

Creates a temporary `build/` directory, runs cmake + make, moves resulting binaries to the project root, then removes the build directory.

### Install (`install.sh`)

Root-only production deployment script (7 steps):

| Step | Actions |
|---|---|
| **1. Stop services** | Stops all active `-ane2.service` units except `ltegps-ane2` |
| **2. Dependencies** | `apt-get install` for ZMQ, cJSON, libcurl, libusb, fftw3, GStreamer stack, libopus, Python3, build tools. Refreshes CA certificates. Enables NTP |
| **3. Hardware libs** | Conditionally compiles libgpiod v2 and kalibrate-hackrf from source if not detected |
| **4. Python env** | Creates `venv` with `--system-site-packages`, installs pip + certifi + `requirements.txt`, runs `build.sh` |
| **5. Shared memory** | Initializes `/dev/shm/persistent.json` as `{}` with permissions `666`, owned by `anepi` |
| **6. Systemd** | Runs `init_sys.py`, copies daemon files to `/etc/systemd/system/`, `systemctl enable` + `daemon-reload` |
| **7. Reboot** | Triggers an immediate reboot to start all services fresh |

Temporary files and lock files in `/tmp` are cleaned. The `/tmp` directory itself is chmodded `1777`.

---

## JSON Contracts (`json/`)

Reference schemas for API and IPC communication. Files with `.jsonc` extension contain comments and are documentation-only.

### API Endpoints

| File | Method | Direction | Key fields |
|---|---|---|---|
| `GET-realtime.jsonc` | GET response | Server → Sensor | `center_freq_hz`, `sample_rate_hz`, `rbw_hz`, `window`, `overlap`, `lna_gain`, `vga_gain`, `antenna_amp`, `antenna_port`, `cooldown_request`, `demodulation` (fm/am/null), `filter` (start/end Hz or null) |
| `GET-campaigns.jsonc` | GET response | Server → Sensor | Array of campaigns, each with `campaign_id`, `status`, all RF params, `acquisition_period_s`, `timeframe` (start/end ms), optional `filter` |
| `POST-data.jsonc` | POST request | Sensor → Server | `mac`, optional `campaign_id`, `Pxx` array (dBm), `start_freq_hz`, `end_freq_hz`, `timestamp`, optional `excursion_hz` (FM) or `depth` (AM) |
| `POST-status.jsonc` | POST request | Sensor → Server | `mac`, `cpu_0`..`cpu_3`, `ram_mb`, `swap_mb`, `disk_mb`, `temp_c`, totals for ram/swap/disk, `delta_t_ms`, `ping_ms`, `timestamp_ms`, `last_kal_ms`, `last_ntp_ms`, `logs` |
| `POST-gps.jsonc` | POST request | Sensor → Server | `mac`, `lat`, `lng`, `alt`, `timestamp` |

### IPC

| File | Purpose |
|---|---|
| `json/rf-engine/params.jsonc` | ZMQ request from Python to C engine — same structure as realtime config plus `calibrate` flag and `method_psd` |
| `json/shmstore.jsonc` | Documented schema for `/dev/shm/persistent.json` — all keys used across the system: campaign params, calibration `ppm_error`, GPS coordinates, `campaign_runner_running` lock flag, `delta_t_ms`, `last_kal_ms`, `changed_gps`, `legal_freqs` cache |

---

## Data Flow Summary

### Realtime Acquisition

```
orchestrator.py → GET /realtime → ServerRealtimeConfig
  → ZmqPairController.request(cfg) → rf_app (C) → Welch/PFB PSD + IQ compensation
  → AcquireDual.get_corrected_data() → DC spike removal → PSD + metadata
  → format_data_for_upload() → POST /data

Parallel: if demodulation enabled:
  rf_app audio thread → TCP :9000 (Opus frames)
  server_webrtc.py → GStreamer pipeline → WebRTC → browser
```

### Campaign Acquisition

```
orchestrator.py → GET /campaigns → CronSchedulerCampaign.sync_jobs()
  → writes params to ShmStore + creates cron entry

cron fires → campaign_runner.py
  → reads params from ShmStore
  → ZmqPairController.request(cfg) → rf_app (C)
  → AcquireDual.get_corrected_data() → DC spike removal
  → POST /data (with campaign_id)
  → on failure → save to Queue/
  → on success → save to Historic/
```

### Status & Retry Loop

```
status-ane2.timer (30s) → status.py
  → ShmStore (delta_t_ms, last_kal_ms)
  → /proc/* + thermal_zone (CPU, RAM, disk, temp)
  → ping (latency)
  → POST /status (up to 10 retries)

retry-queue-ane2.timer (300s) → retry_queue.py
  → read Queue/*.json (oldest first)
  → POST /data per file (up to 2 retries)
  → delete on success or permanent error
  → halt on first transient exhaustion
```

---

## Key Design Patterns

| Pattern | Application |
|---|---|
| **Global state machine** | `GlobalSys` prevents concurrent acquisitions — only one of IDLE/REALTIME/CAMPAIGN/KALIBRATING active |
| **Atomic file writes** | `atomic_write_bytes` (temp + fsync + replace) for all log and data persistence |
| **fcntl-locked shared memory** | `ShmStore` uses shared locks for reads, exclusive for writes, always with fsync |
| **Socket recycling** | `ZmqPairController` destroys and recreates the REQ socket on any timeout or error, resetting the ZMQ state machine |
| **Escalating process kill** | `stop_managed_process` — SIGTERM → 2 s wait → SIGKILL via process group |
| **Cancelable operations** | `sleep_cancelable_ms` (50 ms polling slices) and `ensure_tx_with_retry` (checking volatile flag) for responsive shutdown |
| **Cron + timer hybrid** | Campaigns use cron for flexible scheduling; status/retry use systemd timers for fixed intervals |
| **Guard flags** | `campaign_runner_running` in ShmStore prevents overlapping campaign runner instances |
| **Disk-aware storage** | Campaign runner checks disk usage before saving and auto-deletes oldest Historic files above 80% usage |
| **Log-level gating** | Per-handler severity filters: console only shows WARNING+ by default, file always stores INFO+ |
