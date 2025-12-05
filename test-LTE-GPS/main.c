/**
 * @file main.c
 * @brief Continuous Headless PSD Analyzer (Unified) with CSV Metrics
 * @details Internet logic removed. Strictly uses wlan0 for MAC.
 */

#define _GNU_SOURCE 

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
#include <time.h>
#include <sys/time.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/resource.h>
#include <sys/sysinfo.h>
#include <dirent.h>
#include <errno.h>

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
#define METRICS_DIR "CSV_metrics_psdgpsCount"
#define MAX_CSV_FILES 100

// =========================================================
// GLOBAL VARIABLES
// =========================================================
st_uart LTE;
gp_uart GPS;
hackrf_device* device = NULL;

GPSCommand GPSInfo;
ring_buffer_t rb;
zpub_t *publisher = NULL; 

bool LTE_open = false;
bool GPS_open = false;
volatile bool stop_streaming = false;
volatile bool config_received = false; 

DesiredCfg_t desired_config = {0};
PsdConfig_t psd_cfg = {0};
SDR_cfg_t hack_cfg = {0};
RB_cfg_t rb_cfg = {0};

char device_mac[32] = "unknown_mac"; // Cached MAC

// =========================================================
// METRICS STRUCTS & HELPERS
// =========================================================

typedef struct {
    double cpu_time_ms; // CPU time consumed by process
    long mem_used_kb;
    long swap_used_kb;
    double temp_c;
} ResourceSnapshot;

// Helper to get monotonic time in ms
double get_time_ms() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (ts.tv_sec * 1000.0) + (ts.tv_nsec / 1000000.0);
}

// Helper to get wall clock string for filename
void get_datetime_str(char *buf, size_t len) {
    time_t now = time(NULL);
    struct tm *t = localtime(&now);
    strftime(buf, len, "%Y-%m-%d_%H-%M-%S", t);
}

// Helper to get system resources
void get_sys_metrics(ResourceSnapshot *m, struct rusage *prev_usage) {
    struct sysinfo info;
    sysinfo(&info);
    
    // RAM & Swap
    m->mem_used_kb = (info.totalram - info.freeram) * info.mem_unit / 1024;
    m->swap_used_kb = (info.totalswap - info.freeswap) * info.mem_unit / 1024;

    // CPU (Process specific usage since last check)
    struct rusage curr_usage;
    getrusage(RUSAGE_SELF, &curr_usage);
    
    if (prev_usage != NULL) {
        double start = (prev_usage->ru_utime.tv_sec * 1000.0) + (prev_usage->ru_utime.tv_usec / 1000.0) +
                       (prev_usage->ru_stime.tv_sec * 1000.0) + (prev_usage->ru_stime.tv_usec / 1000.0);
        double end = (curr_usage.ru_utime.tv_sec * 1000.0) + (curr_usage.ru_utime.tv_usec / 1000.0) +
                     (curr_usage.ru_stime.tv_sec * 1000.0) + (curr_usage.ru_stime.tv_usec / 1000.0);
        m->cpu_time_ms = end - start;
    } else {
        m->cpu_time_ms = 0;
    }
    
    // Update previous usage for next delta
    if (prev_usage != NULL) *prev_usage = curr_usage;

    // Temperature (Raspberry Pi / Linux standard thermal zone)
    FILE *f = fopen("/sys/class/thermal/thermal_zone0/temp", "r");
    if (f) {
        long temp;
        if(fscanf(f, "%ld", &temp) == 1) m->temp_c = temp / 1000.0;
        else m->temp_c = 0.0;
        fclose(f);
    } else {
        m->temp_c = 0.0;
    }
}

// Helper to get MAC address (strictly wlan0)
void fetch_mac_address() {
    FILE *fp = fopen("/sys/class/net/wlan0/address", "r");
    
    if (fp) {
        if (fgets(device_mac, sizeof(device_mac), fp)) {
            device_mac[strcspn(device_mac, "\n")] = 0; // Strip newline
        }
        fclose(fp);
    } else {
        fprintf(stderr, "[SYSTEM] Error: Could not read wlan0 MAC address.\n");
    }
}

