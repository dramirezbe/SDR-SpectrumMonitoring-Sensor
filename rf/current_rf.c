/**
 * @file rf.c
 * @brief Continuous Headless PSD Analyzer (With Span Logic)
 */

#define _GNU_SOURCE

#include <stdio.h>
#include <stdbool.h>
#include <stdlib.h>
#include <unistd.h>
#include <stdbool.h>
#include <math.h>
#include <string.h>
#include <inttypes.h>

#include <libhackrf/hackrf.h>
#include <cjson/cJSON.h>

#include "psd.h"
#include "datatypes.h" 
#include "sdr_HAL.h"     
#include "ring_buffer.h" 
#include "zmq_util.h"
#include "consumer.h"
#include "utils.h"

#ifndef NO_COMMON_LIBS
    #include "bacn_gpio.h"
#endif

// =========================================================
// GLOBAL VARIABLES
// =========================================================
hackrf_device* device = NULL;
ring_buffer_t rb;
zpub_t *publisher = NULL;
bool *verbose = NULL; 

volatile bool stop_streaming = true; 
volatile bool config_received = false; 

DesiredCfg_t desired_config = {0};
PsdConfig_t psd_cfg = {0};
SDR_cfg_t hack_cfg = {0};
RB_cfg_t rb_cfg = {0};

// =========================================================
// CALLBACKS
// =========================================================

//Consumers
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
        usleep(100000);
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
    }
    return -1;
}

void publish_results(double* freq_array, double* psd_array, int length, SDR_cfg_t *local_hack) {
    if (!publisher || !freq_array || !psd_array || length <= 0) return;

    cJSON *root = cJSON_CreateObject();
    
    // Math: freq_array is relative to DC (-Span/2 to +Span/2)
    // Add center freq to get absolute RF values
    double start_abs = freq_array[0] + (double)local_hack->center_freq;
    double end_abs   = freq_array[length-1] + (double)local_hack->center_freq;

    cJSON_AddNumberToObject(root, "start_freq_hz", start_abs);
    cJSON_AddNumberToObject(root, "end_freq_hz", end_abs);

    cJSON *pxx_array = cJSON_CreateDoubleArray(psd_array, length);
    cJSON_AddItemToObject(root, "Pxx", pxx_array);

    char *json_string = cJSON_PrintUnformatted(root); 
    zpub_publish(publisher, "data", json_string);
    printf("[ZMQ] Published results (%d bins)\n", length);

    free(json_string);
    cJSON_Delete(root);
}

void handle_psd_message(const char *payload) {
    printf("\n>>> [ZMQ] Received Command Payload.\n");
    memset(&desired_config, 0, sizeof(DesiredCfg_t));

    if (parse_config_rf(payload, &desired_config) == 0) {
        find_params_psd(desired_config, &hack_cfg, &psd_cfg, &rb_cfg);
        print_config_summary(&desired_config, &hack_cfg, &psd_cfg, &rb_cfg);

        #ifndef NO_COMMON_LIBS
            select_ANTENNA(desired_config.antenna_port);
        #else
            printf("[GPIOdummy] selected port: %d\n", desired_config.antenna_port);
        #endif

        config_received = true; 
    } else {
        fprintf(stderr, ">>> [PARSER] Failed to parse JSON configuration.\n");
    }
}

// =========================================================
// MAIN
// =========================================================

