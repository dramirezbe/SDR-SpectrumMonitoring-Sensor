# RF Engine — Architecture & Module Reference

## Overview

The RF engine (`rf/`) is a **C99** real-time data plane that controls a HackRF One SDR peripheral. It ingests high-speed IQ samples from the ADC, applies digital signal processing (DSP), and serves two output paths: **spectral data** (PSD) returned synchronously over ZMQ, and **demodulated audio** streamed asynchronously over TCP in Opus format. The engine is driven by a JSON-based request/reply protocol and designed to run with bounded latency on a Raspberry Pi 5.

Input IQ flows through a **lock-free ring buffer** (producer → consumer decoupling) into a **request-scoped DSP pipeline** that can optionally filter, demodulate, or estimate power spectral density. A concurrent **audio thread** drains a secondary ring buffer and continuously produces PCM→Opus frames for network transmission.

---

## Module Inventory (`rf/libs/`)

### 1. Data Types (`datatypes.h`)

Central type registry for the entire engine. No implementation — only structs and enums.

| Type | Purpose |
|---|---|
| `signal_iq_t` | Complex IQ buffer: `double _Complex *` pointer + sample count |
| `SDR_cfg_t` | HackRF hardware state: center freq (nominal + PPM-corrected), sample rate, LNA/VGA gain, amp, PPM error |
| `DesiredCfg_t` | Full user request: RF mode, PSD method, calibration flag, hardware params, RBW, overlap, window type, optional filter bounds, cooldown |
| `PsdConfig_t` | Spectral analysis parameters: window type, sample rate, nperseg, noverlap |
| `RB_cfg_t` | Ring buffer sizing: total bytes, element count |
| `filter_t` / `filter_audio_t` | Frequency boundaries (start/end Hz) and IIR filter topology |
| `rf_mode_t` | Enum: `PSD_MODE`, `FM_MODE`, `AM_MODE` |
| `Psd_method` | Enum: `WELCH`, `PFB` |
| `PsdWindowType_t` | Enum: Hamming, Hann, Rectangular, Blackman, Flat Top, Kaiser, Tukey, Bartlett |
| `am_depth_state_t` | AM quality: envelope min/max, EMA-smoothed depth percentage |
| `fm_dev_state_t` | FM quality: peak deviation, EMA-smoothed deviation in Hz |

---

### 2. SDR HAL (`sdr_HAL.h`, `sdr_HAL.c`)

Hardware abstraction over `libhackrf`. Single entry point:

**`hackrf_apply_cfg(device, cfg)`** — atomically applies frequency, sample rate, gains, amplifier, and PPM correction to the device. Internally computes a PPM-corrected center frequency (`center_freq_corrected`) used throughout DSP, while preserving the nominal frequency for reporting.

This is the only place the engine touches HackRF registers. All tuning decisions flow through this function.

---

### 3. Ring Buffer (`ring_buffer.h`, `ring_buffer.c`)

Lock-free single-producer, single-consumer (SPSC) circular buffer using `atomic_size_t` head and tail cursors. Designed for the hot path: `rx_callback` (producer) and main/audio thread (consumers) never block each other.

| Operation | Behaviour |
|---|---|
| `rb_write` | Copies data into the buffer; if full, writes as much as fits |
| `rb_read` | Copies data out; returns actual bytes read |
| `rb_available` | Returns byte count between tail and head (readable) |
| `rb_reset` | Zeros both cursors and memset(0) the buffer |
| `rb_discard_all` | Atomically advances tail to head — skips stale data without touching memory |

Cursor arithmetic wraps around `size` (power-of-two constraint), avoiding division/modulo in the hot path.

---

### 4. ZMQ Util (`zmq_util.h`, `zmq_util.c`)

Synchronous ZeroMQ REP socket wrapper implementing a strict 1:1 request/reply pattern.

**`zpair_t`** holds a ZMQ context, a `ZMQ_REP` socket, a 64 KB internal buffer, and the endpoint address.

| Function | Behaviour |
|---|---|
| `zpair_init` | Creates context, binds `ZMQ_REP` to `ipc://` or `tcp://` address |
| `zpair_recv` | Blocks up to 100 ms waiting for a request; returns byte count, `0` on timeout, `-1` on error |
| `zpair_send` | Sends a JSON reply over the socket |
| `zpair_reconnect` | Destroys and recreates the socket to reset the internal REQ/REP state machine (used after errors) |
| `zpair_close` | Graceful shutdown and free |

The 100 ms receive timeout enables the main loop to periodically check idle timers without a separate watchdog thread.

