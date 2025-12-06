/**
 * @file rf.c
 * @brief Continuous Headless PSD Analyzer with CSV Metrics Logging
 */

#define _GNU_SOURCE 

// --- STANDARD HEADERS ---
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <stdbool.h>
#include <math.h>
#include <string.h>
#include <inttypes.h>
#include <time.h>
#include <sys/sysinfo.h>
#include <sys/statvfs.h>
#include <sys/stat.h>
#include <sys/types.h>

// --- LIBRARY HEADERS ---
#include <libhackrf/hackrf.h>
#include <cjson/cJSON.h>

// --- CUSTOM MODULES & DRIVERS ---      
#include "psd.h"
#include "datatypes.h" 
#include "sdr_HAL.h"     
#include "ring_buffer.h" 
#include "zmqsub.h"
#include "zmqpub.h"
#include "bacn_gpio.h"

// =========================================================
// METRICS DEFINITIONS & GLOBALS
// =========================================================
#define CSV_FOLDER "CSV_metrics_psdSDRService"

typedef struct {
    double acq_time_ms;
    double dsp_time_ms;
    double cpu_usage_percent;
    unsigned long ram_used_mb;
    unsigned long swap_used_mb;
    double disk_usage_percent;
} SystemMetrics_t;

// Static buffer for the CSV filename
char csv_filename[256] = {0};

// CPU Load tracking state
unsigned long long prev_user = 0, prev_nice = 0, prev_system = 0, prev_idle = 0;
unsigned long long prev_iowait = 0, prev_irq = 0, prev_softirq = 0, prev_steal = 0;

// =========================================================
// SDR GLOBAL VARIABLES
// =========================================================
hackrf_device* device = NULL;

// Data Structures
ring_buffer_t rb;
zpub_t *publisher = NULL; 

// State Flags
volatile bool stop_streaming = false;
volatile bool config_received = false; 

// Configuration Containers
DesiredCfg_t desired_config = {0};
PsdConfig_t psd_cfg = {0};
SDR_cfg_t hack_cfg = {0};
RB_cfg_t rb_cfg = {0};

// =========================================================
// METRIC HELPER FUNCTIONS
// =========================================================

// Get monotonic time in ms for benchmarking
double get_time_ms() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (ts.tv_sec * 1000.0) + (ts.tv_nsec / 1000000.0);
}

// Retrieve MAC address (Try wlan0, fallback to eth0)
void get_mac_address(char *buffer) {
    char path[128];
    FILE *f;
    const char *interfaces[] = {"wlan0", "eth0", "en0"};
    
    for (int i = 0; i < 3; i++) {
        snprintf(path, sizeof(path), "/sys/class/net/%s/address", interfaces[i]);
        f = fopen(path, "r");
        if (f) {
            if (fgets(buffer, 18, f)) {
                fclose(f);
                buffer[strcspn(buffer, "\n")] = 0; // Remove newline
                return;
            }
            fclose(f);
        }
    }
    strcpy(buffer, "UNKNOWN_MAC");
}

// Calculate CPU usage delta
double get_cpu_load() {
    FILE *fp = fopen("/proc/stat", "r");
    if (!fp) return 0.0;

    unsigned long long user, nice, system, idle, iowait, irq, softirq, steal;
    char buffer[1024];
    if (!fgets(buffer, sizeof(buffer), fp)) { fclose(fp); return 0.0; }
    sscanf(buffer, "cpu  %llu %llu %llu %llu %llu %llu %llu %llu", 
           &user, &nice, &system, &idle, &iowait, &irq, &softirq, &steal);
    fclose(fp);

    unsigned long long prev_idle_total = prev_idle + prev_iowait;
    unsigned long long idle_total = idle + iowait;

    unsigned long long prev_non_idle = prev_user + prev_nice + prev_system + prev_irq + prev_softirq + prev_steal;
    unsigned long long non_idle = user + nice + system + irq + softirq + steal;

    unsigned long long prev_total = prev_idle_total + prev_non_idle;
    unsigned long long total = idle_total + non_idle;

    double total_d = (double)(total - prev_total);
    double id_d = (double)(idle_total - prev_idle_total);
    
    // Update State
    prev_user = user; prev_nice = nice; prev_system = system; prev_idle = idle;
    prev_iowait = iowait; prev_irq = irq; prev_softirq = softirq; prev_steal = steal;

    if (total_d == 0) return 0.0;
    return ((total_d - id_d) / total_d) * 100.0;
}

