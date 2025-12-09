/**
 * @file gps-lte.c
 * @brief GPS handler, sends gps from /gps endopoint each 10 secs. Handle priority of internet interfaces.
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

// --- CUSTOM MODULES & DRIVERS ---
#include "utils.h"       
#include "bacn_LTE.h"
#include "bacn_GPS.h"

// =========================================================
// DEFINITIONS & MACROS
// =========================================================
#define CMD_BUF 256
#define IP_BUF 64

// =========================================================
// GLOBAL VARIABLES
// =========================================================
// Hardware Handles
st_uart LTE;
gp_uart GPS;

// Data Structures
GPSCommand GPSInfo; 

// State Flags
bool LTE_open = false;
bool GPS_open = false;
volatile bool stop_streaming = false; // Controls thread lifecycles

// =========================================================
// FUNCTION PROTOTYPES
// =========================================================
void run_cmd(const char *cmd);
bool is_valid_gps_data(const char* lat_str, const char* lon_str);
void* gps_monitor_thread(void *arg);

// =========================================================
// HELPER IMPLEMENTATIONS
// =========================================================

void run_cmd(const char *cmd) {
    printf("[CMD] %s\n", cmd);
    system(cmd);
}

int get_wlan_ip(char *ip) {
    FILE *fp;
    char cmd[] = "ip -o -4 addr show wlan0 | awk '{print $4}' | cut -d/ -f1";
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

int get_eth_ip(char *ip) {
    FILE *fp;
    char cmd[] = "ip -o -4 addr show eth0 | awk '{print $4}' | cut -d/ -f1";
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

int get_ppp_ip(char *ip) {
    FILE *fp;
    char cmd[] = "ip -o -4 addr show ppp0 | awk '{print $4}' | cut -d/ -f1";
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

// --- GPS Logic ---

bool is_valid_gps_data(const char* lat_str, const char* lon_str) {
    if (!lat_str || !lon_str) return false;
    if (strlen(lat_str) < 1 || strlen(lon_str) < 1) return false;

    char *endptr_lat, *endptr_lon;
    double lat = strtod(lat_str, &endptr_lat);
    double lon = strtod(lon_str, &endptr_lon);

    if (lat_str == endptr_lat || lon_str == endptr_lon) return false;
    // Check ranges
    if (lat < -90.0 || lat > 90.0) return false;
    if (lon < -180.0 || lon > 180.0) return false;
    // Simple check to reject 0.0, 0.0 (often default init values)
    if (fabs(lat) < 0.0001 && fabs(lon) < 0.0001) return false;

    return true;
}

void *gps_monitor_thread(void *arg) {
    char *api_url = (char *)arg;
    printf("[GPS-THREAD] Started. Reporting to: %s\n", api_url ? api_url : "NULL");

    while (!stop_streaming) {
        // 1. Read/Refresh GPS Data from Hardware
        //GPS_Read(&GPS); 

        // 2. Validate and Send
        if (api_url != NULL) {
            if (is_valid_gps_data(GPSInfo.Latitude, GPSInfo.Longitude)) {
                printf("[GPS-THREAD] Sending Fix: %s, %s\n", GPSInfo.Latitude, GPSInfo.Longitude);
                post_gps_data(api_url, GPSInfo.Altitude, GPSInfo.Latitude, GPSInfo.Longitude);
            } else {
                // Optional: print only occasionally to avoid log spam
                // printf("[GPS-THREAD] No valid fix yet.\n");
            }
        }
        
        // 3. Wait 10 seconds before next cycle
        sleep(10);
    }
    return NULL;
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

    // 2. Network / Internet Setup
    char ip[IP_BUF];
    sleep(5); // Give interfaces a moment

    if(get_eth_ip(ip)) {
        printf("IP address assigned to Ethernet: %s\n", ip);
    } else if(get_wlan_ip(ip)) {
        printf("IP address assigned to WiFi: %s\n", ip);
    } else {
        printf("Starting PPP connection...\n");
        run_cmd("sudo pon rnet");
        sleep(10);
        
        if(!get_ppp_ip(ip)) {
            printf("No IP address assigned! Restarting PPP...\n");
            run_cmd("sudo poff rnet");
            sleep(5);
            run_cmd("sudo pon rnet");
            sleep(10);

            if(!get_ppp_ip(ip)) {
                printf("PPP failed again. No IP assigned.\n");
            }
        }
        if(strlen(ip) > 0) {
            printf("PPP connected. IP = %s\n", ip);
        }
    }

    // 3. Environment & Threading
    char *api_url = getenv_c("API_URL"); 
    pthread_t gps_tid;
    
    if (api_url != NULL) {
        printf("API URL found: %s. Starting GPS thread.\n", api_url);
        int err = pthread_create(&gps_tid, NULL, gps_monitor_thread, (void *)api_url);
        if (err != 0) {
             fprintf(stderr, "Failed to create GPS thread\n");
        }
    } else {
        printf("WARN: API_URL not set. GPS thread will not start.\n");
    }

    // 4. Main Loop
    printf("System Running. Press Ctrl+C to exit.\n");
    
    while (1) {
        // Adjusted the label 'Longitude' -> 'Altitude' for the 3rd parameter
        printf("Latitude: %s, Longitude: %s, Altitude: %s\n", GPSInfo.Latitude, GPSInfo.Longitude, GPSInfo.Altitude);
        sleep(1); 
    }

    // Cleanup
    stop_streaming = true;
    if (api_url) pthread_join(gps_tid, NULL);
    
    return 0;
}