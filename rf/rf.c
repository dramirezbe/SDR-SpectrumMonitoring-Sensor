#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include <stdio.h>
#include <stdbool.h>
#include <stdlib.h>
#include <unistd.h>
#include <math.h>
#include <string.h>
#include <inttypes.h>
#include <pthread.h>
#include <time.h>
#include <signal.h>
#include <complex.h>
#include <sys/time.h>
#include <errno.h>
#include <stdatomic.h>
#include <sys/socket.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <sys/types.h>
#include <libhackrf/hackrf.h>
#include <cjson/cJSON.h>

// Project Includes
#include "psd.h"
#include "datatypes.h" 
#include "sdr_HAL.h"     
#include "ring_buffer.h" 
#include "zmq_util.h" 
#include "utils.h"
#include "parser.h"
#include "chan_filter.h"
#include "audio_stream_ctx.h"
#include "am_radio_local.h"
#include "net_audio_retry.h"
#include "iq_iir_filter.h"
#include "opus_tx.h"

#ifndef NO_COMMON_LIBS
    #include "bacn_gpio.h"
#endif

/** * @defgroup rf_binary RF Binary
 * @brief Logic, Digital Signal Processing, and Audio streaming for the Radio module.
 * @{ 
 */

// =========================================================
// GLOBAL VARIABLES
// =========================================================

// ========================= IQ Channel Filter (rf_audio.c variables)
static int    IQ_FILTER_ENABLE        = 1;

// Recommended two-sided channel BW:

// AM voice-like channel BW ~10kHz (±5kHz)
static float  IQ_FILTER_BW_AM_HZ      = 20000.0f;

// Optional: apply same channel filter to IQ before PSD in FM/AM (default OFF to preserve current PSD behavior)
static int    IQ_FILTER_APPLY_TO_PSD  = 1;

zpair_t *zmq_channel = NULL;
hackrf_device* device = NULL;

ring_buffer_t rb;
ring_buffer_t audio_rb;
// This actually allocates the memory for the variable
atomic_bool audio_enabled = false;

volatile bool stop_streaming = true; 
volatile bool config_received = false; 
volatile bool keep_running = true;

DesiredCfg_t desired_config = {0};
PsdConfig_t psd_cfg = {0};
SDR_cfg_t hack_cfg = {0};
RB_cfg_t rb_cfg = {0};

// Track the ACTUAL hardware state for Lazy Tuning
SDR_cfg_t current_hw_cfg = {0};

pthread_t audio_thread;
volatile bool audio_thread_running = false;

pthread_mutex_t cfg_mutex = PTHREAD_MUTEX_INITIALIZER;

// =========================================================
// UTILITY FUNCTIONS
// =========================================================
// =========================================================
// HELPERS
static inline uint64_t now_ms(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (uint64_t)tv.tv_sec * 1000ULL + (tv.tv_usec / 1000ULL);
}

static inline void msleep_int(int ms) {
    if (ms <= 0) return;
    usleep((useconds_t)ms * 1000);
}

void handle_sigint(int sig) {
    (void)sig;
    keep_running = false;
}

int rx_callback(hackrf_transfer* transfer) {
    if (stop_streaming) return 0; 
    if (transfer->valid_length > 0) {
        rb_write(&rb, transfer->buffer, transfer->valid_length);
        if (atomic_load(&audio_enabled)) {
            rb_write(&audio_rb, transfer->buffer, transfer->valid_length);
        }
    }
    return 0;
}

int recover_hackrf(void) {
    printf("\n[RECOVERY] Initiating Hardware Reset sequence...\n");
    if (device != NULL) {
        stop_streaming = true;
        hackrf_stop_rx(device);
        usleep(200000); 
        hackrf_close(device);
        device = NULL;
    }

    int attempts = 0;
    while (attempts < 3 && keep_running) {
        usleep(1000000); 
        if (hackrf_open(&device) == HACKRF_SUCCESS) {
            printf("[RECOVERY] Device Re-opened successfully.\n");
            memset(&current_hw_cfg, 0, sizeof(SDR_cfg_t)); 
            return 0;
        }
        attempts++;
        fprintf(stderr, "[RECOVERY] Attempt %d failed.\n", attempts);
    }
    return -1;
}

