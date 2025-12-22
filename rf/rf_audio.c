// rf.c  -- updated: skip re-applying identical SDR config to avoid audio interruption
#define _GNU_SOURCE

#include <stdio.h>
#include <stdbool.h>
#include <stdlib.h>
#include <unistd.h>
#include <math.h>
#include <string.h>
#include <inttypes.h>
#include <pthread.h>
#include <signal.h>
#include <complex.h>
#include <sys/time.h>

#include <libhackrf/hackrf.h>
#include <cjson/cJSON.h>

// Project Includes
#include "psd.h"
#include "datatypes.h"
#include "sdr_HAL.h"
#include "ring_buffer.h"
#include "zmq_util.h"
#include "utils.h"
#include "fm_radio.h"

#ifndef NO_COMMON_LIBS
    #include "bacn_gpio.h"
#endif

// ========================= Audio & PSD constants
#define AUDIO_CHUNK_SAMPLES 16384
#define PSD_SAMPLES_TOTAL   2097152
#define AUDIO_FS            48000

// =========================================================
// GLOBALS
zpair_t *zmq_channel = NULL;
hackrf_device* device = NULL;

// Two ring buffers:
//   rb         = large buffer used for acquisition/full-PSD (main thread reads)
//   audio_rb   = small buffer used only by audio thread (audio thread reads)
ring_buffer_t rb;
ring_buffer_t audio_rb;

volatile bool config_received = false;

DesiredCfg_t desired_config = {0};
PsdConfig_t psd_cfg = {0};
SDR_cfg_t hack_cfg = {0};
RB_cfg_t rb_cfg = {0};

// Audio thread control
pthread_t audio_thread;
volatile bool audio_thread_running = false;

// Track whether RX is currently running and last applied config
static bool rx_running = false;
static SDR_cfg_t last_applied_cfg;
static bool last_cfg_valid = false;

// Forward decls
void publish_results(double*, double*, int, SDR_cfg_t*);
void on_command_received(const char *payload);

// =========================================================
// HELPERS
static inline uint64_t now_ms(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (uint64_t)tv.tv_sec * 1000ULL + (tv.tv_usec / 1000ULL);
}

/** Compare relevant fields of SDR config to decide if reconfig is required */
static bool sdr_cfg_equal(const SDR_cfg_t *a, const SDR_cfg_t *b) {
    if (!a || !b) return false;
    if (a->center_freq != b->center_freq) return false;
    if (a->lna_gain != b->lna_gain) return false;
    if (a->vga_gain != b->vga_gain) return false;
    if (a->amp_enabled != b->amp_enabled) return false;
    if (a->ppm_error != b->ppm_error) return false;
    /* compare sample rate with small tolerance */
    if (fabs(a->sample_rate - b->sample_rate) > 1e-6) return false;
    return true;
}

// =========================================================
// RX CALLBACK (duplicate incoming bytes to both ring buffers)
int rx_callback(hackrf_transfer* transfer) {
    if (transfer->valid_length > 0) {
        rb_write(&rb, transfer->buffer, transfer->valid_length);
        rb_write(&23456, transfer->buffer, transfer->valid_length);
    }
    return 0;
}

int recover_hackrf(void) {
    printf("\n[RECOVERY] Initiating Hardware Reset sequence...\n");
    if (device != NULL) {
        if (rx_running) {
            hackrf_stop_rx(device);
            rx_running = false;
        }
        hackrf_close(device);
        device = NULL;
    }

    int attempts = 0;
    while (attempts < 3) {
        usleep(500000);
        int status = hackrf_open(&device);
        if (status == HACKRF_SUCCESS) {
            printf("[RECOVERY] Device Re-opened successfully.\n");
            return 0;
        }
        attempts++;
        fprintf(stderr, "[RECOVERY] Attempt %d failed.\n", attempts);
    }
    return -1;
}

