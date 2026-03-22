/**
 * @file utils.c
 * @brief Implementation of utility functions.
 */

#include "utils.h"

#include <cjson/cJSON.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <errno.h>

#define SHM_PERSISTENT_PATH_GPS "/dev/shm/persistent.json"

static char *read_all_from_fd_gps(int fd) {
    if (lseek(fd, 0, SEEK_SET) < 0) {
        return NULL;
    }

    size_t cap = 4096;
    size_t len = 0;
    char *buffer = (char *)malloc(cap);
    if (!buffer) {
        return NULL;
    }

    while (1) {
        if (len + 2048 > cap) {
            cap *= 2;
            char *tmp = (char *)realloc(buffer, cap);
            if (!tmp) {
                free(buffer);
                return NULL;
            }
            buffer = tmp;
        }

        ssize_t n = read(fd, buffer + len, cap - len - 1);
        if (n < 0) {
            free(buffer);
            return NULL;
        }
        if (n == 0) {
            break;
        }
        len += (size_t)n;
    }

    buffer[len] = '\0';
    return buffer;
}

static cJSON *parse_root_or_empty_gps(const char *text) {
    if (!text || text[0] == '\0') {
        return cJSON_CreateObject();
    }

    cJSON *root = cJSON_Parse(text);
    if (!root || !cJSON_IsObject(root)) {
        if (root) {
            cJSON_Delete(root);
        }
        return cJSON_CreateObject();
    }
    return root;
}

static cJSON *parse_value_or_string_gps(const char *value_text) {
    if (!value_text) {
        return cJSON_CreateNull();
    }

    cJSON *parsed = cJSON_Parse(value_text);
    if (parsed) {
        return parsed;
    }
    return cJSON_CreateString(value_text);
}

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

    printf("[UTILS] Datos GPS (%.6f, %.6f, %.1f)enviados con exito.\n", final_lat, final_lng, alt);

    return 0; // Éxito
}

int shm_add_to_persistent_gps(const char *key, const char *value_text) {
    if (!key || key[0] == '\0') {
        return -1;
    }

    int fd = open(SHM_PERSISTENT_PATH_GPS, O_RDWR | O_CREAT, 0666);
    if (fd < 0) {
        return -1;
    }

    if (flock(fd, LOCK_EX) != 0) {
        close(fd);
        return -1;
    }

    int rc = -1;
    char *content = read_all_from_fd_gps(fd);
    cJSON *root = parse_root_or_empty_gps(content);
    free(content);

    if (!root) {
        goto cleanup;
    }

    cJSON *new_item = parse_value_or_string_gps(value_text);
    if (!new_item) {
        cJSON_Delete(root);
        goto cleanup;
    }

    cJSON_DeleteItemFromObjectCaseSensitive(root, key);
    cJSON_AddItemToObject(root, key, new_item);

    char *out = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (!out) {
        goto cleanup;
    }

    size_t out_len = strlen(out);

    if (ftruncate(fd, 0) != 0) {
        free(out);
        goto cleanup;
    }

    if (lseek(fd, 0, SEEK_SET) < 0) {
        free(out);
        goto cleanup;
    }

    const char *ptr = out;
    size_t remaining = out_len;
    while (remaining > 0) {
        ssize_t written = write(fd, ptr, remaining);
        if (written < 0) {
            if (errno == EINTR) {
                continue;
            }
            free(out);
            goto cleanup;
        }
        ptr += (size_t)written;
        remaining -= (size_t)written;
    }

    free(out);

    if (fsync(fd) != 0) {
        goto cleanup;
    }

    rc = 0;

cleanup:
    flock(fd, LOCK_UN);
    close(fd);
    return rc;
}

char *shm_consult_persistent_gps(const char *key) {
    if (!key || key[0] == '\0') {
        return NULL;
    }

    int fd = open(SHM_PERSISTENT_PATH_GPS, O_RDONLY);
    if (fd < 0) {
        return NULL;
    }

    if (flock(fd, LOCK_SH) != 0) {
        close(fd);
        return NULL;
    }

    char *result = NULL;
    char *content = read_all_from_fd_gps(fd);
    cJSON *root = parse_root_or_empty_gps(content);
    free(content);

    if (root) {
        cJSON *item = cJSON_GetObjectItemCaseSensitive(root, key);
        if (item) {
            if (cJSON_IsString(item) && item->valuestring) {
                result = strdup(item->valuestring);
            } else {
                result = cJSON_PrintUnformatted(item);
            }
        }
        cJSON_Delete(root);
    }

    flock(fd, LOCK_UN);
    close(fd);
    return result;
}

char *ashm_consult_persistent_gps(const char *key) {
    return shm_consult_persistent_gps(key);
}

/** @} */