void publish_results(double* psd_array, int length, SDR_cfg_t *local_hack, int rf_mode, float am_depth, float fm_dev) {
    if (!zmq_channel || !psd_array || length <= 0) return;
    
    cJSON *root = cJSON_CreateObject();
    double fs = local_hack->sample_rate;
    double start_freq = (double)local_hack->center_freq - (fs / 2.0);
    double end_freq   = (double)local_hack->center_freq + (fs / 2.0);
    
    cJSON_AddNumberToObject(root, "start_freq_hz", start_freq);
    cJSON_AddNumberToObject(root, "end_freq_hz", end_freq);
    
    if (rf_mode == FM_MODE) {
        cJSON_AddNumberToObject(root, "excursion_hz", (double)fm_dev);
    } else if (rf_mode == AM_MODE){
        cJSON_AddNumberToObject(root, "depth", (double)am_depth * 100.0);
    }
    
    cJSON_AddItemToObject(root, "Pxx", cJSON_CreateDoubleArray(psd_array, length));
    
    char *json_string = cJSON_PrintUnformatted(root); 
    if (json_string) {
        zpair_send(zmq_channel, json_string);
        free(json_string);
    }
    cJSON_Delete(root);
}

void on_command_received(const char *payload) {
    DesiredCfg_t temp_desired;
    SDR_cfg_t temp_hack;
    PsdConfig_t temp_psd;
    RB_cfg_t temp_rb;

    if (parse_config_rf(payload, &temp_desired) == 0) {
        printf("[RF]<<<<<zmq\n");

        //Enable or disable audio based on RF mode
        if (temp_desired.rf_mode == PSD_MODE) {
            atomic_store(&audio_enabled, false);
        } else {
            // If we were OFF and are turning ON, reset the buffer to ensure fresh audio
            if (!atomic_load(&audio_enabled)) {
                rb_reset(&audio_rb); 
            }
            atomic_store(&audio_enabled, true);
        }

        find_params_psd(temp_desired, &temp_hack, &temp_psd, &temp_rb);
        
        pthread_mutex_lock(&cfg_mutex);
        desired_config = temp_desired;
        hack_cfg = temp_hack;
        psd_cfg = temp_psd;
        rb_cfg = temp_rb;
        config_received = true; 
        pthread_mutex_unlock(&cfg_mutex);

        print_config_summary_DEPLOY(&desired_config, &hack_cfg, &psd_cfg, &rb_cfg);

        #ifndef NO_COMMON_LIBS
            select_ANTENNA(temp_desired.antenna_port);
        #else
            printf("[GPIO] selected port: %d\n", temp_desired.antenna_port);
        #endif
    }
}

