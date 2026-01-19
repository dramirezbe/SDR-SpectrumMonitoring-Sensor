/**
 * @file sdr_HAL.h
 * @brief Capa de Abstracción de Hardware (HAL) para dispositivos HackRF.
 *
 * Este módulo simplifica la configuración de parámetros comunes de la HackRF,
 * como ganancias, frecuencias y corrección de error de reloj (PPM).
 */

#ifndef SDR_HAL_H
#define SDR_HAL_H

#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include <libhackrf/hackrf.h>

/** * @brief Macro para convertir valores de Megahertz a Hertz.
 * @param x Valor en MHz.
 */
#ifndef IN_MHZ
#define IN_MHZ(x) ((int64_t)(x) * 1000000)
#endif

/**
 * @struct SDR_cfg_t
 * @brief Estructura que contiene los parámetros de configuración del SDR.
 */
typedef struct {
    double sample_rate;    /**< Frecuencia de muestreo en Hz. */
    uint64_t center_freq;  /**< Frecuencia central de sintonización en Hz. */
    bool amp_enabled;      /**< Activar/Desactivar el amplificador de RF frontal (0 o 14 dB). */
    int lna_gain;          /**< Ganancia de FI (LNA) en pasos de 8 dB (0-40 dB). */
    int vga_gain;          /**< Ganancia de banda base (VGA) en pasos de 2 dB (0-62 dB). */
    int ppm_error;         /**< Corrección de error de frecuencia en partes por millón (PPM). */
} SDR_cfg_t;

/**
 * @brief Aplica una configuración completa al dispositivo HackRF.
 * * Esta función centraliza las llamadas a libhackrf para establecer ganancias, 
 * frecuencia y frecuencia de muestreo de una sola vez.
 * * @param dev Puntero al dispositivo HackRF abierto.
 * @param cfg Puntero a la estructura de configuración que se desea aplicar.
 */
void hackrf_apply_cfg(hackrf_device* dev, SDR_cfg_t *cfg);

#endif