// Helper: Filter for scandir (only CSVs)
int csv_filter(const struct dirent *entry) {
    const char *dot = strrchr(entry->d_name, '.');
    if (dot && strcmp(dot, ".csv") == 0) return 1;
    return 0;
}

void save_metrics_csv(
    double iq_dur, ResourceSnapshot iq_res,
    double psd_dur, ResourceSnapshot psd_res,
    int pxx_len
) {
    // 1. Ensure Directory Exists
    struct stat st = {0};
    if (stat(METRICS_DIR, &st) == -1) {
        mkdir(METRICS_DIR, 0777);
    }

    // 2. Rotation Logic (Keep max 100)
    struct dirent **namelist;
    int n = scandir(METRICS_DIR, &namelist, csv_filter, alphasort);
    if (n >= 0) {
        if (n >= MAX_CSV_FILES) {
            int to_delete = n - MAX_CSV_FILES + 1; // +1 to make room for new one
            for (int i = 0; i < to_delete; i++) {
                char path[512];
                snprintf(path, sizeof(path), "%s/%s", METRICS_DIR, namelist[i]->d_name);
                remove(path);
                free(namelist[i]);
            }
            // Free the rest
            for (int i = to_delete; i < n; i++) free(namelist[i]);
        } else {
             for (int i = 0; i < n; i++) free(namelist[i]);
        }
        free(namelist);
    }

    // 3. Create Filename
    char time_str[64];
    get_datetime_str(time_str, sizeof(time_str));
    char filepath[512];
    snprintf(filepath, sizeof(filepath), "%s/%s_%s.csv", METRICS_DIR, time_str, device_mac);

    // 4. Write CSV
    FILE *fp = fopen(filepath, "w");
    if (fp) {
        // Header
        fprintf(fp, "timestamp,mac,center_freq,sample_rate,rbw,overlap,"
                    "iq_time_ms,iq_cpu_ms,iq_temp,iq_ram_kb,iq_swap_kb,"
                    "psd_time_ms,psd_cpu_ms,psd_temp,psd_ram_kb,psd_swap_kb,"
                    "pxx_len,start_freq,end_freq\n");
        
        // Data
        fprintf(fp, "%s,%s,%" PRIu64 ",%d,%d,%.2f,"
                    "%.2f,%.2f,%.2f,%ld,%ld,"
                    "%.2f,%.2f,%.2f,%ld,%ld,"
                    "%d,%.2f,%.2f\n",
                    time_str, device_mac, desired_config.center_freq, desired_config.sample_rate, 
                    desired_config.rbw, desired_config.overlap,
                    // IQ Metrics
                    iq_dur, iq_res.cpu_time_ms, iq_res.temp_c, iq_res.mem_used_kb, iq_res.swap_used_kb,
                    // PSD Metrics
                    psd_dur, psd_res.cpu_time_ms, psd_res.temp_c, psd_res.mem_used_kb, psd_res.swap_used_kb,
                    // Result Params
                    pxx_len, 
                    (double)(desired_config.center_freq - (desired_config.sample_rate/2)),
                    (double)(desired_config.center_freq + (desired_config.sample_rate/2))
        );
        fclose(fp);
        printf("[METRICS] Saved to %s\n", filepath);
    } else {
        perror("Failed to write CSV");
    }
}

// =========================================================
// FUNCTION PROTOTYPES
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
// HELPER IMPLEMENTATIONS (Standard)
// =========================================================

void run_cmd(const char *cmd) {
    printf("[CMD] %s\n", cmd);
    system(cmd);
}

