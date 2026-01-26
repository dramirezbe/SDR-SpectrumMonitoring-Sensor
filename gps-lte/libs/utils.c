/**
 * @file utils.c
 * @brief Implementation of utility functions.
 */

#include "utils.h"

/**
 * @addtogroup utils_gpslte
 * @{
 */

char *getenv_c_gps(const char *key) {
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


double nmea_to_decimal(double raw_coord) {
    double degrees = floor(raw_coord / 100.0);
    double minutes = raw_coord - (degrees * 100.0);
    return degrees + (minutes / 60.0);
}

int post_gps_data(
    const char *base_api_url,
    const char *altitude_str,
    const char *latitude_str,
    const char *longitude_str)
{
    // --- 1. VALIDACIÓN DE SEGURIDAD (Evita el Segmentation Fault) ---
    // Si cualquiera de estos punteros es NULL o el string está vacío, salimos de la función.
    if (base_api_url == NULL || altitude_str == NULL || 
        latitude_str == NULL || longitude_str == NULL) {
        fprintf(stderr, "[UTILS] Error: Datos NULL recibidos. Saltando envío...\n");
        return -1;
    }

    if (strlen(latitude_str) == 0 || strlen(longitude_str) == 0) {
        fprintf(stderr, "[UTILS] Esperando fijación de GPS (strings vacíos). Saltando...\n");
        return -1;
    }

    CURL *curl;
    CURLcode res;
    char full_url[256];
    char json_payload[MAX_JSON_LENGTH];
    char mac_address[MAC_ADDR_LENGTH];

    // Obtener MAC
    if (get_wlan0_mac(mac_address) != 0) {
        fprintf(stderr, "[UTILS] Error: No se pudo obtener la MAC de wlan0.\n");
        return 1; 
    }

    // --- 2. Parseo y Conversión ---
    // atof es seguro aquí porque ya validamos que no sean NULL
    double raw_lat = atof(latitude_str);
    double raw_lng = atof(longitude_str);
    double alt = atof(altitude_str);

    double final_lat = nmea_to_decimal(raw_lat);
    double final_lng = nmea_to_decimal(raw_lng);

    // --- 3. Corrección de Hemisferio (Oeste para Colombia) ---
    if (final_lng > 0) {
        final_lng = -final_lng; 
    }

    // --- 4. Formatear JSON ---
    int written = snprintf(json_payload, MAX_JSON_LENGTH, 
        "{\"mac\": \"%s\", \"lat\": %.6f, \"lng\": %.6f, \"alt\": %.1f}",
        mac_address, final_lat, final_lng, alt);

    if (written < 0 || written >= MAX_JSON_LENGTH) {
        fprintf(stderr, "[UTILS] Error: JSON buffer overflow.\n");
        return 2;
    }

    // Construir URL completa
    snprintf(full_url, sizeof(full_url), "%s/gps", base_api_url);

    // --- 5. Enviar con CURL ---
    curl = curl_easy_init();
    if(curl) {
        curl_easy_setopt(curl, CURLOPT_URL, full_url);
        curl_easy_setopt(curl, CURLOPT_POST, 1L);
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS, json_payload);

        struct curl_slist *headers = NULL;
        headers = curl_slist_append(headers, "Content-Type: application/json");
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);

        // Timeout para evitar que el programa se quede colgado si no hay internet
        curl_easy_setopt(curl, CURLOPT_TIMEOUT, 5L);

        res = curl_easy_perform(curl);

        if(res != CURLE_OK) {
            fprintf(stderr, "[UTILS] Error en CURL: %s\n", curl_easy_strerror(res));
            curl_slist_free_all(headers);
            curl_easy_cleanup(curl);
            return 3;
        }

        curl_slist_free_all(headers);
        curl_easy_cleanup(curl);
    } else {
        return 4;
    }

    return 0; // Éxito
}

/** @} */