void* audio_thread_fn(void* arg) {
    audio_stream_ctx_t *ctx = (audio_stream_ctx_t*)arg;
    if (!ctx || !ctx->fm_radio || !ctx->am_radio) {
        fprintf(stderr, "[AUDIO] FATAL: ctx or radios NULL\n");
        return NULL;
    }

    // sanity: Opus expects one of the standard rates; we use 48000
    if (!(ctx->opus_sample_rate == 8000  || ctx->opus_sample_rate == 12000 ||
          ctx->opus_sample_rate == 16000 || ctx->opus_sample_rate == 24000 ||
          ctx->opus_sample_rate == 48000)) {
        fprintf(stderr, "[AUDIO] FATAL: invalid opus_sample_rate=%d\n", ctx->opus_sample_rate);
        return NULL;
    }

    const int frame_samples = (ctx->opus_sample_rate * ctx->frame_ms) / 1000; // e.g., 960 @48k/20ms
    if (frame_samples <= 0) {
        fprintf(stderr, "[AUDIO] FATAL: invalid frame_samples\n");
        return NULL;
    }

    int8_t  *raw_iq_chunk = (int8_t*)malloc((size_t)AUDIO_CHUNK_SAMPLES * 2);
    int16_t *pcm_out      = (int16_t*)malloc((size_t)AUDIO_CHUNK_SAMPLES * sizeof(int16_t));

    signal_iq_t audio_sig;
    audio_sig.n_signal = AUDIO_CHUNK_SAMPLES;
    audio_sig.signal_iq = (double complex*)malloc((size_t)AUDIO_CHUNK_SAMPLES * sizeof(double complex));

    int16_t *pcm_accum = (int16_t*)malloc((size_t)frame_samples * sizeof(int16_t));
    int accum_len = 0;

    if (!raw_iq_chunk || !pcm_out || !audio_sig.signal_iq || !pcm_accum) {
        fprintf(stderr, "[AUDIO] FATAL: malloc failed\n");
        free(raw_iq_chunk);
        free(pcm_out);
        free(audio_sig.signal_iq);
        free(pcm_accum);
        return NULL;
    }

    opus_tx_t *tx = NULL;

    // local helper: (re)connect opus tx


    audio_thread_running = true;

    // track mode/fs changes to reconfig IQ filter cleanly
    int    last_mode = -1;
    double last_fs   = 0.0;

    // metrics reporter (added only for metrics)
    uint64_t last_metrics_ms = now_ms();
    const uint64_t METRICS_EVERY_MS = 500;

    while (audio_thread_running) {

        // Ensure TCP/Opus encoder is ready (infinite retries, 3s)
        if (ensure_tx_with_retry(ctx, &tx, &audio_thread_running) != 0) {
            // thread stopping
            break;
        }

        // Wait for enough IQ bytes
        if (rb_available(&audio_rb) < (size_t)(AUDIO_CHUNK_SAMPLES * 2)) {
            usleep(1000);
            continue;
        }

        // Drain one chunk
        rb_read(&audio_rb, raw_iq_chunk, AUDIO_CHUNK_SAMPLES * 2);

        // Convert int8 IQ -> complex double (normalized)
        for (int i = 0; i < AUDIO_CHUNK_SAMPLES; ++i) {
            double real = ((double)raw_iq_chunk[2*i]) / 128.0;
            double imag = ((double)raw_iq_chunk[2*i + 1]) / 128.0;
            audio_sig.signal_iq[i] = real + imag * I;
        }

        // Read current mode/fs (set by main thread)
        int mode = atomic_load(&ctx->current_mode);
        double fs_hz = atomic_load(&ctx->current_fs_hz);
        if (fs_hz <= 0.0) fs_hz = 2000000.0;

        // ===== IQ CHANNEL FILTER =====
        if (IQ_FILTER_ENABLE) {
            float bw = (mode == AM_MODE) ? IQ_FILTER_BW_AM_HZ : IQ_FILTER_BW_FM_HZ;

            ctx->iqf_cfg.type_filter  = BANDPASS_TYPE;
            ctx->iqf_cfg.order_fliter = IQ_FILTER_ORDER;
            ctx->iqf_cfg.bw_filter_hz = bw;

            // init or reconfig if mode/fs changed
            if (!ctx->iqf_ready) {
                if (iq_iir_filter_init(&ctx->iqf, fs_hz, &ctx->iqf_cfg, 1) == 0) {
                    ctx->iqf_ready = 1;
                    last_mode = mode;
                    last_fs = fs_hz;
                }
            } else {
                if (mode != last_mode || fabs(fs_hz - last_fs) > 1e-6) {
                    iq_iir_filter_config(&ctx->iqf, fs_hz, &ctx->iqf_cfg);
                    iq_iir_filter_reset(&ctx->iqf);
                    last_mode = mode;
                    last_fs = fs_hz;
                }
            }

            if (ctx->iqf_ready) {
                iq_iir_filter_apply_inplace(&ctx->iqf, &audio_sig);
            }
        }

        // ===== Demod IQ -> PCM (FM or AM) =====
        int samples_gen = 0;
        if (mode == AM_MODE) {
            samples_gen = am_radio_local_iq_to_pcm(ctx->am_radio, &audio_sig, pcm_out, &ctx->am_depth);
        } else {
            // default: FM
            // >>> FIX: pass metrics state + fs_demod <<<
            samples_gen = fm_radio_iq_to_pcm(
                ctx->fm_radio,
                &audio_sig,
                pcm_out,
                &ctx->fm_dev,
                (int)llround(fs_hz)
            );
        }

        // ===== metrics print (added only for metrics) =====
        uint64_t tnow = now_ms();
        if (tnow - last_metrics_ms >= METRICS_EVERY_MS) {
            last_metrics_ms = tnow;

            if (mode == AM_MODE) {
                float depth_pct = 100.0f * ctx->am_depth.depth_ema;
                if (isfinite(depth_pct)) {
                    fprintf(stderr, "[AM] depth=%.1f %%\n", depth_pct);
                }
            } else {
                float dev_ema = ctx->fm_dev.dev_ema_hz;
                float dev_pk  = ctx->fm_dev.dev_max_hz;
                if (isfinite(dev_ema) || isfinite(dev_pk)) {
                    fprintf(stderr, "[FM] dev_ema=%.1f Hz  dev_peak=%.1f Hz  fs=%d\n",
                            dev_ema, dev_pk, (int)llround(fs_hz));
                }
            }
        }

        if (samples_gen <= 0) continue;

        // Ensure TCP/Opus encoder is ready
        if (ensure_tx_with_retry(ctx, &tx, &audio_thread_running) != 0) {
            // Se solicitó detener el hilo o el programa
            break;
        }

        // Accumulate into exact Opus frames
        int idx = 0;
        while (idx < samples_gen) {
            int space = frame_samples - accum_len;
            int take  = samples_gen - idx;
            if (take > space) take = space;

            memcpy(&pcm_accum[accum_len], &pcm_out[idx], (size_t)take * sizeof(int16_t));
            accum_len += take;
            idx += take;

            if (accum_len == frame_samples) {
                if (opus_tx_send_frame(tx, pcm_accum, frame_samples) != 0) {
                    fprintf(stderr, "[AUDIO] WARN: opus_tx_send_frame failed. Reconnecting in 3s...\n");
                    opus_tx_destroy(tx);
                    tx = NULL;
                    accum_len = 0;
                    sleep_cancelable_ms(RECONNECT_DELAY_MS, &audio_thread_running);
                    break;
                }
                accum_len = 0;
            }
        }
    }

    if (tx) opus_tx_destroy(tx);

    if (ctx->iqf_ready) {
        iq_iir_filter_free(&ctx->iqf);
        ctx->iqf_ready = 0;
    }

    free(raw_iq_chunk);
    free(pcm_out);
    free(audio_sig.signal_iq);
    free(pcm_accum);
    return NULL;
}