void print_desired(const DesiredCfg_t *cfg) {
    printf("  [CFG] Freq: %" PRIu64 " | RBW: %d | Scale: %s\n", cfg->center_freq, cfg->rbw, cfg->scale ? cfg->scale : "dBm");
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
    // 0. Fetch MAC for Logging (wlan0 only)
    fetch_mac_address();
    printf("[SYSTEM] Mac Address: %s\n", device_mac);

    // 1. Hardware Init
    if(init_usart(&LTE) != 0) fprintf(stderr, "Error: LTE Init failed (UART issue)\n");
    if(init_usart1(&GPS) != 0) { fprintf(stderr, "Error: GPS Init failed\n"); return -1; }

    // --- REMOVED: PPP/Internet Connection Logic ---

    // 2. Setup Environment
    char *api_url = getenv_c("API_URL"); 
    pthread_t gps_tid;
    
    if (api_url != NULL) {
        printf("API URL: %s\n", api_url);
        pthread_create(&gps_tid, NULL, gps_monitor_thread, (void *)api_url);
    }

    zsub_t *sub = zsub_init("acquire", handle_psd_message);
    if (!sub) return 1;
    zsub_start(sub);

    publisher = zpub_init();
    if (!publisher) return 1;

    if (hackrf_init() != HACKRF_SUCCESS) return 1;
    if (hackrf_open(&device) != HACKRF_SUCCESS) {
        fprintf(stderr, "[SYSTEM] Warning: Initial Open failed. Will retry in loop.\n");
    }

    int cycle_count = 0;
    bool needs_recovery = false; 

    // --- METRICS VARIABLES ---
    struct rusage prev_rusage; 
    getrusage(RUSAGE_SELF, &prev_rusage); // Init usage baseline

    while (1) {
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

        // --- STEP 1: PREPARE ACQUISITION ---
        rb_init(&rb, rb_cfg.rb_size);
        stop_streaming = false;

        hackrf_apply_cfg(device, &hack_cfg);
        hackrf_start_rx(device, rx_callback, NULL);

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

        // --- STEP 2: IQ EXTRACTION METRICS START ---
        double t0_iq = get_time_ms();
        getrusage(RUSAGE_SELF, &prev_rusage); // Reset CPU counter

        int8_t* linear_buffer = malloc(rb_cfg.total_bytes);
        signal_iq_t* sig = NULL;

        if (linear_buffer) {
            rb_read(&rb, linear_buffer, rb_cfg.total_bytes);
            sig = load_iq_from_buffer(linear_buffer, rb_cfg.total_bytes);
            free(linear_buffer);
        }

        // --- IQ EXTRACTION METRICS END ---
        double t1_iq = get_time_ms();
        ResourceSnapshot iq_metrics;
        get_sys_metrics(&iq_metrics, &prev_rusage);
        double iq_duration = t1_iq - t0_iq;

        // --- STEP 3: PSD COMPUTATION METRICS START ---
        double t0_psd = get_time_ms();
        getrusage(RUSAGE_SELF, &prev_rusage); // Reset CPU counter

        double* freq = NULL;
        double* psd = NULL;

        if (sig) {
            freq = malloc(psd_cfg.nperseg * sizeof(double));
            psd = malloc(psd_cfg.nperseg * sizeof(double));

            if (freq && psd) {
                execute_welch_psd(sig, &psd_cfg, freq, psd);
                scale_psd(psd, psd_cfg.nperseg, desired_config.scale);
                publish_results(freq, psd, psd_cfg.nperseg);
            }
        }

        // --- PSD COMPUTATION METRICS END ---
        double t1_psd = get_time_ms();
        ResourceSnapshot psd_metrics;
        get_sys_metrics(&psd_metrics, &prev_rusage);
        double psd_duration = t1_psd - t0_psd;

        // --- SAVE CSV ---
        if (freq && psd) {
             save_metrics_csv(iq_duration, iq_metrics, psd_duration, psd_metrics, psd_cfg.nperseg);
        }

        // Cleanup
        if (freq) free(freq);
        if (psd) free(psd);
        if (sig) free_signal_iq(sig);
        
        rb_free(&rb); 
        config_received = false; 
        continue; 

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