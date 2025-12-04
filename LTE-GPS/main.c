// main.c
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <stdbool.h>
#include <pthread.h>
#include <libhackrf/hackrf.h>
#include <inttypes.h>

// We still need the Type Definitions
#include "Modules/psd.h"
#include "Modules/datatypes.h" 
#include "Drivers/ring_buffer.h" 
#include "Drivers/zmqsub.h"
#include "Drivers/zmqpub.h"
#include "Drivers/bacn_LTE.h"
#include "Drivers/bacn_GPS.h"

// =========================================================
// MANUAL EXTERNS (Variables defined in functions.c)
// =========================================================
extern st_uart LTE;
extern gp_uart GPS;
extern hackrf_device* device;
extern ring_buffer_t rb;
extern zpub_t *publisher;
extern volatile bool stop_streaming;
extern volatile bool config_received;

extern DesiredCfg_t desired_config;
extern PsdConfig_t psd_cfg;
extern SDR_cfg_t hack_cfg;
extern RB_cfg_t rb_cfg;

// =========================================================
// MANUAL PROTOTYPES (Functions defined in functions.c)
// =========================================================
int establish_ppp_connection(char* ip_buffer);
void* gps_monitor_thread(void *arg);
void handle_psd_message(const char *payload);
int rx_callback(hackrf_transfer* transfer);
void publish_results(double* freq_array, double* psd_array, int length);
int recover_hackrf(void);

// =========================================================
// MAIN LOGIC
// =========================================================
int main() {
    // 1. Hardware Init
    if(init_usart(&LTE) != 0) return -1;
    if(init_usart1(&GPS) != 0) return -1;

    // 2. Internet Connection
    char ip[64];
    if (establish_ppp_connection(ip) != 0) return 1;

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
        fprintf(stderr, "[SYSTEM] Warning: Initial Open failed.\n");
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