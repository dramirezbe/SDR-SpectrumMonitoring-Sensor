/**
 * @file main.c
 * @brief Continuous Headless PSD Analyzer with Error Recovery & Secure Memory Cleanup
 * Flow: ZMQ_SUB -> HackRF -> Welch -> JSON -> ZMQ_PUB
 */

#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <stdbool.h>
#include <math.h>
#include <string.h>
#include <pthread.h>
#include <libhackrf/hackrf.h>
#include <inttypes.h>
#include <cjson/cJSON.h>
#include <ctype.h> // Required for validation logic

// Modules
#include "Modules/psd.h"
#include "Modules/utils.h"
#include "Modules/datatypes.h" 

// Drivers
#include "Drivers/ring_buffer.h" 
#include "Drivers/sdr_HAL.h"
#include "Drivers/zmqsub.h"
#include "Drivers/zmqpub.h"
#include "Drivers/bacn_gpio.h"
#include "Drivers/bacn_LTE.h"
#include "Drivers/bacn_GPS.h"

// ----------------------------------------------------------------------
// Global State & Config
// ----------------------------------------------------------------------

st_uart LTE;
gp_uart GPS;
GPSCommand GPSInfo;

bool LTE_open = false;
bool GPS_open = false;

hackrf_device* device = NULL;
ring_buffer_t rb;
zpub_t *publisher = NULL; // The Output Channel

// Flags for thread synchronization
volatile bool stop_streaming = false;
volatile bool config_received = false; 

// Global Configuration Containers
DesiredCfg_t desired_config = {0};
PsdConfig_t psd_cfg = {0};
SDR_cfg_t hack_cfg = {0};
RB_cfg_t rb_cfg = {0};

// ----------------------------------------------------------------------
// Forward Declarations
// ----------------------------------------------------------------------
void print_desired(const DesiredCfg_t *cfg);
int find_params_psd(DesiredCfg_t desired, SDR_cfg_t *hack_cfg, PsdConfig_t *psd_cfg, RB_cfg_t *rb_cfg);
void publish_results(double* freq_array, double* psd_array, int length);
int recover_hackrf(void);

//LTE connection internet
#define CMD_BUF 256
#define IP_BUF 64

// Run a shell command and get output
void run_cmd(const char *cmd) {
    printf("[CMD] %s\n", cmd);
    system(cmd);
}

// Get PPP0 IP address
int get_ppp_ip(char *ip) {
    FILE *fp;
    char cmd[] = "ip -o -4 addr show dev ppp0 | awk '{print $4}' | cut -d/ -f1";
    char buffer[IP_BUF] = {0};

    fp = popen(cmd, "r");
    if (!fp) return 0;

    if (fgets(buffer, sizeof(buffer), fp) != NULL) {
        // Remove newline
        buffer[strcspn(buffer, "\n")] = 0;
        if (strlen(buffer) > 0) {
            strcpy(ip, buffer);
            pclose(fp);
            return 1;
        }
    }

    pclose(fp);
    return 0;
}


// ----------------------------------------------------------------------
// GPS Validation & Thread
// ----------------------------------------------------------------------

/**
 * @brief Validates GPS strings to ensure they are real coordinates.
 * Checks for: NULL, Empty, Non-numeric, Out of Range, and 0.0 (No Fix).
 */
bool is_valid_gps_data(const char* lat_str, const char* lon_str) {
    if (!lat_str || !lon_str) return false;
    
    // Check length (needs at least 1 digit)
    if (strlen(lat_str) < 1 || strlen(lon_str) < 1) return false;

    char *endptr_lat, *endptr_lon;
    double lat = strtod(lat_str, &endptr_lat);
    double lon = strtod(lon_str, &endptr_lon);

    // 1. Check if conversion actually consumed characters (valid number)
    // If *endptr is not the null terminator (and not whitespace), parsing failed or had garbage
    if (lat_str == endptr_lat || lon_str == endptr_lon) return false;

    // 2. Range Logic
    // Latitude must be -90 to 90
    if (lat < -90.0 || lat > 90.0) return false;
    // Longitude must be -180 to 180
    if (lon < -180.0 || lon > 180.0) return false;

    // 3. "Null Island" Check
    // Most GPS modules output 0.0000 when they don't have a fix yet.
    // We treat (0,0) as invalid unless you are actually in the ocean off Africa.
    if (fabs(lat) < 0.0001 && fabs(lon) < 0.0001) return false;

    return true;
}

/**
 * @brief Thread to post GPS data every 10 seconds.
 * Runs in parallel with the SDR acquisition loop.
 */