int main() {
    // 1. Environment & Config Setup
    char *raw_verbose = getenv_c("VERBOSE");
    bool verbose = (raw_verbose != NULL && strcmp(raw_verbose, "true") == 0);
    if (raw_verbose != NULL) {
        free(raw_verbose);
    }

    // 2. ZMQ Initialization
    zsub_t *sub = zsub_init("acquire", handle_psd_message);
    if (!sub) return 1;
    zsub_start(sub);

    publisher = zpub_init();
    if (!publisher) return 1;

    // 3. Robust HackRF Library Initialization
    // We loop infinitely until the library initializes successfully.
    printf("[RF] Initializing HackRF Library...\n");
    while (hackrf_init() != HACKRF_SUCCESS) {
        fprintf(stderr, "[RF] Error: Failed to init HackRF Main Library. Retrying in 5 seconds...\n");
        sleep(5); // Wait 5 seconds before retrying
    }
    printf("[RF] HackRF Library Initialized successfully.\n");

    // 4. Device Opening (Non-blocking attempt)
    // We attempt to open now, but if it fails, the main loop handles recovery later.
    if (hackrf_open(&device) != HACKRF_SUCCESS) {
        fprintf(stderr, "[RF] Warning: Initial Device Open failed. Will retry in main loop.\n");
    }

    // 5. Buffer Allocation
    size_t FIXED_BUFFER_SIZE = 100 * 1024 * 1024;
    rb_init(&rb, FIXED_BUFFER_SIZE);
    printf("[RF] Persistent Ring Buffer Initialized (%zu MB)\n", FIXED_BUFFER_SIZE / (1024*1024));

    bool needs_recovery = false; 

    // 6. Local Configuration Containers
    SDR_cfg_t local_hack_cfg;
    RB_cfg_t local_rb_cfg;
    PsdConfig_t local_psd_cfg;
    DesiredCfg_t local_desired_cfg;

    // 7. Main Execution Loop
    while (1) {
        // A. Wait for Configuration via ZMQ
        if (!config_received) {
            usleep(10000); 
            continue;
        }

        // B. Check Device Health before proceeding
        if (device == NULL) {
            needs_recovery = true;
            goto error_handler;
        }

        // C. Snapshot Configuration (Thread safety)
        memcpy(&local_hack_cfg, &hack_cfg, sizeof(SDR_cfg_t));
        memcpy(&local_rb_cfg, &rb_cfg, sizeof(RB_cfg_t));
        memcpy(&local_psd_cfg, &psd_cfg, sizeof(PsdConfig_t));
        memcpy(&local_desired_cfg, &desired_config, sizeof(DesiredCfg_t));
        config_received = false; 

        if (local_rb_cfg.total_bytes > rb.size) {
            printf("[RF] Error: Request exceeds buffer size!\n");
            continue;
        }

        // D. Acquire Data
        rb_reset(&rb);
        stop_streaming = false;
        hackrf_apply_cfg(device, &local_hack_cfg);

        if (hackrf_start_rx(device, rx_callback, NULL) != HACKRF_SUCCESS) {
            needs_recovery = true;
            goto error_handler;
        }

        // Wait for buffer to fill
        int safety_timeout = 500; 
        while (safety_timeout > 0) {
            if (rb_available(&rb) >= local_rb_cfg.total_bytes) break; 
            usleep(10000); 
            safety_timeout--;
        }

        stop_streaming = true; 
        hackrf_stop_rx(device);
        usleep(50000); 

        if (safety_timeout <= 0) {
            needs_recovery = true;
            goto error_handler;
        }

        // E. Process Data (DSP)
        int8_t* linear_buffer = malloc(local_rb_cfg.total_bytes);
        if (linear_buffer) {
            rb_read(&rb, linear_buffer, local_rb_cfg.total_bytes);
            
            signal_iq_t* sig = load_iq_from_buffer(linear_buffer, local_rb_cfg.total_bytes);
            
            double* freq = malloc(local_psd_cfg.nperseg * sizeof(double));
            double* psd = malloc(local_psd_cfg.nperseg * sizeof(double));

            if (freq && psd && sig) {
                // 1. Calculate Full Bandwidth PSD (-Fs/2 to Fs/2)
                execute_welch_psd(sig, &local_psd_cfg, freq, psd);
                scale_psd(psd, local_psd_cfg.nperseg, local_desired_cfg.scale);

                // 2. APPLY SPAN LOGIC (Crop Arrays)
                double half_span = local_desired_cfg.span / 2.0;
                int start_idx = 0;
                int end_idx = local_psd_cfg.nperseg - 1;

                // Find indices where freq is within [-Span/2, +Span/2]
                for (int i = 0; i < local_psd_cfg.nperseg; i++) {
                    if (freq[i] >= -half_span) {
                        start_idx = i;
                        break;
                    }
                }
                for (int i = start_idx; i < local_psd_cfg.nperseg; i++) {
                    if (freq[i] > half_span) {
                        end_idx = i - 1;
                        break;
                    }
                    end_idx = i;
                }

                int valid_len = end_idx - start_idx + 1;

                // 3. Publish only the cropped window
                if (valid_len > 0) {
                    publish_results(&freq[start_idx], &psd[start_idx], valid_len, &local_hack_cfg);
                } else {
                    printf("[RF] Warning: Span resulted in 0 bins.\n");
                }
            }

            free(linear_buffer);
            if (freq) free(freq);
            if (psd) free(psd);
            free_signal_iq(sig);
        }
        continue; 

        // F. Error Handling & Recovery
        error_handler:
        stop_streaming = true;
        if (needs_recovery) {
            recover_hackrf(); // This handles closing/reopening internally
            needs_recovery = false;
        }
    }

    rb_free(&rb);
    return 0;
}