// =========================================================
// PUBLISH (unchanged)
void publish_results(double* freq_array, double* psd_array, int length, SDR_cfg_t *local_hack) {
    if (!zmq_channel || !freq_array || !psd_array || length <= 0) return;
    cJSON *root = cJSON_CreateObject();
    double start_abs = freq_array[0] + (double)local_hack->center_freq;
    double end_abs   = freq_array[length-1] + (double)local_hack->center_freq;
    cJSON_AddNumberToObject(root, "start_freq_hz", start_abs);
    cJSON_AddNumberToObject(root, "end_freq_hz", end_abs);
    cJSON *pxx_array = cJSON_CreateDoubleArray(psd_array, length);
    cJSON_AddItemToObject(root, "Pxx", pxx_array);
    char *json_string = cJSON_PrintUnformatted(root);
    zpair_send(zmq_channel, json_string);
    free(json_string);
    cJSON_Delete(root);
}

// =========================================================
// ZMQ CALLBACK (unchanged)
void on_command_received(const char *payload) {
    printf("\n>>> [RF] Received Command Payload.\n");
    memset(&desired_config, 0, sizeof(DesiredCfg_t));
    if (parse_config_rf(payload, &desired_config) == 0) {
        find_params_psd(desired_config, &hack_cfg, &psd_cfg, &rb_cfg);
        print_config_summary(&desired_config, &hack_cfg, &psd_cfg, &rb_cfg);
        #ifndef NO_COMMON_LIBS
            select_ANTENNA(desired_config.antenna_port);
        #else
            printf("[GPIO] selected port: %d\n", desired_config.antenna_port);
        #endif
        config_received = true;
    } else {
        fprintf(stderr, ">>> [PARSER] Failed to parse JSON configuration.\n");
    }
}

// =========================================================
// AUDIO THREAD: drains audio_rb, converts IQ->PCM, writes to aplay
void* audio_thread_fn(void* arg) {
    fm_radio_t *radio = (fm_radio_t*)arg;

    int8_t *raw_iq_chunk = malloc(AUDIO_CHUNK_SAMPLES * 2);
    int16_t *pcm_out = malloc(AUDIO_CHUNK_SAMPLES * sizeof(int16_t));
    signal_iq_t audio_sig;
    audio_sig.n_signal = AUDIO_CHUNK_SAMPLES;
    audio_sig.signal_iq = malloc(AUDIO_CHUNK_SAMPLES * sizeof(double complex));

    FILE *audio_pipe = NULL;
    char audio_cmd[128];
    snprintf(audio_cmd, sizeof(audio_cmd), "aplay -r %d -f S16_LE -c 1 -t raw 2>/dev/null", AUDIO_FS);
    audio_pipe = popen(audio_cmd, "w");
    if (!audio_pipe) {
        fprintf(stderr, "[AUDIO] Warning: failed to open aplay pipe\n");
    }

    audio_thread_running = true;
    while (audio_thread_running) {
        if (rb_available(&audio_rb) >= AUDIO_CHUNK_SAMPLES * 2) {
            rb_read(&audio_rb, raw_iq_chunk, AUDIO_CHUNK_SAMPLES * 2);
            for (int i = 0; i < AUDIO_CHUNK_SAMPLES; ++i) {
                double real = ((double)raw_iq_chunk[2*i]) / 128.0;
                double imag = ((double)raw_iq_chunk[2*i + 1]) / 128.0;
                audio_sig.signal_iq[i] = real + imag * I;
            }
            if (radio) {
                int samples_gen = fm_radio_iq_to_pcm(radio, &audio_sig, pcm_out);
                if (samples_gen > 0 && audio_pipe) {
                    fwrite(pcm_out, sizeof(int16_t), samples_gen, audio_pipe);
                    fflush(audio_pipe);
                }
            }
            continue;
        }
        usleep(1000);
    }

    if (audio_pipe) pclose(audio_pipe);
    if (raw_iq_chunk) free(raw_iq_chunk);
    if (pcm_out) free(pcm_out);
    if (audio_sig.signal_iq) free(audio_sig.signal_iq);
    return NULL;
}

