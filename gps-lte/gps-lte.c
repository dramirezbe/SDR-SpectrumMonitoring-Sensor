/**
 * @file gps-lte.c
 * @brief GPS handler. Sends gps from /gps endpoint every 10 cycles. 
 * Handles priority of internet interfaces and monitors connectivity.
 */

#define _GNU_SOURCE 

// --- STANDARD HEADERS ---
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <stdbool.h>
#include <math.h>
#include <string.h>
// Note: pthread.h removed as requested

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
bool GPSRDY = false;

// =========================================================
// FUNCTION PROTOTYPES
// =========================================================
void run_cmd(const char *cmd);
int get_wlan_ip(char *ip);
int get_eth_ip(char *ip);
int get_ppp_ip(char *ip);

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
    sleep(30); // Give interfaces a moment

    if(get_eth_ip(ip)) {
        printf("IP address assigned to Ethernet: %s\n", ip);
    } else {
        sleep(30);
        if(get_wlan_ip(ip)) {
            printf("IP address assigned to WiFi: %s\n", ip);
        } else {
            sleep(30);
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
    } 
    
    // 3. Environment Setup
    char *api_url = getenv_c("API_URL"); 
    if (api_url == NULL) {
        printf("WARN: API_URL not set. Data sending will be skipped.\n");
    } else {
        printf("API URL found: %s\n", api_url);
    }

    // 4. Main Loop Variables
    const char *ip_address = "8.8.8.8";
    char ping_cmd[100];
    snprintf(ping_cmd, sizeof(ping_cmd), "ping -c 1 -W 1 %s", ip_address);
    
    int count = 0;
    int tryRB = 0;
    int status = 0;
    int ping_result = 0;
    printf("System Running. Press Ctrl+C to exit.\n");

    while (1) {
        // Assume GPSRDY is set by an Interrupt Service Routine (ISR) or separate RX handler
        if(GPSRDY) {
            GPSRDY = false;
            count++;

            // Trigger every 10 GPS updates
            if(count >= 10) { 
                count = 0; // Reset counter
		printf("Latitude: %s, Longitude: %s, Altitude: %s\n", GPSInfo.Latitude, GPSInfo.Longitude, GPSInfo.Altitude);
                // --- A. SEND DATA ---
                status = post_gps_data(api_url, GPSInfo.Altitude, GPSInfo.Latitude, GPSInfo.Longitude);

                if (status == 0) {
                        printf("Success: Data posted to %s\n", api_url);
                } else {
                	fprintf(stderr, "Failed with error code: %d\n", status);
                }

                // --- B. CHECK CONNECTIVITY ---
                // We run the ping command HERE to get the current status
                ping_result = system(ping_cmd); 

                if (ping_result == 0) {
                    // Success (0 return code)
                    printf("Ping to %s successful.\n", ip_address);
                    tryRB = 0;
                } else {
                    // Failure
                    printf("Ping to %s failed. Retry count: %d\n", ip_address, tryRB + 1);
                    tryRB++;
                    
                    if(tryRB >= 6) {
                        printf("CRITICAL: Network down for too long. Rebooting...\n");
                        system("sudo reboot");
                    }
                }
            } 
        }
        
        // Slight delay to prevent 100% CPU usage if GPSRDY is polling based
        usleep(1000); 
    }

    return 0;
}