void *gps_monitor_thread(void *arg) {
    char *api_url = (char *)arg;

    printf("[GPS-THREAD] Started. Reporting to: %s\n", api_url);

    while (!stop_streaming) {
        
        // Only attempt to send if the URL is valid
        if (api_url != NULL) {
            
            // Validate the current global GPSInfo data
            if (is_valid_gps_data(GPSInfo.Latitude, GPSInfo.Longitude)) {
                
                // Debug: Print the payload that will be sent
                printf("[GPS-THREAD] Sending Payload: {\"latitude\": \"%s\", \"longitude\": \"%s\", \"altitude\": \"%s\"}\n",
                       GPSInfo.Latitude, GPSInfo.Longitude, GPSInfo.Altitude);

                int res = post_gps_data(
                    api_url, 
                    GPSInfo.Altitude, 
                    GPSInfo.Latitude, 
                    GPSInfo.Longitude
                );

                if (res != 0) {
                    fprintf(stderr, "[GPS-THREAD] POST failed (Error code: %d)\n", res);
                }
            } 
            else {
                // WARNING: GPS is available but data is invalid or not locked yet
                printf("[GPS-THREAD] WARN: Waiting for valid fix (Lat: '%s', Lon: '%s', Alt: '%s'). Retrying in 10s...\n", 
                       GPSInfo.Latitude, GPSInfo.Longitude, GPSInfo.Altitude);
            }
        }

        sleep(10); // Wait 10 seconds
    }

    return NULL;
}

// ----------------------------------------------------------------------
// HackRF Callback
// ----------------------------------------------------------------------

int rx_callback(hackrf_transfer* transfer) {
    if (stop_streaming) return -1;
    // Write directly to ring buffer
    rb_write(&rb, transfer->buffer, transfer->valid_length);
    return 0;
}

// ----------------------------------------------------------------------
// Recovery Logic
// ----------------------------------------------------------------------

/**
 * @brief Closes and Re-opens the HackRF device to clear hardware locks.
 * @return 0 on success, -1 if device could not be reopened.
 */
int recover_hackrf(void) {
    printf("\n[RECOVERY] Initiating Hardware Reset sequence...\n");

    // 1. Stop RX safely if currently active (best effort)
    if (device != NULL) {
        hackrf_stop_rx(device);
        usleep(100000); // 100ms settle time
    }

    // 2. Close the device to release USB handle
    if (device != NULL) {
        hackrf_close(device);
        device = NULL;
        printf("[RECOVERY] Device Closed.\n");
    }

    // 3. Attempt to Re-open (Try 3 times with delays)
    int attempts = 0;
    while (attempts < 3) {
        usleep(500000); // Wait 500ms before reopening
        int status = hackrf_open(&device);
        
        if (status == HACKRF_SUCCESS) {
            printf("[RECOVERY] Device Re-opened successfully.\n");
            return 0;
        }
        
        fprintf(stderr, "[RECOVERY] Re-open attempt %d failed (Error %d). Retrying...\n", attempts + 1, status);
        attempts++;
    }

    fprintf(stderr, "[CRITICAL] Recovery Failed. Device not found or USB stuck.\n");
    return -1;
}

// ----------------------------------------------------------------------
// Config Logic
// ----------------------------------------------------------------------

int find_params_psd(DesiredCfg_t desired, SDR_cfg_t *hack_cfg, PsdConfig_t *psd_cfg, RB_cfg_t *rb_cfg) {
    double enbw_factor = get_window_enbw_factor(desired.window_type);
    
    // Calculate NPERSEG to hit the desired Resolution Bandwidth (RBW)
    double required_nperseg_val = enbw_factor * (double)desired.sample_rate / (double)desired.rbw;
    int exponent = (int)ceil(log2(required_nperseg_val));
    
    psd_cfg->nperseg = (int)pow(2, exponent);
    psd_cfg->noverlap = psd_cfg->nperseg * desired.overlap;
    psd_cfg->window_type = desired.window_type;
    psd_cfg->sample_rate = desired.sample_rate;

    hack_cfg->sample_rate = desired.sample_rate;
    hack_cfg->center_freq = desired.center_freq;
    hack_cfg->amp_enabled = desired.amp_enabled;
    hack_cfg->lna_gain = desired.lna_gain;
    hack_cfg->vga_gain = desired.vga_gain;
    hack_cfg->ppm_error = desired.ppm_error;

    // Ring Buffer Sizing: Capture enough for Welch averaging
    rb_cfg->total_bytes = (size_t)(desired.sample_rate * 2); // ~1 second of IQ data
    rb_cfg->rb_size = (int)(rb_cfg->total_bytes * 2);        // Buffer overhead
    
    return 0;
}