// Initialize CSV File and Headers
void init_csv_filename() {
    struct stat st = {0};
    if (stat(CSV_FOLDER, &st) == -1) {
        mkdir(CSV_FOLDER, 0777);
    }

    char mac[32];
    get_mac_address(mac);
    // Sanitize MAC for filename
    for(int i=0; mac[i]; i++) { if(mac[i] == ':') mac[i] = '-'; }

    time_t t = time(NULL);
    struct tm tm = *localtime(&t);

    snprintf(csv_filename, sizeof(csv_filename), 
             "%s/%04d%02d%02d_%02d%02d%02d_%s.csv",
             CSV_FOLDER,
             tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday,
             tm.tm_hour, tm.tm_min, tm.tm_sec,
             mac);
    
    FILE *fp = fopen(csv_filename, "a");
    if (fp) {
        fseek(fp, 0, SEEK_END);
        if (ftell(fp) == 0) {
            fprintf(fp, "Timestamp_Epoch,Acq_Time_ms,PSD_Calc_Time_ms,"
                        "CPU_Load_Pct,RAM_Used_MB,RAM_Total_MB,Swap_Used_MB,Disk_Usage_Pct,"
                        "CenterFreq_Hz,RBW_Hz,SampleRate_Hz,Span_Hz,Overlap,Scale,Window,LNA,VGA,Amp,PSD_Bins\n");
        }
        fclose(fp);
    }
}

// Gather System Stats
void collect_system_metrics(SystemMetrics_t *m) {
    struct sysinfo si;
    if (sysinfo(&si) == 0) {
        m->ram_used_mb = (unsigned long)((si.totalram - si.freeram) * si.mem_unit / 1024 / 1024);
        m->swap_used_mb = (unsigned long)((si.totalswap - si.freeswap) * si.mem_unit / 1024 / 1024);
    } else {
        m->ram_used_mb = 0; m->swap_used_mb = 0;
    }

    struct statvfs stat;
    if (statvfs(".", &stat) == 0) {
        unsigned long long total = stat.f_blocks;
        unsigned long long free = stat.f_bfree;
        if (total > 0) m->disk_usage_percent = (1.0 - ((double)free / (double)total)) * 100.0;
        else m->disk_usage_percent = 0.0;
    }

    m->cpu_usage_percent = get_cpu_load();
}

// Write to CSV
void log_to_csv(SystemMetrics_t *m, DesiredCfg_t *cfg, int psd_len) {
    FILE *fp = fopen(csv_filename, "a");
    if (!fp) return;

    struct sysinfo si;
    unsigned long ram_total = 0;
    if(sysinfo(&si) == 0) ram_total = (si.totalram * si.mem_unit) / 1024 / 1024;

    fprintf(fp, "%ld,%.2f,%.2f,%.2f,%lu,%lu,%lu,%.2f,"
                "%" PRIu64 ",%d,%.0f,%.0f,%.2f,%s,%d,%d,%d,%d,%d\n",
            time(NULL), 
            m->acq_time_ms, 
            m->dsp_time_ms,
            m->cpu_usage_percent,
            m->ram_used_mb,
            ram_total,
            m->swap_used_mb,
            m->disk_usage_percent,
            cfg->center_freq,
            cfg->rbw,
            cfg->sample_rate,
            cfg->span,
            cfg->overlap,
            cfg->scale ? cfg->scale : "dBm",
            cfg->window_type,
            cfg->lna_gain,
            cfg->vga_gain,
            cfg->amp_enabled ? 1 : 0,
            psd_len
            );
    fclose(fp);
}

// =========================================================
// CONFIG LOGIC & PARSING
// =========================================================

void print_desired(const DesiredCfg_t *cfg) {
    printf("  [CFG] Freq: %" PRIu64 " | RBW: %d | Scale: %s\n", 
           cfg->center_freq, cfg->rbw, cfg->scale ? cfg->scale : "dBm");
}

