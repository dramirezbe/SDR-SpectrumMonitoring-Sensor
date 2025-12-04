/**
 * @file main.c
 * @brief Continuous Headless PSD Analyzer (Unified)
 * @details logic merged from previous main.c and functions.c
 */

#define _GNU_SOURCE // For advanced string functions if needed

// --- STANDARD HEADERS ---
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <stdbool.h>
#include <pthread.h>
#include <math.h>
#include <string.h>
#include <inttypes.h>
#include <ctype.h>

// --- LIBRARY HEADERS ---
#include <libhackrf/hackrf.h>
#include <cjson/cJSON.h>

// --- CUSTOM MODULES & DRIVERS ---
#include "Modules/utils.h"       
#include "Modules/psd.h"
#include "Modules/datatypes.h" 
#include "Drivers/sdr_HAL.h"     
#include "Drivers/ring_buffer.h" 
#include "Drivers/zmqsub.h"
#include "Drivers/zmqpub.h"
#include "Drivers/bacn_gpio.h"
#include "Drivers/bacn_LTE.h"
#include "Drivers/bacn_GPS.h"

// =========================================================
// DEFINITIONS & MACROS
// =========================================================
#define CMD_BUF 256
#ifndef IP_BUF
#define IP_BUF 64
#endif

// =========================================================
// GLOBAL VARIABLES (Formerly External)
// =========================================================
// Hardware Handles
st_uart LTE;
gp_uart GPS;
hackrf_device* device = NULL;

// Data Structures
GPSCommand GPSInfo;
ring_buffer_t rb;
zpub_t *publisher = NULL; 

// State Flags
bool LTE_open = false;
bool GPS_open = false;
volatile bool stop_streaming = false;
volatile bool config_received = false; 

// Configuration Containers
DesiredCfg_t desired_config = {0};
PsdConfig_t psd_cfg = {0};
SDR_cfg_t hack_cfg = {0};
RB_cfg_t rb_cfg = {0};

// =========================================================
// FUNCTION PROTOTYPES (Forward Declarations)
// =========================================================
void run_cmd(const char *cmd);
void print_desired(const DesiredCfg_t *cfg);
int find_params_psd(DesiredCfg_t desired, SDR_cfg_t *hack_cfg, PsdConfig_t *psd_cfg, RB_cfg_t *rb_cfg);
bool is_valid_gps_data(const char* lat_str, const char* lon_str);
void* gps_monitor_thread(void *arg);
int rx_callback(hackrf_transfer* transfer);
int recover_hackrf(void);
void publish_results(double* freq_array, double* psd_array, int length);
void handle_psd_message(const char *payload);

// =========================================================
// HELPER IMPLEMENTATIONS
// =========================================================

void run_cmd(const char *cmd) {
    printf("[CMD] %s\n", cmd);
    system(cmd);
}

