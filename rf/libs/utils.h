/**
 * @file utils.h
 * @brief Funciones de utilidad general para el sistema.
 * * Este archivo contiene funciones auxiliares, como la lectura de 
 * variables de entorno personalizadas desde archivos locales.
 */

#ifndef UTILS_H
#define UTILS_H

#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <string.h>
#include <stdint.h>


/**
 * @defgroup util_module Utils
 * @ingroup rf_binary
 * @brief Utilidades varias
 * @{
 */

/**
 * @brief Lee el valor de una clave específica desde un archivo .env local.
 * * Busca en el archivo ".env" una línea que comience con la clave y el signo '='.
 * Útil para cargar configuraciones sin depender de las variables de entorno del sistema.
 * * @param key La clave que se desea buscar (ej. "API_URL").
 * @return char* Cadena de caracteres con el valor asignado. 
 * @note El llamador es responsable de liberar (free()) la memoria del resultado.
 * @retval NULL Si el archivo no existe o la clave no se encuentra.
 */
char *getenv_c(const char *key);

/**
 * @brief Agrega/actualiza una clave en /dev/shm/persistent.json de forma segura.
 *
 * Implementa protección concurrente con file-lock exclusivo (flock) y
 * persistencia con fsync, emulando el patrón de ShmStore en Python.
 *
 * @param key Clave JSON a insertar/actualizar.
 * @param value_text Valor en texto. Si es JSON válido (número, bool, objeto, array,
 *                   string con comillas), se guarda tipado. Si no, se guarda como string.
 * @return int 0 en éxito, -1 en error.
 */
int shm_add_to_persistent(const char *key, const char *value_text);

/**
 * @brief Consulta una clave en /dev/shm/persistent.json de forma segura.
 *
 * Usa lock compartido (flock) para lectura concurrente segura.
 * - Si el valor es string JSON, retorna el contenido sin comillas.
 * - En otros tipos (número/bool/objeto/array), retorna JSON serializado.
 *
 * @param key Clave JSON a consultar.
 * @return char* Memoria dinámica con el valor; liberar con free().
 *               Retorna NULL si no existe o hay error.
 */
char *shm_consult_persistent(const char *key);

/**
 * @brief Alias de compatibilidad para shm_consult_persistent().
 * @param key Clave JSON a consultar.
 * @return char* Igual que shm_consult_persistent(). Liberar con free().
 */
char *ashm_consult_persistent(const char *key);

/** @} */

#endif