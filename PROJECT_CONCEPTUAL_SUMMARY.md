# PROJECT CONCEPTUAL SUMMARY

## SDR Spectrum Monitoring Sensor — ANE (Colombia)

Hybrid **C (data plane) + Python 3.11+ (control plane)** sensor for a HackRF One on a Raspberry Pi 5, managed by the Colombian Spectrum Agency (ANE).

---

## 1. System Purpose

A low-cost, edge-deployed RF monitoring sensor that:
- Acquires IQ samples from a HackRF One SDR
- Computes Power Spectral Density (PSD) using Welch or Polyphase Filter Bank methods
- Optionally demodulates FM/AM broadcasts and streams audio via WebRTC
- Reports spectral data and device status to a central REST API (`rsm.ane.gov.co`)
- Executes scheduled measurement campaigns with automatic retry on network failure

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLOUD (ANE Server)                       │
│   REST API: GET /realtime, GET /campaigns                       │
│   REST API: POST /data, POST /status, POST /gps                 │
│   WebSocket: /ws/signal/{SENSOR_ID} (WebRTC signaling)          │
└──────────────┬──────────────────────────────────────┬───────────┘
               │ HTTPS (zmq REQ/REP over IPC inside)  │ WSS
               │                                      │
┌──────────────▼──────────────────────────────────────▼───────────┐
│                    RASPBERRY PI 5 (Sensor)                       │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                   PYTHON CONTROL PLANE                    │   │
│  │                                                           │   │
│  │  orchestrator.py ◄──► campaign_runner.py                  │   │
│  │       │                     │                             │   │
│  │       ▼                     ▼                             │   │
│  │  status.py          retry_queue.py                        │   │
│  │       │                     │                             │   │
│  │       ▼                     ▼                             │   │
│  │  server_webrtc.py    functions.py + utils/                │   │
│  │  (GStreamer+WS)     (state machine, DSP cleanup,          │   │
│  │                      scheduling, shared memory)            │   │
│  └──────────────────────┬───────────────────────────────────┘   │
│                         │ ZMQ REQ/REP                            │
│                         │ ipc:///tmp/rf_engine                   │
│  ┌──────────────────────▼───────────────────────────────────┐   │
│  │                    C DATA PLANE                           │   │
│  │                                                            │   │
│  │  rf_app (rf/rf.c)                                         │   │
│  │    ├── Parser (JSON → config)                              │   │
│  │    ├── Ring Buffer (lock-free SPSC)                        │   │
│  │    ├── SDR HAL (HackRF control)                            │   │
│  │    ├── PSD Engine (Welch / PFB via FFTW3+OpenMP)           │   │
│  │    ├── Channel Filter (freq-domain brick-wall)             │   │
│  │    ├── FM/AM Demodulators → Opus Encoder → TCP :9000       │   │
│  │    └── Calibration (3-stage: sweep → pilot → symmetry)     │   │
│  │                                                            │   │
│  │  ltegps_app (gps-lte/gps-lte.c)                           │   │
│  │    ├── LTE modem control (AT/UART + PPP)                   │   │
│  │    ├── GPS acquisition (NMEA parsing)                      │   │
│  │    └── GPS → SHM + POST /gps                               │   │
│  └────────────────────────────────────────────────────────┘   │
│                                                                  │
│  Shared Memory: /dev/shm/persistent.json (tmpfs)                │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Module Inventory

### 3.1 C Data Plane — RF Engine (`rf/`)