---

### 5. Parser (`parser.h`, `parser.c`)

JSON deserializer that translates incoming command strings into a fully validated `DesiredCfg_t`.

**`parse_config_rf(json_string, target)`** operates in three phases:

1. **Initialize** — hardcoded defaults (PSD mode, Welch method, 100 MHz center, 20 MSps, `cooldown=1.0 s`, etc.)
2. **Extract** — walks the cJSON tree, mapping string keys to struct fields (case-insensitive, with hyphens and underscores normalized)
3. **Clamp** — adjusts filter boundaries so they never exceed Nyquist limits for the given center frequency and sample rate

Validation is permissive: unrecognized keys are silently ignored; malformed JSON returns success but leaves defaults intact. Returns `0` on success.

Also provides `print_config_summary_DEPLOY` for single-line operational logging.

---

### 6. PSD Engine (`psd.h`, `psd.c`)

The spectral estimation core. Built on **FFTW3** with OpenMP parallelism.

**Pre-processing:**

- **`load_iq_into_signal(buffer, bytes, signal)`** — converts interleaved int8 IQ samples (format: `I₀ Q₀ I₁ Q₁ …`) into normalized `double _Complex` values (range approximately `[-1.0, +1.0]`).
- **`iq_compensation(signal)`** — three-stage in-place correction:
  1. DC offset removal (per-channel mean subtraction)
  2. Gain imbalance correction (equalizes I and Q RMS power)
  3. Phase imbalance correction (linear decorrelation of Q onto I)

**Spectral estimation:**

- **`execute_welch_psd(signal, cfg, freq, psd)`** — Welch's averaged periodogram method. Overlapping segments, window function, FFT, squared magnitude, segment averaging. Output in dBm relative to a 50 Ω reference, with a power floor clamp at `1e-20 W` to prevent `-∞`.
- **`execute_pfb_psd(signal, cfg, freq, psd)`** — Polyphase Filter Bank. Uses an 8-tap Kaiser-windowed prototype FIR (β = 8.6, ≈ 80 dB sidelobe rejection), decomposed into polyphase branches, followed by FFT. Better bin isolation than Welch at higher computational cost.

**Parameter solver:**

- **`find_params_psd(desired, hack, psd, rb)`** — derives optimal nperseg, noverlap, and required IQ buffer size from the user-requested RBW, factoring in the ENBW of the selected window. FFT size is forced to the next power of two for efficiency.

---

### 7. Channel Filter (`chan_filter.h`, `chan_filter.c`)

Frequency-domain brick-wall filter operating on IQ signals. Used for isolating a specific sub-band (e.g., an FM station) within the wideband capture before further processing.

**`chan_filter_apply_inplace_abs(sig, cfg, fc_hz, fs_hz)`** follows these steps:

1. **FFT** — project signal to frequency domain
2. **Stage 1 (Anti-blooming)** — compute median magnitude outside the passband; clip any out-of-band peaks exceeding `median + dynamic_threshold`. Prevents strong interferers from dominating the filtered output
3. **Stage 2 (Mask)** — apply raised-cosine transition mask with configurable stopband attenuation. The smooth transition minimizes time-domain ringing (Gibbs phenomenon)
4. **IFFT** — return to time domain with `1/N` normalization

Maintains a cache of FFTW plans to avoid plan-creation overhead on repeated calls. Plans are released via `chan_filter_free_cache()` at shutdown.

---

### 8. FM Demodulator (`fm_radio.h`, `fm_radio.c`)

Full FM broadcast demodulation chain:

1. **Pre-decimation** — coherent IQ averaging to reduce the sample rate before the phase discriminator
2. **Phase discriminator** — computes instantaneous frequency via `Δφ = arg(x[n] · conj(x[n-1]))`, scaled to Hz
3. **De-emphasis** — 1st-order IIR filter with configurable time constant (default 75 µs for Americas)
4. **Decimation** — reduce to audio rate (typically 48 kHz)
5. **DC blocker** — 1st-order high-pass to remove frequency offset bias
6. **Audio LPF** — biquad low-pass at ~15 kHz to suppress out-of-band artifacts
7. **Gain** — scale to int16 PCM range

Tracks two deviation metrics: an EMA-smoothed estimate (`dev_ema_hz`) and peak-hold per reporting window (`dev_max_hz`). These are published alongside PSD results.

---

### 9. AM Demodulator — Basic (`am_radio.h`, `am_radio.c`)

