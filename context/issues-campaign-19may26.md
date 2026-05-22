# Issue Context: RF Node Failures on May 19, 2026

## Summary

On May 19, 2026, campaign execution on deployed Raspberry Pi nodes was partially healthy:

- `orchestrator.py` was running correctly as the `orchestrator-ane2` systemd service.
- campaign scheduling through cron was working correctly.
- `campaign_runner.py` was being launched by cron with the `CAMPAIGN_RUNNER` journal tag.

The failure was observed inside the RF engine (`rf_app` / `rf.c`), not in the Python orchestration path.

## Confirmed Good Components

### 1. `install.sh` / systemd provisioning

The deployment flow creates the Debian services from `init_sys.py`, including:

- `rf-ane2.service`
- `ltegps-ane2.service`
- `orchestrator-ane2.service`
- `retry-queue-ane2.service`
- `status-ane2.service`

`campaign_runner.py` is **not** installed as a systemd service. It is executed by cron.

### 2. `orchestrator.py`

`orchestrator.py` correctly:

- polls the backend for campaigns
- transitions `IDLE -> CAMPAIGN`
- updates shared-memory campaign parameters
- writes cron jobs through `CronSchedulerCampaign`

Relevant observed behavior:

- `orchestrator-ane2` logged campaign checks and state transition to `CAMPAIGN`
- no evidence was found that the orchestrator itself crashed or stopped scheduling

### 3. `campaign_runner.py`

Campaign execution through cron was confirmed on deployed nodes.

Observed journal pattern:

- `CRON (...) CMD (systemd-cat -t CAMPAIGN_RUNNER ... campaign_runner.py # CAMPAIGN_294)`
- `CAMPAIGN_RUNNER ... Starting Campaign Acquisition ID: 294`

This confirms:

- cron is active
- campaign jobs are being written and reloaded
- Python runner launch is healthy
- journald visibility works with `journalctl -t CAMPAIGN_RUNNER`

## Actual Problem Area

The runtime issue is in the RF engine, specifically in `rf/rf.c`.

Two failure modes were observed on May 19, 2026 during campaign `294`:

### A. Segmentation fault / service restart

Observed on `rf-ane2.service`:

- `rf-ane2.service: Main process exited, code=exited, status=139/n/a`
- `rf-ane2.service: Failed with result 'exit-code'`
- systemd restarted the RF service automatically

`status=139` indicates a segmentation fault (`SIGSEGV`).

### B. Acquisition timeout with hardware recovery

Observed RF log sequence:

- config received
- tune applied
- repeated `[AUDIO] Waiting socket...`
- `[RF] Error: Acquisition Timeout (buffer empty).`
- `[RECOVERY] Initiating Hardware Reset sequence...`
- `[RECOVERY] Device Re-opened successfully.`

This confirms the RF path can also stay alive but fail to receive/process samples in time.

## Exact RF Code Locations

Relevant source points:

- timeout detection:
  - `rf/rf.c:1362`
- recovery logic:
  - `rf/rf.c:816`
- campaign/sample processing path:
  - `rf/rf.c:1394-1423`
- audio reconnect wait loop:
  - `rf/libs/net_audio_retry.c:157`

The crashing window happens after:

- RF config is received via ZMQ
- tuning is applied
- `linear_buffer` processing begins

and before successful PSD publication is logged.

## Important Deployment Evidence

Both affected nodes were confirmed to include the suspected RF optimization commits:

- `9c95f4a` — `Implement Wave 1 optimizations` — April 16, 2026
- `21d9326` — `Optimize RF processing workspaces and DSP caches` — April 17, 2026

Verification used on nodes:

```bash
git merge-base --is-ancestor 9c95f4a HEAD && echo includes_9c95f4a
git merge-base --is-ancestor 21d9326 HEAD && echo includes_21d9326
```

Both `ane1-pi` and `ane4-pi` returned:

```text
includes_9c95f4a
includes_21d9326
```

This means the deployed nodes already contain the likely regression window.

## Most Likely Regression Window

The issue does **not** appear to come from newer May 2026 commits.

The most likely regression window is:

1. `9c95f4a` on April 16, 2026
2. `21d9326` on April 17, 2026

### Why `9c95f4a` is the strongest candidate

This commit changed the exact acquisition/processing block now implicated in failures:

- reusable RF workspaces replaced per-request allocations
- ring buffer implementation was changed from mutex-based to atomic lock-free logic
- PSD processing path was rewritten to reuse cached buffers

The line-level blame for the current processing block in `rf/rf.c` points to `9c95f4a`.

### Why this is risky

`9c95f4a` changed `rf/libs/ring_buffer.c` from serialized access to atomic head/tail operations while:

- `rx_callback()` can write concurrently
- the main RF loop can read concurrently
- `rb_reset()` can zero and reset buffers during tune/recovery transitions
- the audio thread may also access the audio ring buffer concurrently

This is a plausible cause for:

- intermittent `SIGSEGV` crashes
- intermittent acquisition buffer starvation / timeout

## Secondary Technical Risk

There is also an older ZeroMQ design risk in `rf/libs/zmq_util.c`:

- one thread does `zmq_recv()` on a socket
- another thread does `zmq_send()` on that same socket

ZeroMQ sockets are not thread-safe.

This is a valid bug candidate, but it predates the April 16-17, 2026 regression window, so it is less likely to be the newly introduced break.

## Current Working Hypothesis

The Python control path is healthy:

- `install.sh` provisions correctly
- `init_sys.py` generates correct services
- `orchestrator.py` schedules campaigns correctly
- cron launches `campaign_runner.py` correctly

The unstable component is the RF binary path:

- `rf_app`
- `rf/rf.c`
- ring buffer / acquisition / PSD runtime

Most likely cause:

- regression introduced by April 16-17, 2026 RF optimization changes, especially the ring buffer and reusable workspace changes

## Suggested Next Validation

Best rollback probe:

- test a node on commit `8daa487` (April 14, 2026), which is before the suspected RF optimization window

If campaign acquisition becomes stable there, it strongly supports the regression hypothesis around `9c95f4a` / `21d9326`.

## Update on May 20, 2026: Reproduced and Narrowed Root Cause

The original May 19, 2026 hypothesis was refined with direct reproduction on May 20, 2026.

The segmentation fault was reproduced manually by sending the calibration command directly to a running `rf_app` over the real ZMQ IPC socket:

```bash
python3 -m tools.test_kalibrate_payload
```

This sent:

```json
{"calibrate": true}
```

Observed behavior:

- the Python ZMQ client connected successfully to `ipc:///tmp/rf_engine`
- the calibration command was sent successfully
- the client timed out waiting for a response
- `rf_app` crashed and produced a new core dump

This is important because it proves the crash can occur from the calibration path alone, without involving:

- cron scheduling
- `campaign_runner.py` acquisition payloads
- campaign-specific parser differences

## Why Campaigns Crash but Realtime Does Not

The key behavioral difference is now confirmed:

- campaign mode triggers `_perform_calibration_sequence()` before entering campaign acquisition
- realtime mode does **not** use this calibration path

That means campaigns exercise a different RF-engine code path than realtime.

In the RF engine:

- ZMQ command reception enters `on_command_received()`
- when `{"calibrate": true}` is received, execution goes into `calibrate_hackrf()`
- the crash happens during the Welch PSD sweep executed inside calibration

Relevant path:

- `rf/rf.c:895` -> calibration branch in `on_command_received()`
- `rf/rf.c:296` -> `calibrate_hackrf()`
- `rf/rf.c:404` -> `execute_welch_psd()`

## Core Dump Evidence

`coredumpctl` for the reproduced May 20, 2026 crash showed the critical stack:

- `execute_welch_psd._omp_fn.0`
- `execute_welch_psd`
- `calibrate_hackrf`
- `on_command_received`
- `listener_thread`

This demonstrates that the reproduced crash is not centered in:

- campaign payload parsing
- `CronSchedulerCampaign`
- `campaign_runner.py`
- steady-state acquisition after campaign tuning

Instead, the crash occurs inside the calibration Welch PSD computation.

## Actual Root Cause

The root cause is in `rf/libs/psd.c`, inside `execute_welch_psd()`.

Welch used a thread-local cache:

- `static __thread welch_window_cache_t tl_window_cache`

When the window cache was rebuilt, the code computed `u_norm` using:

- `#pragma omp parallel for reduction(+:u_norm)`

but the OpenMP worker threads dereferenced `tl_window_cache.window` directly inside that loop.

That is unsafe because `tl_window_cache` is thread-local storage:

- the calling thread initializes its own `tl_window_cache.window`
- OpenMP worker threads have different TLS instances
- those worker TLS instances may still be uninitialized
- workers can therefore read an invalid/null pointer and crash

This explains why the fault lands specifically inside:

- `execute_welch_psd._omp_fn.0`

## Why PFB Did Not Show the Same Failure

`execute_pfb_psd()` follows a safer pattern:

- thread-local buffers are resolved into local pointer variables before use in the parallel work
- worker code uses those local resolved pointers instead of repeatedly dereferencing TLS symbols that belong to another thread context

The Welch correction followed that same stability pattern without changing the Welch algorithm itself.

## Applied Correction

The fix was applied in `rf/libs/psd.c`.

Instead of reading `tl_window_cache.window` directly inside the OpenMP reduction loop, the code now first resolves:

```c
const double *window_cache = tl_window_cache.window;
```

and then uses `window_cache[i]` inside the reduction.

This preserves the Welch algorithm and the cache design, while removing the invalid TLS access pattern during OpenMP execution.

## Additional Hardening Applied

Two additional defensive fixes were also applied during investigation:

- `campaign_runner.py` now sends `ppm_error = 0.0` if that value is missing from shared memory
- `rf/libs/sdr_HAL.c` now uses `PRIu64` for `uint64_t` frequency logging instead of `%lu`

These were not the primary cause of the reproduced crash, but they remove secondary risk.

## Validation After the Fix

After applying the Welch correction:

- `rf_app` was rebuilt with `./build.sh -dev`
- the new binary was started
- the same ZMQ calibration command was sent again

Observed result after the fix:

- calibration completed successfully
- the engine returned a valid response with `ppm_error`
- no timeout occurred in the Python test client
- no new core dump was generated
- `rf_app` remained alive after calibration

Observed response example:

```json
{"status":"calibration_complete","ppm_error":-8.135040283203125}
```

## Updated Conclusion

The May 19, 2026 campaign crash is now attributable to the calibration Welch PSD path, not to the campaign payload itself.

More specifically:

- campaigns crashed because they invoked the pre-campaign calibration sequence
- the calibration sequence executed `execute_welch_psd()`
- Welch contained an invalid TLS + OpenMP access pattern
- that bug produced the observed `SIGSEGV`

This finding supersedes the earlier primary hypothesis that the main cause was most likely the April 16-17 ring-buffer/runtime optimization window.
