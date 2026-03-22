/**
 * @file utils.c
 * @brief Implementación de funciones de utilidad y manejo de archivos de configuración.
 */

#include "utils.h"

#include <cjson/cJSON.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>

#define SHM_PERSISTENT_PATH "/dev/shm/persistent.json"

static char *read_all_from_fd(int fd) {
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

static cJSON *parse_root_or_empty(const char *text) {
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

static cJSON *parse_value_or_string(const char *value_text) {
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
 * @addtogroup util_module
 * @{
 */


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

int shm_add_to_persistent(const char *key, const char *value_text) {
    if (!key || key[0] == '\0') {
        return -1;
    }

    int fd = open(SHM_PERSISTENT_PATH, O_RDWR | O_CREAT, 0666);
    if (fd < 0) {
        return -1;
    }

    if (flock(fd, LOCK_EX) != 0) {
        close(fd);
        return -1;
    }

    int rc = -1;
    char *content = read_all_from_fd(fd);
    cJSON *root = parse_root_or_empty(content);
    free(content);

    if (!root) {
        goto cleanup;
    }

    cJSON *new_item = parse_value_or_string(value_text);
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

char *shm_consult_persistent(const char *key) {
    if (!key || key[0] == '\0') {
        return NULL;
    }

    int fd = open(SHM_PERSISTENT_PATH, O_RDONLY);
    if (fd < 0) {
        return NULL;
    }

    if (flock(fd, LOCK_SH) != 0) {
        close(fd);
        return NULL;
    }

    char *result = NULL;
    char *content = read_all_from_fd(fd);
    cJSON *root = parse_root_or_empty(content);
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

char *ashm_consult_persistent(const char *key) {
    return shm_consult_persistent(key);
}

/** @} */