int get_wlan_ip(char *ip) {
    FILE *fp;
    // Command targeted at wlan0
    char cmd[] = "ip -o -4 addr show wlan0 | awk '{print $4}' | cut -d/ -f1";
    char buffer[IP_BUF] = {0};

    fp = popen(cmd, "r");
    if (!fp) return 0;

    if (fgets(buffer, sizeof(buffer), fp) != NULL) {
        // Remove trailing newline character
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

int get_eth_ip(char *ip) {
    FILE *fp;
    // Command targeted at eth0
    char cmd[] = "ip -o -4 addr show eth0 | awk '{print $4}' | cut -d/ -f1";
    char buffer[IP_BUF] = {0};

    fp = popen(cmd, "r");
    if (!fp) return 0;

    if (fgets(buffer, sizeof(buffer), fp) != NULL) {
        // Remove trailing newline character
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

int get_ppp_ip(char *ip) {
    FILE *fp;
    char cmd[] = "ip -o -4 addr show ppp0 | awk '{print $4}' | cut -d/ -f1";
    char buffer[IP_BUF] = {0};

    fp = popen(cmd, "r");
    if (!fp) return 0;

    if (fgets(buffer, sizeof(buffer), fp) != NULL) {
        // Remove trailing newline character
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

// --- Config Logic ---

void print_desired(const DesiredCfg_t *cfg) {
    printf("  [CFG] Freq: %" PRIu64 " | RBW: %d | Scale: %s\n", 
           cfg->center_freq, cfg->rbw, cfg->scale ? cfg->scale : "dBm");
}

int find_params_psd(DesiredCfg_t desired, SDR_cfg_t *hack_cfg, PsdConfig_t *psd_cfg, RB_cfg_t *rb_cfg) {
    double enbw_factor = get_window_enbw_factor(desired.window_type);
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

    rb_cfg->total_bytes = (size_t)(desired.sample_rate * 2);
    rb_cfg->rb_size = (int)(rb_cfg->total_bytes * 2);
    return 0;
}

// --- GPS Logic ---

bool is_valid_gps_data(const char* lat_str, const char* lon_str) {
    if (!lat_str || !lon_str) return false;
    if (strlen(lat_str) < 1 || strlen(lon_str) < 1) return false;

    char *endptr_lat, *endptr_lon;
    double lat = strtod(lat_str, &endptr_lat);
    double lon = strtod(lon_str, &endptr_lon);

    if (lat_str == endptr_lat || lon_str == endptr_lon) return false;
    if (lat < -90.0 || lat > 90.0) return false;
    if (lon < -180.0 || lon > 180.0) return false;
    if (fabs(lat) < 0.0001 && fabs(lon) < 0.0001) return false;

    return true;
}

void *gps_monitor_thread(void *arg) {
    char *api_url = (char *)arg;
    printf("[GPS-THREAD] Started. Reporting to: %s\n", api_url);

    while (!stop_streaming) {
        if (api_url != NULL) {
            // Note: GPSInfo is a global structure updated by GPS ISR/Driver
            if (is_valid_gps_data(GPSInfo.Latitude, GPSInfo.Longitude)) {
                post_gps_data(api_url, GPSInfo.Altitude, GPSInfo.Latitude, GPSInfo.Longitude);
            } else {
                printf("[GPS-THREAD] WARN: Waiting for valid fix...\n");
            }
        }
        sleep(10);
    }
    return NULL;
}

// --- Hardware Callbacks ---

int rx_callback(hackrf_transfer* transfer) {
    if (stop_streaming) return -1;
    rb_write(&rb, transfer->buffer, transfer->valid_length);
    return 0;
}

int recover_hackrf(void) {
    printf("\n[RECOVERY] Initiating Hardware Reset sequence...\n");
    if (device != NULL) {
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

void publish_results(double* freq_array, double* psd_array, int length) {
    if (!publisher || !freq_array || !psd_array) return;

    cJSON *root = cJSON_CreateObject();
    cJSON_AddNumberToObject(root, "start_freq_hz", freq_array[0] + (double)hack_cfg.center_freq);
    cJSON_AddNumberToObject(root, "end_freq_hz", freq_array[length-1] + (double)hack_cfg.center_freq);
    cJSON_AddNumberToObject(root, "bin_count", length);

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

// =========================================================
// MAIN ORCHESTRATION
// =========================================================

int main() {
    // 1. Hardware Init
    if(init_usart(&LTE) != 0) {
        fprintf(stderr, "Error: LTE Init failed (UART issue)\n");
    }
    if(init_usart1(&GPS) != 0) {
        fprintf(stderr, "Error: GPS Init failed\n");
        return -1;
    }

    // 2. Internet Connection (Priority: LTE -> Eth -> WLAN)
    char current_ip[IP_BUF] = {0};
    int net_status = -1;

    printf("=== Network Init (Priority: LTE > Eth > WLAN) ===\n");

    if (establish_ppp_connection(current_ip) == 0) {
        printf("[NET] Selected LTE Interface (ppp0).\n");
        net_status = 0;
    } 
    else if (establish_eth_connection(current_ip) == 0) {
        printf("[NET] Selected Ethernet Interface (eth0).\n");
        net_status = 0;
    } 
    else if (establish_wlan_connection(current_ip) == 0) {
        printf("[NET] Selected WLAN Interface (wlan0).\n");
        net_status = 0;
    } 

    if (net_status != 0) {
        fprintf(stderr, "[CRITICAL] All network interfaces failed. Exiting.\n");
        return 1; 
    }

    // 3. Environment & Threading
    char *api_url = getenv_c("API_URL"); 
    pthread_t gps_tid;
    
    if (api_url != NULL) {
        printf("API URL: %s\n", api_url);
        pthread_create(&gps_tid, NULL, gps_monitor_thread, (void *)api_url);
    }

    // 4. ZMQ & SDR Init
    zsub_t *sub = zsub_init("acquire", handle_psd_message);
    if (!sub) return 1;
    zsub_start(sub);

    publisher = zpub_init();
    if (!publisher) return 1;

    if (hackrf_init() != HACKRF_SUCCESS) return 1;
    
    if (hackrf_open(&device) != HACKRF_SUCCESS) {
        fprintf(stderr, "[SYSTEM] Warning: Initial Open failed. Will retry in loop.\n");
    }

    // 5. Continuous Loop
    int cycle_count = 0;
    bool needs_recovery = false; 

    while (1) {
        // A. Wait for ZMQ Command
        if (!config_received) {
            usleep(10000); 
            continue;
        }

        cycle_count++;
        printf("\n=== Acquisition Cycle #%d ===\n", cycle_count);

        if (device == NULL) {
            needs_recovery = true;
            goto error_handler;
        }

        // B. Setup Acquisition
        rb_init(&rb, rb_cfg.rb_size);
        stop_streaming = false;

        hackrf_apply_cfg(device, &hack_cfg);
        hackrf_start_rx(device, rx_callback, NULL);

        // C. Wait for Buffer Fill
        int safety_timeout = 500; 
        while ((rb_available(&rb) < rb_cfg.total_bytes) && (safety_timeout > 0)) {
            usleep(10000); 
            safety_timeout--;
        }

        stop_streaming = true;
        hackrf_stop_rx(device);

        if (safety_timeout <= 0) {
            needs_recovery = true;
            goto error_handler;
        }

        // D. DSP Processing
        int8_t* linear_buffer = malloc(rb_cfg.total_bytes);
        if (linear_buffer) {
            rb_read(&rb, linear_buffer, rb_cfg.total_bytes);
            
            signal_iq_t* sig = load_iq_from_buffer(linear_buffer, rb_cfg.total_bytes);
            double* freq = malloc(psd_cfg.nperseg * sizeof(double));
            double* psd = malloc(psd_cfg.nperseg * sizeof(double));

            if (freq && psd && sig) {
                execute_welch_psd(sig, &psd_cfg, freq, psd);
                scale_psd(psd, psd_cfg.nperseg, desired_config.scale);
                publish_results(freq, psd, psd_cfg.nperseg);
            }

            free(linear_buffer);
            if (freq) free(freq);
            if (psd) free(psd);
            free_signal_iq(sig);
        }

        rb_free(&rb); 
        config_received = false; 
        continue; 

        // E. Error Handler
        error_handler:
        rb_free(&rb); 
        if (needs_recovery) {
            recover_hackrf();
            needs_recovery = false;
        }
        config_received = false;
        printf("[SYSTEM] Cycle Aborted.\n");
    }

    return 0;
}