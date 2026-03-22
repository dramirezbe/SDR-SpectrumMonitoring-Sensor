/**
 * @file utils.h
 * @brief Utility functions for Environment, Network, and GPS HTTP POSTs.
 * @note This module is self-contained and requires libcurl.
 */

#ifndef GPS_LTE_UTILS_H
#define GPS_LTE_UTILS_H

#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <math.h>
#include <string.h>
#include <curl/curl.h>
#include <sys/socket.h>
#include <sys/ioctl.h>
#include <net/if.h>
#include <linux/if.h>
#include <unistd.h>
#include <netinet/in.h>

/**
 * @defgroup utils_gpslte Utilities GPS-LTE
 * @ingroup gps_binary
 * @brief Utility functions for Environment, Network, and GPS HTTP POSTs.
 * @{
 */

#define MAX_URL_LENGTH 1024
#define MAX_JSON_LENGTH 256
#define MAC_ADDR_LENGTH 18

/**
 * @brief Reads a specific key from a local .env file.
 * @param key The key to search for (e.g., "API_URL").
 * @return char* Dynamically allocated string containing the value. 
 * Caller must free() the result. Returns NULL if not found.
 */
char *getenv_c_gps(const char *key);

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

/**
 * @brief Adds or updates a key in /dev/shm/persistent.json (GPS-LTE variant).
 *
 * Uses exclusive file lock + fsync for safe multi-process writes,
 * matching Python ShmStore protection semantics.
 *
 * @param key JSON key to insert/update.
 * @param value_text Value as text. If valid JSON, it is stored typed;
 * otherwise it is stored as JSON string.
 * @return int 0 on success, -1 on error.
 */
int shm_add_to_persistent_gps(const char *key, const char *value_text);

/**
 * @brief Reads a key from /dev/shm/persistent.json (GPS-LTE variant).
 *
 * Uses shared file lock for safe concurrent reads.
 * - If JSON value is string: returns raw string content (no quotes).
 * - Otherwise: returns unformatted JSON text.
 *
 * @param key JSON key to query.
 * @return char* Heap-allocated string (caller must free), or NULL on not found/error.
 */
char *shm_consult_persistent_gps(const char *key);

/**
 * @brief Compatibility alias for shm_consult_persistent_gps.
 * @param key JSON key to query.
 * @return char* Same return contract as shm_consult_persistent_gps.
 */
char *ashm_consult_persistent_gps(const char *key);

/** @}  */

#endif // GPS_LTE_UTILS_H