| Module | Files | Responsibility |
|---|---|---|
| **Main loop** | `rf/rf.c` | Request-driven state machine: receives JSON config via ZMQ, orchestrates capture→DSP→publish |
| **Parser** | `libs/parser.c`, `libs/parser.h` | Deserializes incoming JSON into `DesiredCfg_t` with defaults, validation, and Nyquist clamping |
| **SDR HAL** | `libs/sdr_HAL.c`, `libs/sdr_HAL.h` | Hardware abstraction over `libhackrf`: applies frequency, sample rate, gains, amplifier, PPM correction |
| **Ring Buffer** | `libs/ring_buffer.c`, `libs/ring_buffer.h` | Lock-free SPSC circular buffer with atomic head/tail; decouples `rx_callback` from consumer threads |
| **PSD Engine** | `libs/psd.c`, `libs/psd.h` | Welch and Polyphase Filter Bank spectral estimation; IQ compensation (DC offset, gain/phase imbalance); FFTW3 + OpenMP |
| **Channel Filter** | `libs/chan_filter.c`, `libs/chan_filter.h` | Frequency-domain brick-wall filter with anti-blooming and raised-cosine transition mask |
| **FM Demodulator** | `libs/fm_radio.c`, `libs/fm_radio.h` | Full FM broadcast chain: pre-decimation → phase discriminator → de-emphasis → decimation → DC blocker → audio LPF |
| **AM Demodulator (Local)** | `libs/am_radio_local.c`, `libs/am_radio_local.c` | Production AM: CIC decimator → carrier mean tracker → normalization → AGC → PCM16 |
| **IQ IIR Filter** | `libs/iq_iir_filter.c`, `libs/iq_iir_filter.h` | Butterworth bandpass (biquad cascade) for sub-band isolation before demodulation |
| **Audio Stream Context** | `libs/audio_stream_ctx.c`, `libs/audio_stream_ctx.h` | Aggregator binding demodulators, Opus config, atomic mode/rate flags, IIR filter state |
| **Opus Transmitter** | `libs/opus_tx.c`, `libs/opus_tx.h` | Opus encoder + TCP sender with custom binary header and `send_all` semantics |
| **Net Audio Retry** | `libs/net_audio_retry.c`, `libs/net_audio_retry.h` | Resilience layer for Opus TCP: auto-reconnect, cancelable sleep, retry loop |
| **ZMQ Util** | `libs/zmq_util.c`, `libs/zmq_util.h` | Synchronous ZMQ REP socket wrapper: 100 ms recv timeout, reconnect on error |
| **Utils** | `libs/utils.c`, `libs/utils.h` | `.env` reader, atomic SHM read/write with `flock` |
| **Data Types** | `libs/datatypes.h` | Central type registry: `signal_iq_t`, `SDR_cfg_t`, `DesiredCfg_t`, enums for modes/windows/methods |

### 3.2 C Data Plane — GPS/LTE Engine (`gps-lte/`)

| Module | Files | Responsibility |
|---|---|---|
| **Main** | `gps-lte/gps-lte.c` | LTE modem lifecycle, PPP management, GPS coordinate acquisition, periodic reporting |
| **GPS** | `libs/bacn_GPS.c`, `libs/bacn_GPS.h` | NMEA sentence parsing, coordinate extraction |
| **LTE** | `libs/bacn_LTE.c`, `libs/bacn_LTE.h` | AT command interface for cellular modem control |
| **GPIO** | `common/bacn_gpio.c`, `common/bacn_gpio.h` | GPIO pin control via `libgpiod` (antenna switching, LED, reset) |
| **Utils** | `libs/utils.c`, `libs/utils.h` | Shared utilities (SHM write, file I/O) |

### 3.3 Python Control Plane

| Module | File | Responsibility |
|---|---|---|
| **Orchestrator** | `orchestrator.py` | Central event loop: alternates between realtime and campaign modes; manages WebRTC child process; enforces state machine |
| **Campaign Runner** | `campaign_runner.py` | One-shot cron-invoked script: reads params from SHM, acquires data, uploads, manages local storage (Queue/Historic) with disk-aware cleanup |
| **Status Reporter** | `status.py` | Systemd timer-triggered: collects CPU/RAM/disk/temp/ping metrics, reads NTP sync time, POSTs to `/status` |
| **Retry Queue** | `retry_queue.py` | Systemd timer-triggered: processes `Queue/*.json` oldest-first, up to 2 retries per file, halts on first exhaustion |
| **WebRTC Server** | `server_webrtc.py` | Standalone async process: bridges C engine's Opus TCP stream → GStreamer pipeline → WebRTC peer connection via WebSocket signaling |
| **Functions** | `functions.py` | Global state machine (`GlobalSys`), campaign cron scheduler (`CronSchedulerCampaign`), upload formatter, `AcquireDual` acquisition engine with DC spike removal |
| **Config** | `cfg.py` | Central configuration: env vars, API URLs, paths, MAC detection, timestamps (Colombia UTC-5), atomic log rotation, `run_and_capture` wrapper |