// ----------------------------------------------------------------------
// Output Serialization (JSON)
// ----------------------------------------------------------------------

void publish_results(double* freq_array, double* psd_array, int length) {
    if (!publisher || !freq_array || !psd_array) return;

    cJSON *root = cJSON_CreateObject();
    
    // Metadata
    cJSON_AddNumberToObject(root, "start_freq_hz", freq_array[0] + (double)hack_cfg.center_freq);
    cJSON_AddNumberToObject(root, "end_freq_hz", freq_array[length-1] + (double)hack_cfg.center_freq);
    cJSON_AddNumberToObject(root, "bin_count", length);

    // Data Array
    cJSON *pxx_array = cJSON_CreateDoubleArray(psd_array, length);
    cJSON_AddItemToObject(root, "Pxx", pxx_array);

    // Send
    char *json_string = cJSON_PrintUnformatted(root); 
    zpub_publish(publisher, "data", json_string);
    
    printf("[ZMQ] Published results (%d bins, %zu bytes)\n", length, strlen(json_string));

    free(json_string);
    cJSON_Delete(root);
}

// ----------------------------------------------------------------------
// ZMQ Callback
// ----------------------------------------------------------------------

void handle_psd_message(const char *payload) {
    printf("\n>>> [ZMQ] Received Command Payload.\n");

    free_desired_psd(&desired_config); 
    memset(&desired_config, 0, sizeof(DesiredCfg_t));

    if (parse_psd_config(payload, &desired_config) == 0) {
        find_params_psd(desired_config, &hack_cfg, &psd_cfg, &rb_cfg);
        print_desired(&desired_config);
        config_received = true; 
    } else {
        fprintf(stderr, ">>> [PARSER] Failed to parse JSON configuration.\n");
    }
}

// ----------------------------------------------------------------------
// Main Application
// ----------------------------------------------------------------------