Legacy AM chain: envelope detection (`|I + jQ| = sqrt(I² + Q²)`) → decimation → DC blocker → LPF → gain → int16 PCM. References `am_depth_state_t` for modulation depth metrics.

Not used in production — superseded by the local variant below. Retained for backward compatibility in the audio thread (which holds a pointer but routes to `am_radio_local`).

---

### 10. AM Demodulator — Local (`am_radio_local.h`, `am_radio_local.c`)

Production AM demodulator with three enhancements over the basic version:

1. **CIC decimator (order 2)** — cascaded integrator-comb filter for efficient sample rate reduction with better alias rejection than simple averaging
2. **Carrier mean tracker** — EMA-based running estimate of the envelope mean (DC carrier level), used for **normalization** — dividing the AC component by the carrier level yields a modulation index robust to signal strength variations
3. **RMS-based AGC** — per-sample adaptive gain with separate attack (fast reduction) and release (slow recovery) rates, bounded by configurable min/max gain limits

The pipeline is: envelope detection → CIC decimate → normalize (divide by carrier mean) → DC block → audio LPF → AGC → output gain → PCM16.

---

### 11. IQ IIR Filter (`iq_iir_filter.h`, `iq_iir_filter.c`)

Butterworth bandpass filter applied to complex IQ samples before demodulation. Implemented as a cascade of second-order sections (biquads) in Direct Form II Transposed structure.

Key characteristics:
- **Symmetric filtering**: separate state registers for I and Q channels, identical coefficients
- **Configurable order** (even, default 6): supported bandwidths are 200 kHz (FM) and 20 kHz (AM)
- **Optional DC notch**: a 1st-order DC blocker applied before the biquad cascade
- **Dynamic reconfiguration**: `iq_iir_filter_config()` recomputes coefficients when sample rate or bandwidth changes, reallocating if order changes
- **`iq_iir_filter_apply_inplace()`**: processes a `signal_iq_t` buffer through the full cascade

Used by the audio thread to suppress adjacent-channel interference before the demodulator.

---

### 12. Audio Streaming Context (`audio_stream_ctx.h`, `audio_stream_ctx.c`)

Aggregator struct (`audio_stream_ctx_t`) that binds together all audio-path resources:

| Field | Role |
|---|---|
| `fm_radio`, `am_radio` | Demodulator instances (owned by main, borrowed by audio thread) |
| `tcp_host`, `tcp_port` | Opus stream destination (from env vars, default `127.0.0.1:9000`) |
| `opus_sample_rate` / `channels` / `bitrate` / `complexity` / `vbr` / `frame_ms` | Opus encoder config (default 48 kHz mono, 32 kbps CBR, 20 ms frames) |
| `current_mode` (atomic) | Written by main thread on each request; read by audio thread |
| `current_fs_hz` (atomic) | Current IQ sample rate; used to configure decimation ratios and IIR filter |
| `iqf`, `iqf_cfg`, `iqf_ready` | IIR filter state and initialization flag |
| `fm_dev`, `am_depth` | Modulation quality metrics read by main thread for PSD replies |

**`audio_stream_ctx_defaults()`** initializes the struct with compile-time defaults, then overrides from environment variables (`AUDIO_TCP_HOST`, `AUDIO_TCP_PORT`, etc.) if present.

---

### 13. Opus Transmitter (`opus_tx.h`, `opus_tx.c`)

Encoder + TCP sender for PCM audio frames.

**`opus_tx_create(host, port, cfg)`** — allocates an Opus encoder with the given bitrate/complexity/VBR settings, resolves the hostname, connects a TCP socket, and configures `TCP_NODELAY`.

**`opus_tx_send_frame(tx, pcm, frame_samples)`** — encodes `frame_samples` of int16 PCM into an Opus packet, prepends a custom header (sequence number + payload length), and sends the complete frame over TCP. Returns `-1` on socket error (triggering reconnect).

Internally uses `send_all` semantics: loops `send()` until all bytes are transmitted, handling partial writes.

---

### 14. Network Audio Retry (`net_audio_retry.h`, `net_audio_retry.c`)

Resilience layer for the Opus TCP connection.

| Function | Behaviour |
|---|---|
| `connect_tcp_net_audio(host, port)` | `getaddrinfo` → `socket` → `connect` with configurable timeout + `SO_KEEPALIVE` |
| `send_all_net_audio(fd, buf, len)` | Loops `send()` handling partial writes and `EINTR`; returns 0 only when all bytes sent |
| `sleep_cancelable_ms(ms, flag)` | Sleeps in 50 ms slices, checking `*flag` each iteration for early exit |
| `ensure_tx_with_retry(ctx, ptx, flag)` | If `*ptx` is NULL, loops `opus_tx_create()` + 3 s sleep until success or `flag` goes false |

