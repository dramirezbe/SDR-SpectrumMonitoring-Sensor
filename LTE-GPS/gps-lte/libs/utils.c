/**
 * @file utils.c
 * @brief Implementation of utility functions.
 */

#include "utils.h"
#include <string.h>
#include <curl/curl.h>

// System Headers for MAC Address and Sockets
#include <sys/socket.h>
#include <sys/ioctl.h>
#include <net/if.h>
#include <unistd.h>
#include <netinet/in.h> // Required for IPPROTO_IP definition

// --- Environment Variable Helper ---

char *getenv_c(const char *key) {
    FILE *file;
    char line[1024];
    size_t key_len = strlen(key);

    file = fopen(".env", "r");
    if (file == NULL) {
        return NULL; 
    }

    // Prepare search prefix, e.g., "API_URL="
    // We add +2 for '=' and null terminator
    char *search_prefix = malloc(key_len + 2);
    if (!search_prefix) {
        fclose(file);
        return NULL;
    }
    snprintf(search_prefix, key_len + 2, "%s=", key);

    while (fgets(line, sizeof(line), file) != NULL) {
        size_t len = strlen(line);
        // Remove newline
        if (len > 0 && line[len - 1] == '\n') {
            line[len - 1] = '\0';
            len--;
        }

        if (strncmp(line, search_prefix, key_len + 1) == 0) {
            // Found it. Value is after the '='
            const char *value_start = line + key_len + 1;
            char *result = strdup(value_start);
            
            free(search_prefix);
            fclose(file); 
            return result;
        }
    }

    free(search_prefix);
    fclose(file);
    return NULL;
}

// --- Network / MAC Helper ---

int get_wlan0_mac(char *mac_out) {
    struct ifreq s;
    // Uses IPPROTO_IP (requires netinet/in.h)
    int fd = socket(PF_INET, SOCK_DGRAM, IPPROTO_IP);
    
    if (fd < 0) return -1;

    memset(&s, 0, sizeof(struct ifreq));
    // Safe string copy for interface name
    strncpy(s.ifr_name, "wlan0", IFNAMSIZ - 1);
    
    // IOCTL request for Hardware Address
    if (ioctl(fd, SIOCGIFHWADDR, &s) == 0) {
        unsigned char *m = (unsigned char *)s.ifr_addr.sa_data;
        snprintf(mac_out, MAC_ADDR_LENGTH, "%02x:%02x:%02x:%02x:%02x:%02x",
                 m[0], m[1], m[2], m[3], m[4], m[5]);
        close(fd);
        return 0;
    }
    
    close(fd);
    return -1;
}

// --- HTTP POST Helper ---

int post_gps_data(
    const char *base_api_url,
    const char *altitude_str,
    const char *latitude_str,
    const char *longitude_str)
{
    CURL *curl;
    CURLcode res;
    char full_url[256];
    char json_payload[MAX_JSON_LENGTH];
    char mac_address[MAC_ADDR_LENGTH];

    // 1. Get MAC
    if (get_wlan0_mac(mac_address) != 0) {
        fprintf(stderr, "[UTILS] Error: Could not retrieve wlan0 MAC address.\n");
        return 1; 
    }

    // 2. Parse Floats
    float alt = (float)atof(altitude_str);
    float lat = (float)atof(latitude_str);
    float lng = (float)atof(longitude_str);

    // 3. Format JSON
    int written = snprintf(json_payload, MAX_JSON_LENGTH, 
        "{\"mac\": \"%s\", \"lat\": %.4f, \"lng\": %.4f, \"alt\": %.1f}",
        mac_address, lat, lng, alt);

    if (written < 0 || written >= MAX_JSON_LENGTH) {
        fprintf(stderr, "[UTILS] Error: JSON buffer overflow.\n");
        return 2;
    }

    // 4. Construct URL
    // Ensure we don't overflow the URL buffer
    snprintf(full_url, sizeof(full_url), "%s/gps", base_api_url);

    // 5. Send Request
    curl = curl_easy_init();
    if(curl) {
        curl_easy_setopt(curl, CURLOPT_URL, full_url);
        curl_easy_setopt(curl, CURLOPT_POST, 1L);
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS, json_payload);

        struct curl_slist *headers = NULL;
        headers = curl_slist_append(headers, "Content-Type: application/json");
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);

        // Perform
        res = curl_easy_perform(curl);

        if(res != CURLE_OK) {
            fprintf(stderr, "[UTILS] curl_easy_perform() failed: %s\n", 
                    curl_easy_strerror(res));
            curl_slist_free_all(headers);
            curl_easy_cleanup(curl);
            return 3;
        }

        curl_slist_free_all(headers);
        curl_easy_cleanup(curl);
    } else {
        return 4; // Curl init failed
    }

    return 0; // Success
}