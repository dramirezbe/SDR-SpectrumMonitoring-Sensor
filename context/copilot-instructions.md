# Copilot instructions for SDR-SpectrumMonitoring-Sensor

## Build, test, and lint commands

### Build (C binaries)
- **Development build (no GPIO dependency):** `./build.sh -dev`
  - Builds `rf_app` with `-DBUILD_STANDALONE=ON` and no `gpiod`.
- **Target/RPi build:** `./build.sh`
  - Builds `rf_app` and `ltegps_app`.

### End-to-end install flows used as integration checks
- **Development flow:** `sudo ./install-local.sh`
  - Recreates `venv`, installs dependencies, no systemd/reboot.
- **Production flow:** `sudo ./install.sh`
  - Full provisioning, daemons/timers, and reboot.

### Test entry points present in this repository
- **Main scripted benchmark flow:** `python3 test/tester.py`
- **Single test/scenario run (example):** `python3 tools/test_kalibrate_payload.py`
  - Sends `{"calibrate": true}` through ZMQ and waits for one RF engine response.

### Lint
- No dedicated lint command is defined in this repository (no `ruff`, `flake8`, `pylint`, `mypy`, or `clang-tidy` config/targets).

## High-level architecture

- This is a **hybrid C + Python sensor stack**:
  - **C runtime/data plane:** `rf_app` (`rf/rf.c` + `rf/libs/*.c`) and `ltegps_app` (`gps-lte/gps-lte.c` + libs).
  - **Python control plane:** `orchestrator.py`, `campaign_runner.py`, `retry_queue.py`, `status.py`, calibration scripts.

- The main control loop is in `orchestrator.py`:
  - Polls API for realtime config and campaign windows.
  - Enforces exclusivity with `GlobalSys` state machine (`IDLE`, `REALTIME`, `CAMPAIGN`, `KALIBRATING`).
  - Uses `AcquireDual` to request spectra from C over ZMQ and apply DC-spike correction pipeline before upload.

- **IPC contract** between Python and C:
  - Python side: `ZmqPairController` (`utils/request_util.py`) on `cfg.IPC_ADDR` (default `ipc:///tmp/rf_engine`).
  - C side: JSON parsing in `rf/libs/parser.c` (`parse_config_rf`), then RF processing and JSON response.

- **Shared state** is centralized in `/dev/shm/persistent.json` through `ShmStore` (`utils/io_util.py`):
  - Used across orchestrator, campaign runner, status, and calibration (e.g. `ppm_error`, `last_kal_ms`, campaign parameters, `delta_t_ms`).

- **Scheduling/runtime services**:
  - `init_sys.py` generates systemd unit/timer files in `daemons/`.
  - Campaign execution is scheduled via cron (`CronSchedulerCampaign`) and runs `campaign_runner.py`.
  - Realtime demodulation dynamically starts/stops `server_webrtc.py` as a managed subprocess.

## Key codebase conventions

- Respect the project’s hard constraints in `AI_RULES.md`:
  - Optimize hot C paths for Raspberry Pi 5 resource limits.
  - Do **not** degrade requested RF resolution/parameters from server JSON to “save resources”.
  - Prefer function-by-function refactors to avoid breaking interfaces.
  - Use `./build.sh -dev` for non-RPi compilation; use `sudo ./install-local.sh`/`sudo ./install.sh` for full-flow validation.

- The RF config path is intentionally strict:
  - Python validates with `ServerRealtimeConfig`.
  - C parser applies defaults and clamps filter bounds to Nyquist range (`center_freq ± sample_rate/2`).
  - `cooldown_request` defaults to `1.0`, must be `>= 0`, and is treated as sticky until updated.

- Use repository helpers instead of ad-hoc file/state handling:
  - `atomic_write_bytes` for JSON/file persistence.
  - `ShmStore` for inter-process state instead of separate temp files.

- Entry-point scripts consistently use `cfg.run_and_capture(...)` + `cfg.set_logger()`:
  - Preserves project logging behavior (rotating atomic logs in `Logs/` and consistent exception capture).

- Campaign scheduling follows a specific rule:
  - `CronSchedulerCampaign.sync_jobs` clears existing `CAMPAIGN_*` jobs and schedules only the highest-`campaign_id` active candidate in window.