// =========================================================
// MAIN
int main() {
    char *raw_verbose = getenv_c("VERBOSE");
    bool verbose_mode = (raw_verbose != NULL && strcmp(raw_verbose, "true") == 0);
    if (raw_verbose) free(raw_verbose);

    char *ipc_addr = getenv_c("IPC_ADDR");
    if (!ipc_addr) ipc_addr = strdup("ipc:///tmp/rf_engine");

    printf("[RF] Starting. IPC=%s, VERBOSE=%d\n", ipc_addr, verbose_mode);

    zmq_channel = zpair_init(ipc_addr, on_command_received, verbose_mode ? 1 : 0);
    if (!zmq_channel) {
        fprintf(stderr, "[RF] FATAL: Failed to initialize ZMQ at %s\n", ipc_addr);
        if (ipc_addr) free(ipc_addr);
        return 1;
    }
    zpair_start(zmq_channel);

    // Init HackRF
    printf("[RF] Initializing HackRF Library...\n");
    while (hackrf_init() != HACKRF_SUCCESS) {
        fprintf(stderr, "[RF] Error: HackRF Init failed. Retrying in 5s...\n");
        sleep(5);
    }
    printf("[RF] HackRF Library Initialized.\n");

    // Open device
    while (hackrf_open(&device) != HACKRF_SUCCESS) {
        fprintf(stderr, "[RF] Warning: Initial Open failed. Retrying in 5s...\n");
        sleep(5);
    }
    printf("[RF] HackRF Device Opened.\n");

    // Initialize BOTH ring buffers
    size_t FIXED_BUFFER_SIZE = 100 * 1024 * 1024;
    rb_init(&rb, FIXED_BUFFER_SIZE);
    size_t AUDIO_BUFFER_SIZE = AUDIO_CHUNK_SAMPLES * 2 * 8;
    rb_init(&audio_rb, AUDIO_BUFFER_SIZE);

    printf("[RF] Ring Buffers: big=%zu MB, audio=%zu KB\n",
           FIXED_BUFFER_SIZE / (1024*1024), AUDIO_BUFFER_SIZE / 1024);

    bool needs_recovery = false;

    // Local copies
    SDR_cfg_t local_hack_cfg;
    RB_cfg_t local_rb_cfg;
    PsdConfig_t local_psd_cfg;
    DesiredCfg_t local_desired_cfg;

    int8_t *linear_buffer = NULL;
    double *f_axis = NULL;
    double *p_vals = NULL;

    // audio resources
    fm_radio_t *radio_ptr = malloc(sizeof(fm_radio_t));
    memset(radio_ptr, 0, sizeof(fm_radio_t));
    bool audio_thread_created = false;
    double last_radio_sample_rate = 0.0;

    while (1) {
        if (!config_received) {
            usleep(50000);
            continue;
        }

        if (device == NULL) { needs_recovery = true; goto error_handler; }

        /* Snapshot global config structs (atomically used below) */
        memcpy(&local_hack_cfg, &hack_cfg, sizeof(SDR_cfg_t));
        memcpy(&local_rb_cfg, &rb_cfg, sizeof(RB_cfg_t));
        memcpy(&local_psd_cfg, &psd_cfg, sizeof(PsdConfig_t));
        memcpy(&local_desired_cfg, &desired_config, sizeof(DesiredCfg_t));
        config_received = false;

        if (local_rb_cfg.total_bytes > rb.size) {
            printf("[RF] Error: Request bytes (%zu) exceeds buffer size!\n", local_rb_cfg.total_bytes);
            continue;
        }

        /* re-alloc PSD arrays */
        if (f_axis) free(f_axis);
        if (p_vals) free(p_vals);
        f_axis = malloc(local_psd_cfg.nperseg * sizeof(double));
        p_vals  = malloc(local_psd_cfg.nperseg * sizeof(double));

        // If RX not running yet -> apply cfg and start RX
        if (!rx_running) {
            hackrf_apply_cfg(device, &local_hack_cfg);
            if (hackrf_start_rx(device, rx_callback, NULL) != HACKRF_SUCCESS) {
                fprintf(stderr, "[RF] Error: hackrf_start_rx failed on initial start.\n");
                needs_recovery = true; goto error_handler;
            }
            rx_running = true;
            last_applied_cfg = local_hack_cfg;
            last_cfg_valid = true;
        } else {
            // If RX running and config differs from last applied -> apply new cfg (but do not restart RX)
            if (!last_cfg_valid || !sdr_cfg_equal(&local_hack_cfg, &last_applied_cfg)) {
                printf("[RF] New SDR config differs from last - applying.\n");
                hackrf_apply_cfg(device, &local_hack_cfg);
                last_applied_cfg = local_hack_cfg;
                last_cfg_valid = true;
            } else {
                // identical config -> skip hackrf_apply_cfg() to avoid interruption
                // leave rx_running true and do not stop/start device
                // We still proceed to wait for rb to fill and run PSD/publish as usual.
                // This prevents audio interruption on repeated identical configs.
            }
        }

        // Initialize or re-init FM radio only if sample_rate changed
        if (!audio_thread_created || fabs(last_radio_sample_rate - local_hack_cfg.sample_rate) > 1e-6) {
            fm_radio_init(radio_ptr, local_hack_cfg.sample_rate, AUDIO_FS, 75);
            last_radio_sample_rate = local_hack_cfg.sample_rate;
        }

        // Start audio thread once (it will keep running and drain audio_rb)
        if (!audio_thread_created) {
            if (pthread_create(&audio_thread, NULL, audio_thread_fn, (void*)radio_ptr) == 0) {
                audio_thread_created = true;
            } else {
                fprintf(stderr, "[RF] Warning: failed to create audio thread\n");
            }
        }

        // Wait until big buffer has filled (do NOT stop RX) - time-based timeout
        uint64_t start_ms = now_ms();
        const uint64_t timeout_ms = 5000;
        bool bigbuffer_full = false;

        while (now_ms() - start_ms < timeout_ms) {
            if (rb_available(&rb) >= local_rb_cfg.total_bytes) { bigbuffer_full = true; break; }
            usleep(5000);
        }

        if (!bigbuffer_full) {
            fprintf(stderr, "[RF] Error: Acquisition Timeout.\n");
            needs_recovery = true;
            goto error_handler;
        }

        // Read linear buffer for full-band PSD while RX remains running
        linear_buffer = malloc(local_rb_cfg.total_bytes);
        if (linear_buffer) {
            rb_read(&rb, linear_buffer, local_rb_cfg.total_bytes);
            signal_iq_t *sig = load_iq_from_buffer(linear_buffer, local_rb_cfg.total_bytes);

            double *freq = malloc(local_psd_cfg.nperseg * sizeof(double));
            double *psd = malloc(local_psd_cfg.nperseg * sizeof(double));

            if (sig && freq && psd) {
                execute_welch_psd(sig, &local_psd_cfg, freq, psd);
                scale_psd(psd, local_psd_cfg.nperseg, local_desired_cfg.scale);

                double half_span = local_desired_cfg.span / 2.0;
                int start_idx = 0, end_idx = local_psd_cfg.nperseg - 1;
                for (int i = 0; i < local_psd_cfg.nperseg; ++i) { if (freq[i] >= -half_span) { start_idx = i; break; } }
                for (int i = start_idx; i < local_psd_cfg.nperseg; ++i) {
                    if (freq[i] > half_span) { end_idx = i - 1; break; }
                    end_idx = i;
                }
                int valid_len = end_idx - start_idx + 1;
                if (valid_len > 0) publish_results(&freq[start_idx], &psd[start_idx], valid_len, &local_hack_cfg);
                else printf("[RF] Warning: Span resulted in 0 bins.\n");
            }

            free(linear_buffer);
            if (freq) free(freq);
            if (psd) free(psd);
            free_signal_iq(sig);
        }

        continue;

        error_handler:
        // Try to recover hardware
        if (rx_running && device) {
            hackrf_stop_rx(device);
            rx_running = false;
        }
        if (needs_recovery) {
            recover_hackrf();
            needs_recovery = false;
            last_cfg_valid = false; // force reapply on next good config
        }
    }

    // Cleanup (unreachable normally)
    audio_thread_running = false;
    if (audio_thread_created) pthread_join(audio_thread, NULL);
    if (radio_ptr) free(radio_ptr);
    if (f_axis) free(f_axis);
    if (p_vals) free(p_vals);
    zpair_close(zmq_channel);
    rb_free(&rb);
    rb_free(&audio_rb);
    if (ipc_addr) free(ipc_addr);
    return 0;
}
