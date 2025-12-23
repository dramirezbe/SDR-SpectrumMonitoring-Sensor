#define _GNU_SOURCE

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

#ifndef NO_COMMON_LIBS
    #include "bacn_gpio.h"
#endif

// =========================================================
// GLOBAL VARIABLES
// =========================================================

zpair_t *zmq_channel = NULL;
hackrf_device* device = NULL;
ring_buffer_t rb;

volatile bool stop_streaming = true; 
volatile bool config_received = false; 
volatile bool keep_running = true;

DesiredCfg_t desired_config = {0};
PsdConfig_t psd_cfg = {0};
SDR_cfg_t hack_cfg = {0};
RB_cfg_t rb_cfg = {0};

// Track the ACTUAL hardware state for Lazy Tuning
SDR_cfg_t current_hw_cfg = {0};

pthread_mutex_t cfg_mutex = PTHREAD_MUTEX_INITIALIZER;

// =========================================================
// UTILITY FUNCTIONS
// =========================================================

void handle_sigint(int sig) {
    (void)sig;
    keep_running = false;
}

int rx_callback(hackrf_transfer* transfer) {
    if (stop_streaming) return 0; 
    rb_write(&rb, transfer->buffer, transfer->valid_length);
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

void publish_results(double* psd_array, int length, SDR_cfg_t *local_hack) {
    if (!zmq_channel || !psd_array || length <= 0) return;
    cJSON *root = cJSON_CreateObject();
    double fs = local_hack->sample_rate;
    double start_freq = (double)local_hack->center_freq - (fs / 2.0);
    double end_freq   = (double)local_hack->center_freq + (fs / 2.0);
    cJSON_AddNumberToObject(root, "start_freq_hz", start_freq);
    cJSON_AddNumberToObject(root, "end_freq_hz", end_freq);
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

// =========================================================
// MAIN EXECUTION
// =========================================================

int main() {
    signal(SIGINT, handle_sigint);
    signal(SIGTERM, handle_sigint);

    char *ipc_addr = getenv_c("IPC_ADDR");
    if (!ipc_addr) ipc_addr = strdup("ipc:///tmp/rf_engine");
    
    printf("[RF] Starting Engine. IPC=%s\n", ipc_addr);

    zmq_channel = zpair_init(ipc_addr, on_command_received, 0);
    if (!zmq_channel) return 1;
    zpair_start(zmq_channel); 

    if (hackrf_init() != HACKRF_SUCCESS) return 1;

    size_t FIXED_BUFFER_SIZE = 100 * 1024 * 1024; 
    rb_init(&rb, FIXED_BUFFER_SIZE);

    struct timespec last_activity_time;
    clock_gettime(CLOCK_MONOTONIC, &last_activity_time);

    SDR_cfg_t local_hack;
    RB_cfg_t local_rb;
    PsdConfig_t local_psd;
    DesiredCfg_t local_desired;

    while (keep_running) {
        // --- 1. IDLE / TIMEOUT MANAGEMENT ---
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
        config_received = false; // Reset inside mutex to prevent race condition
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
            // Fixed formatting string
            printf("[HAL] Tuning: %" PRIu64 " Hz | LNA: %u | VGA: %u\n", 
                    local_hack.center_freq, local_hack.lna_gain, local_hack.vga_gain);
            hackrf_apply_cfg(device, &local_hack);
            memcpy(&current_hw_cfg, &local_hack, sizeof(SDR_cfg_t));
            
            // Allow hardware to settle before flushing buffer
            usleep(150000); 
            rb_reset(&rb); 
        }

        if (stop_streaming) {
            rb_reset(&rb);
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
                    
                    publish_results(psd, local_psd.nperseg, &local_hack);
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
    zpair_close(zmq_channel);
    rb_free(&rb);
    if (device) { 
        hackrf_stop_rx(device); 
        hackrf_close(device); 
    }
    hackrf_exit();
    if (ipc_addr) free(ipc_addr);
    chan_filter_free_cache();
    
    return 0;
}