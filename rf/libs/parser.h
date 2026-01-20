/**
 * @file parser.h
 * @brief Procesamiento de JSON y Gestión de Configuración para el Motor RF.
 * * Proporciona las herramientas para traducir cadenas JSON de comando en estructuras
 * internas de control para el hardware (SDR) y el procesamiento digital de señales (DSP).
 */

#ifndef PARSER_H
#define PARSER_H

#include "datatypes.h"
#include "sdr_HAL.h"
#include <cjson/cJSON.h>
#include <inttypes.h>
#include <string.h>
#include <stdlib.h>
#include <ctype.h>
#include <stdio.h>

/**
 * @defgroup parser_module Módulo Parser
 * @ingroup rf_binary
 * @brief Funciones para la deserialización y validación de parámetros del sistema.
 * @{
 */

/**
 * @brief Analiza una cadena JSON y puebla una estructura DesiredCfg_t.
 * * Sigue un flujo lógico de tres etapas:
 * 1. **Inicialización**: Aplica valores por defecto hardcoded.
 * 2. **Extracción**: Sobrescribe parámetros con los datos encontrados en el JSON.
 * 3. **Validación (Clamping)**: Ajusta las frecuencias de filtrado para que no excedan
 * el ancho de banda de Nyquist definido por la frecuencia central y el sample rate.
 * * @param[in] json_string Cadena JSON cruda recibida por la interfaz de comunicación.
 * @param[out] target Puntero a la estructura de configuración donde se guardarán los datos.
 * @return 0 si el proceso fue exitoso, -1 si los punteros de entrada son nulos.
 * @note Si el JSON es inválido, la función retorna 0 pero mantiene los valores por defecto.
 */
int parse_config_rf(const char *json_string, DesiredCfg_t *target);

/**
 * @brief Duplica una cadena convirtiendo todos los caracteres a minúsculas.
 * @param[in] str Cadena de origen.
 * @return Nueva cadena en minúsculas asignada en el heap, o NULL si falla el malloc.
 * @warning El usuario es responsable de liberar la memoria mediante free().
 */
char* strdup_lowercase(const char *str);

/**
 * @brief Imprime un resumen detallado del estado del sistema en formato tabla ASCII.
 * @param des Configuración deseada por el usuario.
 * @param hw Estado actual del hardware SDR.
 * @param psd Configuración del motor de densidad espectral (PSD).
 * @param rb Estado del buffer circular (Ring Buffer).
 */
void print_config_summary_DEBUG(DesiredCfg_t *des, SDR_cfg_t *hw, PsdConfig_t *psd, RB_cfg_t *rb);

/**
 * @brief Imprime un registro compacto de una sola línea de la configuración.
 * Ideal para logs de producción (deployment) y monitoreo de terminal en tiempo real.
 */
void print_config_summary_DEPLOY(DesiredCfg_t *des, SDR_cfg_t *hw, PsdConfig_t *psd, RB_cfg_t *rb);

/** @} */

#endif