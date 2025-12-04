// functions.c
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
#include <ctype.h>

// Include your existing custom headers
#include "Modules/psd.h"
#include "Modules/utils.h"
#include "Modules/datatypes.h" 
#include "Drivers/ring_buffer.h" 
#include "Drivers/sdr_HAL.h"
#include "Drivers/zmqsub.h"
#include "Drivers/zmqpub.h"
#include "Drivers/bacn_gpio.h"
#include "Drivers/bacn_LTE.h"
#include "Drivers/bacn_GPS.h"

// ----------------------------------------------------------------------
// Global State & Config (DEFINITIONS)
// ----------------------------------------------------------------------
st_uart LTE;
gp_uart GPS;
GPSCommand GPSInfo;
bool LTE_open = false;
bool GPS_open = false;
hackrf_device* device = NULL;
ring_buffer_t rb;
zpub_t *publisher = NULL; 
volatile bool stop_streaming = false;
volatile bool config_received = false; 

DesiredCfg_t desired_config = {0};
PsdConfig_t psd_cfg = {0};
SDR_cfg_t hack_cfg = {0};
RB_cfg_t rb_cfg = {0};

// Internal constants for this file
#define CMD_BUF 256
#define IP_BUF 64

// ----------------------------------------------------------------------
// Helper Functions
// ----------------------------------------------------------------------

void run_cmd(const char *cmd) {
    printf("[CMD] %s\n", cmd);
    system(cmd);
}

int get_ppp_ip(char *ip) {
    FILE *fp;
    char cmd[] = "ip -o -4 addr show dev ppp0 | awk '{print $4}' | cut -d/ -f1";
    char buffer[IP_BUF] = {0};

    fp = popen(cmd, "r");
    if (!fp) return 0;

    if (fgets(buffer, sizeof(buffer), fp) != NULL) {
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

// Moves the messy connection logic out of main
int establish_ppp_connection(char* ip_buffer) {
    printf("Starting PPP connection...\n");
    run_cmd("sudo pon rnet");
    sleep(5); 

    if (!get_ppp_ip(ip_buffer)) {
        printf("No IP address assigned! Restarting PPP...\n");
        run_cmd("sudo poff rnet");
        sleep(10);
        run_cmd("sudo pon rnet");
        sleep(5);

        if (!get_ppp_ip(ip_buffer)) {
            printf("PPP failed again. No IP assigned.\n");
            return -1;
        }
    }
    printf("PPP connected. IP = %s\n", ip_buffer);
    return 0;
}

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

// ----------------------------------------------------------------------
// GPS Logic
// ----------------------------------------------------------------------

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

// ----------------------------------------------------------------------
// Hardware & Comm Callbacks
// ----------------------------------------------------------------------

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