### 3.4 Python Utilities (`utils/`)

| Module | File | Responsibility |
|---|---|---|
| **I/O & SHM** | `io_util.py` | `atomic_write_bytes` (temp+fsync+replace), `ShmStore` (fcntl-locked read/write to `/dev/shm/persistent.json`), `ElapsedTimer` |
| **HTTP & ZMQ** | `request_util.py` | `RequestClient` (HTTP wrapper), `ZmqPairController` (async ZMQ REQ with socket recycling), `ServerRealtimeConfig`/`FilterConfig` dataclasses with validation |
| **System Status** | `status_util.py` | `StatusDevice` (reads `/proc/stat`, `/proc/meminfo`, thermal zone, disk, ping), `StatusPost` DTO |
| **DC Spike Detection** | `dc_spike_detection.py` | Slope-based symmetric detector for DC artifact width in PSD |
| **DC Spike Removal** | `dc_spike_removal.py` | Full pipeline: detect → expand → reconstruct (linear interpolation or polynomial fit) |
| **Spectral Content Analysis** | `spectral_content_analysis.py` | Histogram-based detection of empty vs. occupied spectrum around DC |
| **DSP Primitives** | `libs_DSP.py` | Moving average, MAD scale estimation, discrete differences, polynomial reconstruction |
| **Benchmarking** | `benchmarking.py` | Optional profiling: CPU%, memory, disk I/O, thread count via `psutil` |

### 3.5 System Initialization (`init_sys.py`)

Generates 7 systemd unit/timer files into `daemons/`:

| Unit | Type | Purpose |
|---|---|---|
| `rf-ane2.service` | Service (Restart=always) | C RF engine with flock singleton |
| `ltegps-ane2.service` | Service (Restart=always) | C GPS/LTE engine with flock singleton |
| `orchestrator-ane2.service` | Service (Restart=always) | Python orchestrator with network pre-check |
| `status-ane2.service` | Oneshot | Status reporter (timer-triggered) |
| `status-ane2.timer` | Timer | 30 s interval + 1 min after boot |
| `retry-queue-ane2.service` | Oneshot | Retry processor (timer-triggered) |
| `retry-queue-ane2.timer` | Timer | 300 s interval + 1 min after boot |

---

## 4. State Machine

```
                    ┌──────────────┐
                    │     IDLE     │◄──────────────────────────┐
                    └──────┬───────┘                           │
                           │                                   │
              ┌────────────┼────────────┐                      │
              ▼            ▼            ▼                      │
       ┌──────────┐ ┌───────────┐ ┌──────────────┐            │
       │ REALTIME │ │ CAMPAIGN  │ │  KALIBRATING │            │
       └────┬─────┘ └─────┬─────┘ └──────┬───────┘            │
            │              │              │                     │
            └──────────────┴──────────────┴─────────────────────┘
```

**Enforced by `GlobalSys` class in `functions.py`:**
- Only one active state at a time
- All entry points check `GlobalSys.is_idle()` before proceeding
- Transitions are logged with timestamps
- Mutual exclusion prevents concurrent acquisitions

---

## 5. IPC Contract (Python ↔ C)

### ZMQ REQ/REP over `ipc:///tmp/rf_engine`

```
Python (ZmqPairController)                    C (rf_app)
        │                                          │
        │──── JSON config string ─────────────────▶│
        │     (REQ socket, 15s timeout)            │
        │                                          │ parse_config_rf()
        │                                          │ find_params_psd()
        │                                          │ HackRF RX + DSP
        │◀──── JSON result ────────────────────────│
        │     (REP socket)                         │ publish_results()
```