/** @} */

// =========================================================
// MAIN EXECUTION
// =========================================================

int main() {
    signal(SIGINT, handle_sigint);
    signal(SIGTERM, handle_sigint);
    signal(SIGPIPE, SIG_IGN); // Added to prevent crash on broken TCP audio pipes

    char *ipc_addr = getenv_c("IPC_ADDR");
    if (!ipc_addr) ipc_addr = strdup("ipc:///tmp/rf_engine");
    
    printf("[RF] Starting Engine. IPC=%s\n", ipc_addr);

    zmq_channel = zpair_init(ipc_addr, on_command_received, 0);
    if (!zmq_channel) return 1;
    zpair_start(zmq_channel); 

    printf("[RF] Initializing HackRF Library...\n");
    while (hackrf_init() != HACKRF_SUCCESS) {
        fprintf(stderr, "[RF] Error: HackRF Init failed. Retrying in 5s...\n");
        sleep(5);
    }
    printf("[RF] HackRF Library Initialized.\n");

    // --- AUDIO & RING BUFFER INIT ---
    size_t FIXED_BUFFER_SIZE = 100 * 1024 * 1024; 
    rb_init(&rb, FIXED_BUFFER_SIZE);
    
    // Audio ring buffer initialization
    size_t AUDIO_BUFFER_SIZE = AUDIO_CHUNK_SAMPLES * 2 * 8;
    rb_init(&audio_rb, AUDIO_BUFFER_SIZE);

    // Audio resource allocation
    fm_radio_t *radio_ptr = (fm_radio_t*)malloc(sizeof(fm_radio_t));
    am_radio_local_t *am_ptr = (am_radio_local_t*)malloc(sizeof(am_radio_local_t));
    if (!radio_ptr || !am_ptr) {
        fprintf(stderr, "[RF] FATAL: malloc radio resources failed\n");
        return 1;
    }
    memset(radio_ptr, 0, sizeof(fm_radio_t));
    memset(am_ptr, 0, sizeof(am_radio_local_t));

    bool audio_thread_created = false;
    double last_radio_sample_rate = 0.0;

    // Audio streaming context setup
    audio_stream_ctx_t audio_ctx;
    audio_stream_ctx_defaults(&audio_ctx, radio_ptr, am_ptr);

    fprintf(stderr, "[AUDIO] Stream target TCP %s:%d (Opus sr=%d ch=%d)\n",
            audio_ctx.tcp_host, audio_ctx.tcp_port,
            audio_ctx.opus_sample_rate, audio_ctx.opus_channels);

    struct timespec last_activity_time;
    clock_gettime(CLOCK_MONOTONIC, &last_activity_time);

    SDR_cfg_t local_hack;
    RB_cfg_t local_rb;
    PsdConfig_t local_psd;
    DesiredCfg_t local_desired;

    while (keep_running) {
        // --- 1. IDLE / TIMEOUT MANAGEMENT (Preserved) ---
        if (!config_received) {
            struct timespec now;
            clock_gettime(CLOCK_MONOTONIC, &now);
            double elapsed = (now.tv_sec - last_activity_time.tv_sec) + 
                             (now.tv_nsec - last_activity_time.tv_nsec) / 1e9;

            if (elapsed >= 15.0 && device != NULL) {
                printf("[RF] Idle timeout (%.1fs). Closing radio.\n", elapsed);
                stop_streaming = true;
                hackrf_stop_rx(device);
                usleep(100000); 
                hackrf_close(device);
                device = NULL;
                memset(&current_hw_cfg, 0, sizeof(SDR_cfg_t)); 
            }
            usleep(10000); 
            continue;
        }

        // --- 2. SNAPSHOT CONFIG ---
        pthread_mutex_lock(&cfg_mutex);
        memcpy(&local_hack, &hack_cfg, sizeof(SDR_cfg_t));
        memcpy(&local_rb, &rb_cfg, sizeof(RB_cfg_t));
        memcpy(&local_psd, &psd_cfg, sizeof(PsdConfig_t));
        memcpy(&local_desired, &desired_config, sizeof(DesiredCfg_t));
        
        // Audio Logic: Update audio thread mode/fs atomics
        atomic_store(&audio_ctx.current_mode, (int)local_desired.rf_mode);
        atomic_store(&audio_ctx.current_fs_hz, (double)local_hack.sample_rate);

        config_received = false; 
        pthread_mutex_unlock(&cfg_mutex);
        clock_gettime(CLOCK_MONOTONIC, &last_activity_time);

        // --- 3. HARDWARE PREP ---
        if (device == NULL) {
            if (hackrf_open(&device) != HACKRF_SUCCESS) {
                recover_hackrf();
                continue;
            }
        }

        bool needs_tune = (local_hack.center_freq != current_hw_cfg.center_freq ||
                           local_hack.sample_rate != current_hw_cfg.sample_rate ||
                           local_hack.lna_gain    != current_hw_cfg.lna_gain    ||
                           local_hack.vga_gain    != current_hw_cfg.vga_gain);

        if (needs_tune) {
            printf("[HAL] Tuning: %" PRIu64 " Hz | LNA: %u | VGA: %u\n", 
                    local_hack.center_freq, local_hack.lna_gain, local_hack.vga_gain);
            hackrf_apply_cfg(device, &local_hack);
            memcpy(&current_hw_cfg, &local_hack, sizeof(SDR_cfg_t));
            
            usleep(150000); 
            rb_reset(&rb); 
            rb_reset(&audio_rb); // Also reset audio buffer on tune
        }

        // --- AUDIO THREAD & RADIO INIT ---
        // Initialize or re-init radios only if sample_rate changed
        if (!audio_thread_created || fabs(last_radio_sample_rate - local_hack.sample_rate) > 1e-6) {
            fm_radio_init(radio_ptr, local_hack.sample_rate, audio_ctx.opus_sample_rate, 75);
            am_radio_local_init(am_ptr, local_hack.sample_rate, audio_ctx.opus_sample_rate);
            last_radio_sample_rate = local_hack.sample_rate;

            // Reset metrics window state
            memset(&audio_ctx.fm_dev, 0, sizeof(audio_ctx.fm_dev));
            memset(&audio_ctx.am_depth, 0, sizeof(audio_ctx.am_depth));
            audio_ctx.am_depth.env_min = 1e9f;
            audio_ctx.am_depth.report_samples = (uint32_t)audio_ctx.opus_sample_rate;
        }

        // Start audio thread once
        if (!audio_thread_created) {
            if (pthread_create(&audio_thread, NULL, audio_thread_fn, (void*)&audio_ctx) == 0) {
                audio_thread_created = true;
            } else {
                fprintf(stderr, "[RF] Warning: failed to create audio thread\n");
            }
        }

        if (stop_streaming) {
            rb_reset(&rb);
            rb_reset(&audio_rb);
            stop_streaming = false;
            if (hackrf_start_rx(device, rx_callback, NULL) != HACKRF_SUCCESS) {
                recover_hackrf();
                continue;
            }
        }

        // --- 4. DATA ACQUISITION ---
        int safety_timeout = 500; 
        while (safety_timeout > 0 && keep_running) {
            if (rb_available(&rb) >= local_rb.total_bytes) break; 
            usleep(10000); 
            safety_timeout--;
        }

        if (safety_timeout <= 0 && keep_running) {
            fprintf(stderr, "[RF] Error: Acquisition Timeout.\n");
            recover_hackrf();
            continue;
        }

        // --- 5. PROCESSING WITH SAFETY CHECKS ---
        int8_t* linear_buffer = malloc(local_rb.total_bytes);
        if (linear_buffer) {
            rb_read(&rb, linear_buffer, local_rb.total_bytes);
            signal_iq_t* sig = load_iq_from_buffer(linear_buffer, local_rb.total_bytes);
            
            if (sig) {
                iq_compensation(sig);
                double* freq = malloc(local_psd.nperseg * sizeof(double));
                double* psd = malloc(local_psd.nperseg * sizeof(double));

                if (freq && psd) {
                    if (local_desired.filter_enabled) {
                        chan_filter_apply_inplace_abs(sig, &local_desired.filter_cfg, 
                                                      local_hack.center_freq, local_hack.sample_rate);
                    }

                    if (local_desired.method_psd == PFB) execute_pfb_psd(sig, &local_psd, freq, psd);
                    else execute_welch_psd(sig, &local_psd, freq, psd);
                    
                    publish_results(
                        psd, 
                        local_psd.nperseg, 
                        &local_hack, 
                        (int)local_desired.rf_mode, 
                        audio_ctx.am_depth.depth_ema, 
                        audio_ctx.fm_dev.dev_ema_hz
                    );
                } else {
                    fprintf(stderr, "[RF] Error: PSD buffer allocation failed.\n");
                }

                if (freq) free(freq);
                if (psd) free(psd);
                free_signal_iq(sig);
            } else {
                fprintf(stderr, "[RF] Error: Failed to load IQ signal from buffer.\n");
            }
            free(linear_buffer);
        } else {
            fprintf(stderr, "[RF] Error: Linear buffer allocation failed (%zu bytes).\n", local_rb.total_bytes);
        }

        clock_gettime(CLOCK_MONOTONIC, &last_activity_time);
    }

    // --- CLEANUP ---
    printf("[RF] Shutting down...\n");
    audio_thread_running = false; // Flag for audio thread to exit
    if (audio_thread_created) pthread_join(audio_thread, NULL);
    
    zpair_close(zmq_channel);
    rb_free(&rb);
    rb_free(&audio_rb);
    
    if (device) { 
        hackrf_stop_rx(device); 
        hackrf_close(device); 
    }
    hackrf_exit();
    
    if (ipc_addr) free(ipc_addr);
    if (radio_ptr) free(radio_ptr);
    if (am_ptr) free(am_ptr);
    chan_filter_free_cache();
    
    return 0;
}