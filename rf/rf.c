//rf.c
#define _GNU_SOURCE

#include <stdio.h>
#include <stdbool.h>
#include <stdlib.h>
#include <unistd.h>
#include <math.h>
#include <string.h>
#include <inttypes.h>
#include <pthread.h>

#include <libhackrf/hackrf.h>
#include <cjson/cJSON.h>

// Project Includes
#include "psd.h"
#include "datatypes.h" 
#include "sdr_HAL.h"     
#include "ring_buffer.h" 
#include "zmq_util.h" 
#include "utils.h"

#ifndef NO_COMMON_LIBS
    #include "bacn_gpio.h"
#endif

// =========================================================
// GLOBAL VARIABLES
// =========================================================

// Communication
zpair_t *zmq_channel = NULL;

// Hardware
hackrf_device* device = NULL;
ring_buffer_t rb;

// State Control
volatile bool stop_streaming = true; 
volatile bool config_received = false; 

// Global Config Containers (Written by ZMQ thread, Read by Main)
DesiredCfg_t desired_config = {0};
PsdConfig_t psd_cfg = {0};
SDR_cfg_t hack_cfg = {0};
RB_cfg_t rb_cfg = {0};

// =========================================================
// HARDWARE FUNCTIONS
// =========================================================

/**
 * @brief LibHackRF Callback. Pushes raw IQ data into Ring Buffer.
 */
int rx_callback(hackrf_transfer* transfer) {
    if (stop_streaming) return 0; 
    rb_write(&rb, transfer->buffer, transfer->valid_length);
    return 0;
}

/**
 * @brief Hardware Recovery Routine.
 * Closes and re-opens the HackRF device if an error occurs.
 */
int recover_hackrf(void) {
    printf("\n[RECOVERY] Initiating Hardware Reset sequence...\n");
    
    // 1. Close existing handle if open
    if (device != NULL) {
        stop_streaming = true;
        hackrf_stop_rx(device);
        usleep(100000); // 100ms
        hackrf_close(device);
        device = NULL;
    }

    // 2. Attempt Re-open loop
    int attempts = 0;
    while (attempts < 3) {
        usleep(500000); // 500ms wait
        int status = hackrf_open(&device);
        if (status == HACKRF_SUCCESS) {
            printf("[RECOVERY] Device Re-opened successfully.\n");
            return 0;
        }
        attempts++;
        fprintf(stderr, "[RECOVERY] Attempt %d failed.\n", attempts);
    }
    
    return -1; // Failed to recover
}

// =========================================================
// ZMQ & LOGIC FUNCTIONS
// =========================================================

/**
 * @brief Formats result data as JSON and publishes via ZMQ PAIR.
 */
void publish_results(double* freq_array, double* psd_array, int length, SDR_cfg_t *local_hack) {
    if (!zmq_channel || !freq_array || !psd_array || length <= 0) return;

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
    
    // SEND via ZMQ Pair
    zpair_send(zmq_channel, json_string);

    free(json_string);
    cJSON_Delete(root);
}

/**
 * @brief ZMQ Callback. Handles incoming configuration commands.
 */
void on_command_received(const char *payload) {
    printf("\n>>> [RF] Received Command Payload.\n");
    
    // 1. Cleanup previous scale strings if needed
    // (Assuming free_desired_psd handles internal pointers)
    if (config_received) {
        // Optional: free_desired_psd(&desired_config); 
    }

    // 2. Clear Structs
    memset(&desired_config, 0, sizeof(DesiredCfg_t));

    // 3. Parse and Calculate
    if (parse_config_rf(payload, &desired_config) == 0) {
        find_params_psd(desired_config, &hack_cfg, &psd_cfg, &rb_cfg);
        print_config_summary(&desired_config, &hack_cfg, &psd_cfg, &rb_cfg);

        // 4. Hardware GPIO Selection (if applicable)
        #ifndef NO_COMMON_LIBS
            select_ANTENNA(desired_config.antenna_port);
        #else
            printf("[GPIO] selected port: %d\n", desired_config.antenna_port);
        #endif

        // 5. Signal Main Loop
        config_received = true; 
    } else {
        fprintf(stderr, ">>> [PARSER] Failed to parse JSON configuration.\n");
    }
}

// =========================================================
// MAIN EXECUTION
// =========================================================