int parse_psd_config(const char *json_string, DesiredCfg_t *target) {
    if (json_string == NULL || target == NULL) return -1;

    cJSON *root = cJSON_Parse(json_string);
    if (root == NULL) {
        const char *error_ptr = cJSON_GetErrorPtr();
        if (error_ptr != NULL) fprintf(stderr, "Error before: %s\n", error_ptr);
        return -1;
    }

    // 1. Center Freq
    cJSON *cf = cJSON_GetObjectItemCaseSensitive(root, "center_freq_hz");
    if (cJSON_IsNumber(cf)) target->center_freq = (uint64_t)cf->valuedouble;
    else target->center_freq = 0;

    // 2. RBW
    cJSON *rbw = cJSON_GetObjectItemCaseSensitive(root, "rbw_hz");
    if (cJSON_IsNumber(rbw)) target->rbw = (int)rbw->valuedouble;

    // 3. Sample Rate
    cJSON *sample_rate_json = cJSON_GetObjectItemCaseSensitive(root, "sample_rate_hz");
    if (cJSON_IsNumber(sample_rate_json)) target->sample_rate = sample_rate_json->valuedouble;

    // 4. Span
    cJSON *span_json = cJSON_GetObjectItemCaseSensitive(root, "span");
    if (cJSON_IsNumber(span_json)) target->span = span_json->valuedouble;

    // 5. Overlap
    cJSON *overlap_json = cJSON_GetObjectItemCaseSensitive(root, "overlap");
    if (cJSON_IsNumber(overlap_json)) target->overlap = overlap_json->valuedouble;

    // 6. Scale
    cJSON *scale = cJSON_GetObjectItemCaseSensitive(root, "scale");
    if (cJSON_IsString(scale) && (scale->valuestring != NULL)) {
        target->scale = strdup(scale->valuestring);
    } else {
        target->scale = NULL;
    }

    // 7. Window
    cJSON *win = cJSON_GetObjectItemCaseSensitive(root, "window");
    if (cJSON_IsString(win)) {
        target->window_type = get_window_type_from_string(win->valuestring);
    } else {
        target->window_type = RECTANGULAR_TYPE;
    }

    // 8. Gains
    cJSON *lna = cJSON_GetObjectItemCaseSensitive(root, "lna_gain");
    if (cJSON_IsNumber(lna)) target->lna_gain = (int)lna->valuedouble;

    cJSON *vga = cJSON_GetObjectItemCaseSensitive(root, "vga_gain");
    if (cJSON_IsNumber(vga)) target->vga_gain = (int)vga->valuedouble;

    // 9. Amp
    cJSON *amp = cJSON_GetObjectItemCaseSensitive(root, "antenna_amp");
    if (cJSON_IsBool(amp)) target->amp_enabled = cJSON_IsTrue(amp);

    target->ppm_error = 0;
    cJSON_Delete(root);
    return 0;
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

// =========================================================
// HARDWARE CALLBACKS & RECOVERY
// =========================================================

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
    
    // 1. Metrics Init
    init_csv_filename();
    get_cpu_load(); // Prime the CPU delta calculation

    // 2. ZMQ & SDR Init
    zsub_t *sub = zsub_init("acquire", handle_psd_message);
    if (!sub) return 1;
    zsub_start(sub);

    publisher = zpub_init();
    if (!publisher) return 1;

    if (hackrf_init() != HACKRF_SUCCESS) return 1;
    
    if (hackrf_open(&device) != HACKRF_SUCCESS) {
        fprintf(stderr, "[SYSTEM] Warning: Initial Open failed. Will retry in loop.\n");
    }

    select_ANTENNA(1);

    // 3. Continuous Loop
    bool needs_recovery = false; 

    while (1) {
        // Variables for timing
        double t_start_acq = 0, t_end_acq = 0;
        double t_start_dsp = 0, t_end_dsp = 0;

        // A. Wait for ZMQ Command
        if (!config_received) {
            usleep(10000); 
            continue;
        }

        if (device == NULL) {
            needs_recovery = true;
            goto error_handler;
        }

        // B. Setup Acquisition
        rb_init(&rb, rb_cfg.rb_size);
        stop_streaming = false;

        hackrf_apply_cfg(device, &hack_cfg);
        
        // --- START ACQ TIMER ---
        t_start_acq = get_time_ms();

        hackrf_start_rx(device, rx_callback, NULL);

        // C. Wait for Buffer Fill
        int safety_timeout = 500; 
        while ((rb_available(&rb) < rb_cfg.total_bytes) && (safety_timeout > 0)) {
            usleep(10000); 
            safety_timeout--;
        }

        stop_streaming = true;
        hackrf_stop_rx(device);

        // --- STOP ACQ TIMER ---
        t_end_acq = get_time_ms();

        if (safety_timeout <= 0) {
            needs_recovery = true;
            goto error_handler;
        }

        // D. DSP Processing
        int8_t* linear_buffer = malloc(rb_cfg.total_bytes);
        if (linear_buffer) {
            rb_read(&rb, linear_buffer, rb_cfg.total_bytes);
            
            // --- START DSP TIMER ---
            t_start_dsp = get_time_ms();

            signal_iq_t* sig = load_iq_from_buffer(linear_buffer, rb_cfg.total_bytes);
            double* freq = malloc(psd_cfg.nperseg * sizeof(double));
            double* psd = malloc(psd_cfg.nperseg * sizeof(double));

            if (freq && psd && sig) {
                execute_welch_psd(sig, &psd_cfg, freq, psd);
                scale_psd(psd, psd_cfg.nperseg, desired_config.scale);
                
                // --- STOP DSP TIMER ---
                t_end_dsp = get_time_ms();

                publish_results(freq, psd, psd_cfg.nperseg);

                // --- LOG METRICS ---
                SystemMetrics_t metrics;
                metrics.acq_time_ms = t_end_acq - t_start_acq;
                metrics.dsp_time_ms = t_end_dsp - t_start_dsp;
                collect_system_metrics(&metrics);
                
                log_to_csv(&metrics, &desired_config, psd_cfg.nperseg);
                printf("[METRICS] Logged cycle to CSV.\n");
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