The audio thread calls `ensure_tx_with_retry` at the top of every iteration, so a broken TCP connection causes an automatic blocking reconnect without dropping the thread.

---

### 15. Utilities (`utils.h`, `utils.c`)

- **`getenv_c(key)`** — reads a key from the local `.env` file (plain text, not shell). Used to load `IPC_ADDR` and audio network config without environment variables.
- **`shm_add_to_persistent(key, value)`** — atomic write to `/dev/shm/persistent.json` using `flock` for mutual exclusion and `fsync` for durability. Used by the calibration routine to persist `ppm_error`.
- **`shm_consult_persistent(key)`** — atomic read from the same shared-memory store with shared lock.

---

## Main Application Flow (`rf/rf.c`)

### Entry and Initialization

`main()` sequences through:

1. **OpenMP tuning** — sets `OMP_WAIT_POLICY=PASSIVE` (yield CPU instead of spin), `OMP_PROC_BIND=FALSE` (let OS schedule), limits to 3 threads (cores − 1 on a Pi 5)
2. **I/O** — disables stdout/stderr buffering for real-time log visibility
3. **Signal handlers** — `SIGINT`/`SIGTERM` → set `keep_running = false`; `SIGPIPE` → ignored (prevents crash on broken TCP audio pipes)
4. **IPC** — reads `IPC_ADDR` from `.env` (default `ipc:///tmp/rf_engine`), creates ZMQ REP socket
5. **Hardware** — calls `hackrf_init()` with infinite retry (5 s sleep between attempts)
6. **Buffers** — allocates primary ring buffer (100 MB), audio ring buffer (~262 KB for 8 chunks of 16384 I/Q pairs)
7. **Radios** — heap-allocates `fm_radio_t` and `am_radio_local_t`, initializes `audio_stream_ctx_t`

### Main Loop

The loop is a **request-driven state machine**:

```
while (keep_running):
    req = zpair_recv(zmq_channel)     // 100 ms timeout

    if timeout:
        if idle > 15 s && not calibrating:
            close HackRF                // power/thermal saving
        continue

    parse_config_rf(buffer, &desired)   // JSON → DesiredCfg_t

    if desired.calibrate:
        ppm = calibrate_hackrf()        // → shared memory
        send json reply with ppm
        continue

    apply_runtime_request()             // audio on/off, find_params_psd()
    lazy_tune_hackrf()                  // only if freq/gain/ppm changed
    ensure_audio_thread_once()          // pthread_create on first request
    start_rx_if_stopped()               // hackrf_start_rx + rx_callback
    rb_discard_all()                    // drop stale pre-request IQ
    wait_iq_with_timeout(5s)            // condvar wait on rb_cond
    pacing_cooldown()                   // enforce cooldown_request interval
    dsp_pipeline()                      // IQ → signal → compensate → filter → PSD
    publish_results()                   // JSON reply with PSD + AM/FM metrics
```

### Rx Callback

`rx_callback` executes in the **HackRF driver thread** (high frequency, ~20 MHz sample rate means up to 20 million invocations/second). It does minimal work:

1. If `stop_streaming`, return immediately
2. `rb_write(rb, transfer->buffer, valid_length)` → primary buffer
3. If `audio_enabled` (atomic):
   - `rb_write(audio_rb, transfer->buffer, valid_length)` → audio buffer
   - `pthread_cond_signal(audio_rb_cond)` → wake audio thread
4. `pthread_cond_signal(rb_cond)` → wake main thread

No malloc, no blocking, no heavy computation. The `stop_streaming` flag is `volatile` for immediate visibility across threads.

### DSP Pipeline (per request)

1. **`load_iq_into_signal(linear_buffer, total_bytes, &sig)`** — int8→complex conversion, populates `signal_iq_t`
2. **`iq_compensation(&sig)`** — DC/phase/gain correction in-place
3. **Conditional channel filter** — `chan_filter_apply_inplace_abs()` if `filter_enabled` in the request
4. **PSD computation** — `execute_welch_psd()` or `execute_pfb_psd()` based on `method_psd`
5. **`publish_results()`** — builds JSON with `start_freq_hz`, `end_freq_hz`, `Pxx` array, and optionally `depth` (AM%) or `excursion_hz` (FM). Sends via `zpair_send()`.

### Audio Thread