int main() {
    // 1. Environment Configuration
    char *raw_verbose = getenv_c("VERBOSE");
    bool verbose_mode = (raw_verbose != NULL && strcmp(raw_verbose, "true") == 0);
    if (raw_verbose) free(raw_verbose);

    char *ipc_addr = getenv_c("IPC_ADDR");
    if (!ipc_addr) ipc_addr = strdup("ipc:///tmp/rf_engine"); // Ensure valid ZMQ addr

    printf("[RF] Starting. IPC=%s, VERBOSE=%d\n", ipc_addr, verbose_mode);

    // 2. Init ZMQ Pair
    zmq_channel = zpair_init(ipc_addr, on_command_received, verbose_mode ? 1 : 0);
    if (!zmq_channel) {
        fprintf(stderr, "[RF] FATAL: Failed to initialize ZMQ at %s\n", ipc_addr);
        if (ipc_addr) free(ipc_addr);
        return 1;
    }
    zpair_start(zmq_channel); // Starts the listener thread

    // 3. Robust HackRF Init
    printf("[RF] Initializing HackRF Library...\n");
    while (hackrf_init() != HACKRF_SUCCESS) {
        fprintf(stderr, "[RF] Error: HackRF Init failed. Retrying in 5s...\n");
        sleep(5);
    }
    printf("[RF] HackRF Library Initialized.\n");

    // 4. Device Opening
    while (hackrf_open(&device) != HACKRF_SUCCESS) {
        fprintf(stderr, "[RF] Warning: Initial Open failed. Retrying in 5s...\n");
        sleep(5);
    }
    printf("[RF] HackRF Device Opened.\n");

    // 5. Buffer Allocation
    size_t FIXED_BUFFER_SIZE = 100 * 1024 * 1024; // 100MB
    rb_init(&rb, FIXED_BUFFER_SIZE);
    printf("[RF] Ring Buffer Initialized (%zu MB)\n", FIXED_BUFFER_SIZE / (1024*1024));

    bool needs_recovery = false; 

    // 6. Local Config Containers (Thread Safety)
    SDR_cfg_t local_hack_cfg;
    RB_cfg_t local_rb_cfg;
    PsdConfig_t local_psd_cfg;
    DesiredCfg_t local_desired_cfg;

    // 7. Main Loop
    while (1) {
        // A. Wait for Configuration
        if (!config_received) {
            usleep(50000); // 50ms wait
            continue;
        }

        // B. Check Device Health
        if (device == NULL) {
            needs_recovery = true;
            goto error_handler;
        }

        // C. Snapshot Configuration
        // Copy globals to locals so we can run atomically
        memcpy(&local_hack_cfg, &hack_cfg, sizeof(SDR_cfg_t));
        memcpy(&local_rb_cfg, &rb_cfg, sizeof(RB_cfg_t));
        memcpy(&local_psd_cfg, &psd_cfg, sizeof(PsdConfig_t));
        memcpy(&local_desired_cfg, &desired_config, sizeof(DesiredCfg_t));
        config_received = false; 

        // Sanity Check
        if (local_rb_cfg.total_bytes > rb.size) {
            printf("[RF] Error: Request bytes (%zu) exceeds buffer size!\n", local_rb_cfg.total_bytes);
            continue;
        }

        // D. Acquire Data
        rb_reset(&rb);
        stop_streaming = false;

        // Apply Config
        hackrf_apply_cfg(device, &local_hack_cfg);

        // Start Rx
        if (hackrf_start_rx(device, rx_callback, NULL) != HACKRF_SUCCESS) {
            needs_recovery = true; goto error_handler;
        }

        // Wait for buffer to fill (with timeout)
        int safety_timeout = 500; // ~5 seconds
        while (safety_timeout > 0) {
            if (rb_available(&rb) >= local_rb_cfg.total_bytes) break; 
            usleep(10000); 
            safety_timeout--;
        }

        // Stop Rx
        stop_streaming = true; 
        hackrf_stop_rx(device);
        usleep(50000); // Settle time

        if (safety_timeout <= 0) {
            fprintf(stderr, "[RF] Error: Acquisition Timeout.\n");
            needs_recovery = true;
            goto error_handler;
        }

        // E. DSP Processing & Span Logic
        int8_t* linear_buffer = malloc(local_rb_cfg.total_bytes);
        if (linear_buffer) {
            // 1. Unroll RB
            rb_read(&rb, linear_buffer, local_rb_cfg.total_bytes);
            
            // 2. Convert to IQ
            signal_iq_t* sig = load_iq_from_buffer(linear_buffer, local_rb_cfg.total_bytes);
            
            double* freq = malloc(local_psd_cfg.nperseg * sizeof(double));
            double* psd = malloc(local_psd_cfg.nperseg * sizeof(double));

            if (freq && psd && sig) {
                // 3. Execute Welch (Full Bandwidth)
                execute_welch_psd(sig, &local_psd_cfg, freq, psd);
                scale_psd(psd, local_psd_cfg.nperseg, local_desired_cfg.scale);

                // 4. APPLY SPAN LOGIC (Crop Arrays)
                // Filter the result to only show the requested Span
                double half_span = local_desired_cfg.span / 2.0;
                int start_idx = 0;
                int end_idx = local_psd_cfg.nperseg - 1;

                // Find valid index range
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

                // 5. Publish
                if (valid_len > 0) {
                    publish_results(&freq[start_idx], &psd[start_idx], valid_len, &local_hack_cfg);
                } else {
                    printf("[RF] Warning: Span resulted in 0 bins.\n");
                }
            }

            // Cleanup Local DSP
            free(linear_buffer);
            if (freq) free(freq);
            if (psd) free(psd);
            free_signal_iq(sig);
        }
        
        continue; 

        // F. Error Handler
        error_handler:
        stop_streaming = true;
        if (needs_recovery) {
            recover_hackrf(); 
            needs_recovery = false;
        }
    }

    // Cleanup
    zpair_close(zmq_channel);
    if (ipc_addr) free(ipc_addr);
    rb_free(&rb);
    
    return 0;
}