**Python side (`ZmqPairController`):**
- Uses `zmq.REQ` (despite the class name)
- `LINGER=0`, `IMMEDIATE=1`, `SNDHWM=1`, `RCVHWM=1`
- Destroys and recreates socket on any timeout/error
- 15 s timeout for both send and receive

**C side (`zmq_util.c`):**
- Uses `ZMQ_REP` with 100 ms recv timeout
- `zpair_reconnect()` resets REQ/REP state machine on error
- JSON reply via cJSON serialization

---

## 6. Shared State (`/dev/shm/persistent.json`)

Managed by `ShmStore` (Python) and `shm_add_to_persistent`/`shm_consult_persistent` (C).

| Key | Writer | Reader | Purpose |
|---|---|---|---|
| `campaign_runner_running` | `campaign_runner.py` | `campaign_runner.py` | Guard flag preventing overlapping executions |
| `campaign_id` | `CronSchedulerCampaign` | `campaign_runner.py` | Active campaign identifier |
| `expires_at_ms` | `CronSchedulerCampaign` | `campaign_runner.py` | Campaign expiration timestamp |
| `center_freq_hz`, `sample_rate_hz`, etc. | `CronSchedulerCampaign` | `campaign_runner.py` | RF parameters for campaign acquisitions |
| `ppm_error` | `calibrate_hackrf()` (C) | `orchestrator.py` | PPM frequency correction from calibration |
| `last_kal_ms` | C calibration | `status.py` | Last calibration timestamp |
| `last_lat`, `last_lng` | `ltegps_app` | API/status | GPS coordinates |
| `changed_gps` | `ltegps_app` | API/status | GPS movement flag (>200m threshold) |
| `legal_freqs` | calibration scripts | calibration scripts | Cached legal FM frequencies |
| `delta_t_ms` | `orchestrator.py` / `campaign_runner.py` | `status.py` | HTTP round-trip latency |
| `method_psd` | scheduler | `campaign_runner.py` | PSD method (welch/pfb) |

---

## 7. Data Flow Diagrams

### 7.1 Realtime Acquisition

```
Server ──GET /realtime──▶ orchestrator.py
                            │
                            ▼
                    ServerRealtimeConfig
                            │
                            ▼
                    ZmqPairController.request(cfg)
                            │ ZMQ IPC
                            ▼
                    rf_app: parse → DSP → PSD
                            │ ZMQ IPC
                            ▼
                    AcquireDual.get_corrected_data()
                            │ DC spike removal pipeline
                            ▼
                    format_data_for_upload()
                            │
                            ▼
                    POST /data ──▶ Server

Parallel path (if demodulation enabled):
  rf_app audio thread → TCP :9000 (Opus frames)
  server_webrtc.py → GStreamer → WebRTC → Browser
```

### 7.2 Campaign Acquisition

```
orchestrator.py ──GET /campaigns──▶ Server
                    │
                    ▼
            CronSchedulerCampaign.sync_jobs()
                    │
                    ├──▶ ShmStore (write RF params)
                    │
                    └──▶ Crontab entry (*/N * * * *)
                              │
                              ▼ cron fires
                    campaign_runner.py
                              │
                              ├──▶ Read params from ShmStore
                              │
                              ├──▶ ZMQ → rf_app → PSD
                              │
                              ├──▶ DC spike removal
                              │
                              ├──▶ POST /data (with campaign_id)
                              │
                              ├──▶ On failure → Queue/*.json
                              │
                              └──▶ On success → Historic/*.json
```

### 7.3 Status & Retry Loop

```
status-ane2.timer (30s) ──▶ status.py
                               │
                               ├──▶ ShmStore (delta_t_ms, last_kal_ms)
                               ├──▶ /proc/* (CPU, RAM, disk, temp)
                               ├──▶ ping (latency)
                               └──▶ POST /status (up to 10 retries)

retry-queue-ane2.timer (300s) ──▶ retry_queue.py
                                     │
                                     ├──▶ Read Queue/*.json (oldest first)
                                     ├──▶ POST /data per file (2 retries)
                                     ├──▶ Delete on success or 4xx
                                     └──▶ Halt on first transient exhaustion
```

