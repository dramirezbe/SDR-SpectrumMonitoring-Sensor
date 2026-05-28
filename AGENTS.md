# AGENTS.md — SDR Spectrum Monitoring Sensor

## Project identity

Hybrid **C (data plane) + Python 3.11+ (control plane)** sensor for a HackRF One on a Raspberry Pi 5, managed by the Colombian Spectrum Agency (ANE).

## Build & verify

```bash
# Desktop build (no gpiod, RF only)
./build.sh -dev

# RPi production build (rf_app + ltegps_app + gpiod)
./build.sh

# Dev integration flow (venv, no systemd, no reboot)
sudo ./install-local.sh

# Production deployment (systemd, reboot)
sudo ./install.sh
```

**No lint, typecheck, or CI commands exist** in this repo. Do not try to run `ruff`, `flake8`, `mypy`, `clang-tidy`, `mypy`, or `pytest` — they are not configured.

## Architecture

| Layer | Language | Entrypoints | Location |
|---|---|---|---|
| RF engine | C (C99) | `rf_app` | `rf/rf.c` + `rf/libs/*.c` |
| GPS/LTE | C (C99) | `ltegps_app` | `gps-lte/gps-lte.c` + `gps-lte/libs/*.c` |
| Orchestrator | Python | `orchestrator.py` | root |
| Campaign runner | Python | `campaign_runner.py` | root |
| Status reporter | Python | `status.py` | root |
| WebRTC audio | Python | `server_webrtc.py` | root |
| Shared state & helpers | Python | `functions.py`, `cfg.py` | root |
| Systemd init | Python | `init_sys.py` | root |
| Retry queue | Python | `retry_queue.py` | root |
| Utilities | Python | — | `utils/` |

The **state machine** (`GlobalSys` in `functions.py`) enforces exclusivity: only one of `IDLE`, `REALTIME`, `CAMPAIGN`, or `KALIBRATING` active at a time. Do not attempt concurrent acquisitions.

## IPC contract (critical)

- **Python ↔ C** communicate via **ZMQ REQ/REP** over `ipc:///tmp/rf_engine`. Python (`ZmqPairController` in `utils/request_util.py`) sends JSON config strings using `zmq.REQ`; C parses them in `rf/libs/parser.c` and responds with JSON using `ZMQ_REP`. Despite the class name, this is **not** a PAIR socket — it's strict request/reply.
- The C side (`zmq_util.c`) uses `zpair_reconnect()` to recreate the socket on error, resetting the REQ/REP state machine.
- **Inter-process shared state** lives in `/dev/shm/persistent.json` (tmpfs). Read/write it through `ShmStore` (`utils/io_util.py`), never directly.

## Hardware-specific constraints

- Target: **Raspberry Pi 5**. All hot-path C code must be conservative with CPU/memory. Avoid `malloc`/`memcpy` in `rx_callback` and OpenMP regions.
- **Never degrade RF parameters** (sample_rate, nperseg, FFT resolution) requested by the server to "save resources." The user's config is the source of truth.
- **OpenMP + thread-local storage** is a known hazard. The `execute_welch_psd` function had a SIGSEGV from unsafe TLS dereference inside a parallel region. Be defensive with stack vs. heap allocations in `#pragma omp parallel` blocks.
- GPIO uses `libgpiod`; `-dev` builds stub this out with `NO_COMMON_LIBS`.

## Configuration & environment

- `API_URL` defaults to `https://rsm.ane.gov.co:12443/api/sensor` (read from `.env`).
- `DEBUG=true` enables DEBUG console + file logging; `VERBOSE=true` enables INFO console logging. Without either, console shows only WARNING/ERROR.
- `DEVELOPMENT=true` uses `DUMMY_MAC` for MAC identification.
- `LOG_FILES_NUM` (default `10`) and `LOG_ROTATION_LINES` (default `100`) control log rotation.
- `IPC_ADDR` defaults to `ipc:///tmp/rf_engine` for Python↔C ZMQ communication.
- `INTERVAL_REQUEST_CAMPAIGNS_S` (default `120`), `INTERVAL_REQUEST_REALTIME_S` (default `5`), `INTERVAL_STATUS_S` (default `30`), `INTERVAL_RETRY_QUEUE_S` (default `300`) control polling intervals.
- All timestamps are **Colombia time (UTC-5)**, applied as a manual offset.

## Key conventions

- Use `cfg.run_and_capture(...)` + `cfg.set_logger()` in every Python entrypoint for consistent logging with atomic rotation to `Logs/`.
- **Never write JSON files directly** — use `atomic_write_bytes` from `utils`.
- Campaign cron scheduler (`functions.py`) *clears* all `CAMPAIGN_*` jobs and keeps only the highest `campaign_id` in window.
- `init_sys.py` generates systemd unit/timer files into `daemons/` at install time. These are not committed.
- Build artifacts (`rf_app`, `ltegps_app`) are placed in the repo root by `build.sh` and are gitignored.

## Reference docs (read when working in these areas)

- `context/AI_RULES.md` — hardware constraints and refactoring rules.
- `context/issues-campaign-19may26.md` — post-mortem of the OpenMP/TLS crash.
- `context/copilot-instructions.md` — complementary AI instructions.
- `context/OPTIMIZATION_ROADMAP.md` — optimization roadmap and planned improvements.
