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

/** @} */

#endif