### 7.4 WebRTC Audio Pipeline

```
rf_app audio thread
    │
    ├── Demodulate (FM/AM)
    ├── IIR bandpass filter
    ├── Opus encode
    └── TCP send (port 9000, binary header)
         │
         ▼
server_webrtc.py
    │
    ├── TCP server (accept connection)
    ├── Parse binary header (magic, seq, rate, ch, len)
    ├── Push Opus frames into GStreamer pipeline
    │     appsrc → opusparse → rtpopuspay → webrtcbin
    │
    └── WebSocket signaling (wss://rsm.ane.gov.co/ws/signal/{ID})
         │
         ├── SDP offer/answer exchange
         └── ICE candidate trickle
              │
              ▼
         Browser (WebRTC peer)
```

---

## 8. Build & Deployment

### Build Modes

| Mode | Command | Output | Notes |
|---|---|---|---|
| Production | `./build.sh` | `rf_app` + `ltegps_app` | Full build with `libgpiod` |
| Development | `./build.sh -dev` | `rf_app` only | Stubs GPIO, `NO_COMMON_LIBS` |

### Installation

| Step | Script | Actions |
|---|---|---|
| Full deploy | `sudo ./install.sh` | Stop services → install deps → compile libs → build → venv → SHM init → systemd enable → reboot |
| Local dev | `sudo ./install-local.sh` | Same but no systemd, no reboot |
| System init | `init_sys.py` | Generate systemd units, clean cron, clear SHM |

---

## 9. Key Design Patterns

| Pattern | Where | Purpose |
|---|---|---|
| **Lock-free ring buffer** | C `rx_callback` → consumers | Zero-contention hot path for IQ streaming |
| **Condvar wakeup** | C main/audio threads | Avoid polling; efficient sleep-until-data |
| **Request-scoped capture** | C `rb_discard_all()` before each acquisition | Ensures fresh data per request |
| **Lazy device open** | C main loop | HackRF opened on first request, closed after 15s idle |
| **Workspace reuse** | C DSP functions | Heap allocations grow on demand; never freed until shutdown |
| **Global state machine** | Python `GlobalSys` | Prevents concurrent acquisitions |
| **Atomic file writes** | Python `atomic_write_bytes` | temp + fsync + rename for crash safety |
| **fcntl-locked SHM** | Python `ShmStore`, C `shm_add_to_persistent` | Concurrent-safe shared state on tmpfs |
| **Socket recycling** | Python `ZmqPairController` | Destroys/recreates ZMQ socket on error, resetting REQ/REP state |
| **Cron + timer hybrid** | Campaign scheduling, status/retry | Flexible cron for campaigns; fixed systemd timers for periodic tasks |
| **Guard flags** | `campaign_runner_running` in SHM | Prevents overlapping one-shot campaign runners |
| **Disk-aware storage** | `campaign_runner.py` | Auto-deletes oldest Historic files above 80% disk usage |
| **Cancelable blocking** | C `sleep_cancelable_ms`, Python `ElapsedTimer` | Responsive shutdown without busy-waiting |
| **Escalating process kill** | Python `stop_managed_process` | SIGTERM → wait → SIGKILL via process group |

---

## 10. External Dependencies

### C Libraries

| Library | Purpose |
|---|---|
| `libhackrf` | HackRF One SDR control |
| `libzmq` | ZeroMQ IPC transport |
| `cJSON` | JSON parsing/generation |
| `FFTW3` | Fast Fourier Transform |
| `libusb` | USB device access |
| `libcurl` | HTTP client (GPS/LTE status) |
| `libopus` | Opus audio encoding |
| `libgpiod` | GPIO control (production only) |
| OpenMP | Parallel DSP (Welch/PFB) |

### Python Dependencies