int main() {
    // -------------------------------------------------
    // 1. Hardware/Driver Init
    // -------------------------------------------------
    if(init_usart(&LTE) != 0) {
        printf("Error : LTE open failed\r\n");
        return -1;
    }

    if(init_usart1(&GPS) != 0) {
        printf("Error : GPS open failed\r\n");
        return -1;
    }

    char ip[IP_BUF];
    printf("Starting PPP connection...\n");
    run_cmd("sudo pon rnet");
    sleep(10); // allow time for ppp to start

    while (!get_ppp_ip(ip)) {
        printf("No IP address assigned! Restarting PPP...\n");

        run_cmd("sudo poff rnet");
        sleep(10);

        run_cmd("sudo pon rnet");
        sleep(10);

        if (!get_ppp_ip(ip)) {
            printf("PPP failed again. No IP assigned.\n");
        }
    }

    printf("PPP connected. IP = %s\n", ip);

    // -------------------------------------------------
    // 2. Load Environment Variables (Using utils module)
    // -------------------------------------------------
    char *api_url = getenv_c("API_URL");
    if (api_url != NULL) {
        printf("API URL: %s\n", api_url);
    } else {
        printf("API URL not found in .env\n");
    }

    // -------------------------------------------------
    // 3. System Initialization
    // -------------------------------------------------
    
    // START GPS THREAD
    pthread_t gps_tid;
    if (api_url != NULL) {
        int ret = pthread_create(&gps_tid, NULL, gps_monitor_thread, (void *)api_url);
        if (ret != 0) {
            fprintf(stderr, "Error: Failed to create GPS thread\n");
        } else {
            printf("[SYSTEM] GPS Monitor Thread Launched.\n");
        }
    } else {
        printf("[SYSTEM] Warning: No API_URL. GPS thread disabled.\n");
    }
    
    char *input_topic = "acquire";
    int cycle_count = 0;
    bool needs_recovery = false; 
    
    // A. ZMQ Input
    zsub_t *sub = zsub_init(input_topic, handle_psd_message);
    if (!sub) { fprintf(stderr, "CRITICAL: ZMQ Sub Init Failed.\n"); return 1; }
    zsub_start(sub);

    // B. ZMQ Output
    publisher = zpub_init();
    if (!publisher) { fprintf(stderr, "CRITICAL: ZMQ Pub Init Failed.\n"); zsub_close(sub); return 1; }

    // C. HackRF Library
    if (hackrf_init() != HACKRF_SUCCESS) { fprintf(stderr, "CRITICAL: HackRF Lib Init Failed.\n"); return 1; }
    
    // D. Initial Device Open
    int status = hackrf_open(&device);
    if (status != HACKRF_SUCCESS) {
        fprintf(stderr, "[SYSTEM] Warning: Initial Open failed. Will retry in loop.\n");
    } else {
        printf("[SYSTEM] HackRF Connected. Entering Idle Loop.\n");
    }


    // -------------------------------------------------
    // 4. Continuous Loop
    // -------------------------------------------------
    while (1) {
        
        // --- A. IDLE STATE ---
        if (!config_received) {
            usleep(10000); // 10ms sleep to save CPU
            continue;
        }

        // --- B. PREPARE CYCLE ---
        cycle_count++;
        printf("\n=== Acquisition Cycle #%d ===\n", cycle_count);

        if (device == NULL) {
            fprintf(stderr, "[ERROR] Device handle missing.\n");
            needs_recovery = true;
            goto error_handler;
        }

        // Initialize Ring Buffer (Ensure Drivers/ring_buffer.c uses calloc!)
        rb_init(&rb, rb_cfg.rb_size);
        stop_streaming = false;

        // Apply Settings
        hackrf_apply_cfg(device, &hack_cfg);

        // --- C. START HARDWARE ---
        hackrf_start_rx(device, rx_callback, NULL);

        // --- D. WAIT FOR DATA (WITH TIMEOUT) ---
        int safety_timeout = 500; // 500 * 10ms = 5 Seconds
        while ((rb_available(&rb) < rb_cfg.total_bytes) && (safety_timeout > 0)) {
            usleep(10000); 
            safety_timeout--;
        }

        // Stop Hardware immediately after loop
        stop_streaming = true;
        hackrf_stop_rx(device);

        if (safety_timeout <= 0) {
            fprintf(stderr, "[ERROR] Buffer Timeout. HackRF stalled.\n");
            needs_recovery = true;
            goto error_handler;
        }

        // --- E. DSP PROCESSING ---
        int8_t* linear_buffer = malloc(rb_cfg.total_bytes);
        if (!linear_buffer) {
            fprintf(stderr, "[ERROR] System OOM.\n");
            goto error_handler;
        }

        // Extract from Ring Buffer
        rb_read(&rb, linear_buffer, rb_cfg.total_bytes);
        
        // Convert to IQ Complex
        signal_iq_t* sig = load_iq_from_buffer(linear_buffer, rb_cfg.total_bytes);
        
        // Output Arrays
        double* freq = malloc(psd_cfg.nperseg * sizeof(double));
        double* psd = malloc(psd_cfg.nperseg * sizeof(double));

        if (freq && psd && sig) {
            // Welch Method
            execute_welch_psd(sig, &psd_cfg, freq, psd);
            
            // Scaling / Units
            scale_psd(psd, psd_cfg.nperseg, desired_config.scale);
            
            // Send to Python/Viewer
            publish_results(freq, psd, psd_cfg.nperseg);
        }

        // DSP Cleanup
        if (linear_buffer) free(linear_buffer);
        if (freq) free(freq);
        if (psd) free(psd);
        free_signal_iq(sig);

        // --- F. CYCLE CLEANUP (SUCCESS PATH) ---
        // IMPORTANT: rb_free must zero-out memory in Drivers/ring_buffer.c
        rb_free(&rb); 
        config_received = false; 
        continue; // Go back to Idle

        // --- G. ERROR RECOVERY PATH ---
        error_handler:
        rb_free(&rb); // Zero-out buffer even on failure
        
        if (needs_recovery) {
            if (recover_hackrf() != 0) {
                fprintf(stderr, "[SYSTEM] Hardware dead. Sleeping 2s.\n");
                sleep(2);
            }
            needs_recovery = false;
        }
        
        config_received = false; // Reset state to wait for NEW command
        printf("[SYSTEM] Cycle Aborted. Returning to Idle.\n");
    }

    // Unreachable Code (Cleanup)
    if(device) hackrf_close(device);
    hackrf_exit();
    zsub_close(sub);
    zpub_close(publisher);
    if(api_url) free(api_url); // Free the env string
    return 0;
}

void print_desired(const DesiredCfg_t *cfg) {
    printf("  [CFG] Freq: %" PRIu64 " | RBW: %d | Scale: %s\n", 
           cfg->center_freq, cfg->rbw, cfg->scale ? cfg->scale : "dBm");
}
