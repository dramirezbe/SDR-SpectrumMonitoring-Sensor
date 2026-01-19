/**
 * @file utils.c
 * @brief Implementación de funciones de utilidad y manejo de archivos de configuración.
 */

#include "utils.h"

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