| Package | Purpose |
|---|---|
| `requests` | HTTP client |
| `pyzmq` | ZeroMQ bindings |
| `numpy` | Numerical computation (DC removal, DSP) |
| `python-dotenv` | Environment variable loading |
| `python-crontab` | System crontab manipulation |
| `websockets` | WebSocket client (signaling) |
| `GStreamer` + `gstwebrtcbin` | WebRTC media pipeline (via `gi` bindings) |

---

## 11. Configuration

All configuration loaded from `.env` via `python-dotenv`:

| Variable | Default | Description |
|---|---|---|
| `API_URL` | `https://rsm.ane.gov.co:12443/api/sensor` | Central REST API |
| `IPC_ADDR` | `ipc:///tmp/rf_engine` | ZMQ socket for Python↔C |
| `INTERVAL_REQUEST_REALTIME_S` | `5` | Realtime config polling |
| `INTERVAL_REQUEST_CAMPAIGNS_S` | `60` | Campaign sync polling |
| `INTERVAL_STATUS_S` | `30` | Status reporting interval |
| `INTERVAL_RETRY_QUEUE_S` | `300` | Retry queue processing |
| `DEBUG` | `false` | Enable DEBUG console + file logging |
| `VERBOSE` | `false` | Enable INFO console logging |
| `DEVELOPMENT` | `false` | Use dummy MAC, mock crontab |
| `LOG_FILES_NUM` | `10` | Max retained log files |
| `LOG_ROTATION_LINES` | `100` | Lines per log before rotation |

---

## 12. File System Layout

```
project_root/
├── rf_app                    # C RF engine binary (built)
├── ltegps_app                # C GPS/LTE binary (built)
├── orchestrator.py           # Main Python orchestrator
├── campaign_runner.py        # One-shot campaign executor
├── status.py                 # Status reporter
├── retry_queue.py            # Retry processor
├── server_webrtc.py          # WebRTC audio bridge
├── functions.py              # Shared logic, state machine, scheduling
├── cfg.py                    # Central configuration
├── init_sys.py               # System provisioning
├── build.sh                  # CMake build script
├── install.sh                # Production deployment
├── .env                      # Runtime configuration
├── requirements.txt          # Python dependencies
├── CMakeLists.txt            # C build system
├── rf/                       # C RF engine source
│   ├── rf.c
│   └── libs/                 # 15 C modules (parser, PSD, demod, etc.)
├── gps-lte/                  # C GPS/LTE source
│   ├── gps-lte.c
│   └── libs/                 # GPS, LTE, utils
├── common/                   # Shared C code (GPIO)
├── utils/                    # Python utility modules (9 files)
├── json/                     # Reference API/IPC schemas
├── daemons/                  # Auto-generated systemd units
├── Queue/                    # Failed upload payloads
├── Historic/                 # Successful campaign archives
├── Logs/                     # Rotated log files
├── docs/                     # Sphinx documentation source
└── context/                  # Architecture reference docs
```

---

## 13. Error Recovery & Resilience

| Mechanism | Layer | Description |
|---|---|---|
| HackRF session health check | C | Board ID + streaming bit verified before each request |
| Device recovery | C | 3 attempts with 1s spacing after disconnect |
| Lazy device open/close | C | Opens on first request, closes after 15s idle |
| Acquisition timeout | C | 5s condvar deadline; rejects request and invalidates state on expiry |
| ZMQ socket recycling | Python | Destroys/recreates REQ socket on any timeout/error |
| WebRTC auto-reconnect | Python | TCP server accepts new C connection; WS retry loop with 5s backoff |
| HTTP retry with backoff | Python | `status.py`: 10 retries, 0.5s delay; `retry_queue.py`: 2 retries per file, 5s delay |
| Process cleanup | Python | Stale process detection via `/proc/*/cmdline`; SIGTERM → SIGKILL escalation |
| Cron guard | Python | `campaign_runner_running` flag in SHM prevents overlapping executions |
| Disk management | Python | Auto-deletes oldest Historic files above 80% usage; saves only below 90% |
| Cancelable sleep | C + Python | `sleep_cancelable_ms` (50ms slices) and `volatile bool` flags for responsive shutdown |

---

*Generated from project documentation and source code analysis.*
