#ifndef UTILS_H
#define UTILS_H

#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <string.h>

/**
 * @brief Reads a specific key from a local .env file.
 * @param key The key to search for (e.g., "API_URL").
 * @return char* Dynamically allocated string containing the value. 
 * Caller must free() the result. Returns NULL if not found.
 */
char *getenv_c(const char *key);

#endif