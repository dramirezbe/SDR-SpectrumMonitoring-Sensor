/**
 * @file utils.h
 * @brief Utility functions for Environment, Network, and GPS HTTP POSTs.
 * @note This module is self-contained and requires libcurl.
 */

#ifndef UTILS_H
#define UTILS_H

#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <math.h>

// Macros
#define MAX_URL_LENGTH 1024
#define MAX_JSON_LENGTH 256
#define MAC_ADDR_LENGTH 18

/**
 * @brief Reads a specific key from a local .env file.
 * @param key The key to search for (e.g., "API_URL").
 * @return char* Dynamically allocated string containing the value. 
 * Caller must free() the result. Returns NULL if not found.
 */
char *getenv_c(const char *key);

/**
 * @brief Retrieves the MAC address of the wlan0 interface.
 * @param mac_out Buffer to store the result (must be at least MAC_ADDR_LENGTH).
 * @return int 0 on success, -1 on failure.
 */
int get_wlan0_mac(char *mac_out);

/**
 * @brief Converts coordinates to JSON and POSTs them via HTTP.
 * @note Automatically retrieves the wlan0 MAC address to include in the JSON.
 * * @param base_api_url The server URL (e.g., "http://myserver.com").
 * @param altitude_str Altitude as string.
 * @param latitude_str Latitude as string.
 * @param longitude_str Longitude as string.
 * @return int 0 on success, non-zero on failure.
 */
int post_gps_data(
    const char *base_api_url,
    const char *altitude_str,
    const char *latitude_str,
    const char *longitude_str
);

#endif // UTILS_H