Runs concurrently with the main loop. Consumes from `audio_rb`.

```
while (audio_thread_running):
    ensure_tx_with_retry()                    // connect/reconnect Opus TX
    wait for audio_rb ≥ AUDIO_CHUNK_SAMPLES   // 16384 IQ pairs
    read chunk → int8 to complex conversion
    optional IIR bandpass filter              // 200 kHz (FM) or 20 kHz (AM)
    demodulate: am_radio_local or fm_radio    // → int16 PCM
    accumulate PCM into Opus frame buffer     // 960 samples @ 48k/20ms
    when frame full: opus_tx_send_frame()     // encode + TCP send
    if send fails: destroy tx, sleep 2s, loop back to retry
```

Modulation quality metrics (AM depth, FM deviation) are updated per chunk and exposed to the main thread through atomic reads on `audio_ctx`.

### Calibration

`calibrate_hackrf()` is a self-contained three-stage procedure:

1. **Wideband sweep** — captures ~0.5 s of IQ at 20 MSps, computes a Welch PSD, identifies the top 6 peaks above `median + 5 dB` with minimum spacing
2. **FM pilot detection** — for each candidate, frequency-shifts the candidate to DC, FM-demodulates to audio, computes a second PSD, and searches the 18–20 kHz region for a stereo pilot tone. SNR is computed as `peak_power / median_power`. Candidates weaker than the strongest by >8 dB are rejected
3. **Fine symmetry search** — the best candidate is examined via a decimated baseband PSD. A cost function minimizes the asymmetry between upper and lower sidebands over a ±15 kHz range, yielding a PPM correction. A smoothing EMA (0.7/0.3 weight) and outlier guard (±80 PPM hard cap, ±20 PPM rejection from locked value) prevent single-run errors from corrupting the estimate

The result is stored to `/dev/shm/persistent.json` via `shm_add_to_persistent("ppm_error", ...)` for the Python orchestrator to pick up.

### Error Recovery

- **Session health check** — `ensure_hackrf_session_is_healthy()` reads the board ID and verifies streaming bit before each request. If either check fails, calls `invalidate_hackrf_state()` which stops RX, closes the device handle, resets ring buffers, and zeros `current_hw_cfg`
- **Device recovery** — `recover_hackrf()` makes 3 attempts spaced 1 s apart to re-open the device after a disconnect
- **Lazy device open** — the main loop opens the HackRF only when the first request arrives; idle timeout closes it after 15 s, avoiding continuous USB power draw
- **Acquisition timeout** — waiting for IQ data uses `pthread_cond_timedwait` with a 5 s deadline; on expiry the request is rejected with `acquisition_timeout` and the HackRF state is invalidated

### Shutdown Sequence

1. Set `audio_thread_running = false`, broadcast audio condvar
2. `pthread_join(audio_thread)` — wait for thread exit (may take a few seconds if blocked in TCP connect)
3. `zpair_close()` — graceful ZMQ disconnect
4. `rb_free()` — both ring buffers
5. `rf_workspace_release()` — DSP workspace and calibration workspace
6. HackRF: stop RX, close device, `hackrf_exit()`
7. Free radio instances, `chan_filter_free_cache()`, free IPC address string

---

## Key Design Patterns

| Pattern | Application |
|---|---|
| **Lock-free ring buffer** | Hot path (`rx_callback`) writes, consumers read. No mutex contention on the data path |
| **Condvar wakeup** | `pthread_cond_signal` from callback notifies main/audio threads when data is available, avoiding polling |
| **Workspace reuse** | `rf_workspace_ensure_*()` functions grow heap allocations on demand; never freed until shutdown. Avoids malloc in the DSP hot path |
| **Lazy tuning** | HackRF registers are only touched when the requested frequency, sample rate, gain, or PPM actually change from the current state |
| **Request-scoped capture** | `rb_discard_all()` before each acquisition guarantees the reply is built from samples captured *after* the request, not stale pre-request data |
| **Cooldown pacing** | A configurable inter-request delay prevents CPU saturation from rapid-fire PSD requests |
| **Atomic signaling** | `audio_enabled`, `calibration_running`, `current_mode`, `current_fs_hz` are atomic — safe for read/write across threads without a mutex |
| **Cancelable blocking** | `sleep_cancelable_ms()` and `ensure_tx_with_retry()` check a `volatile bool*` flag every 50 ms, enabling prompt thread shutdown |
| **Shared memory persistence** | Calibration results are written atomically to `/dev/shm/persistent.json` with file locking, mirroring the Python `ShmStore` pattern |
