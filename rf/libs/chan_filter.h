/**
 * @file chan_filter.h
 * @brief Filtro de canal en el dominio de la frecuencia (FFT).
 *
 * Proporciona herramientas para el filtrado de señales IQ mediante una máscara
 * de frecuencia de dos etapas, incluyendo transiciones de coseno alzado.
 */

#ifndef CHAN_FILTER_H
#define CHAN_FILTER_H

#include "datatypes.h"
#include <stddef.h>
#include <stdint.h>
#include <fftw3.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <math.h>
#include <complex.h>

/**
 * @defgroup chan_filter_module Channel Filter
 * @ingroup rf_binary
 * @brief Filtrado de banda base mediante FFT y máscaras de frecuencia.
 * @{
 */

/**
 * @brief Valida si el rango del filtro está dentro del ancho de banda capturado.
 * * Verifica que \f$ [f_{start}, f_{end}] \f$ se encuentre dentro de \f$ [f_c - \frac{f_s}{2}, f_c + \frac{f_s}{2}] \f$.
 *
 * @param[in]  cfg    Configuración del filtro (frecuencias absolutas).
 * @param[in]  fc_hz  Frecuencia central de sintonía (Hz).
 * @param[in]  fs_hz  Frecuencia de muestreo (Hz).
 * @param[out] err    Buffer para el mensaje de error.
 * @param[in]  err_sz Tamaño del buffer de error.
 * @return int        0 si es válido, negativo en caso de error.
 */
int chan_filter_validate_cfg_abs(
    const filter_t *cfg,
    uint64_t fc_hz,
    double fs_hz,
    char *err,
    size_t err_sz
);

/**
 * @brief Aplica un filtro de dos etapas in-place sobre una señal IQ.
 * * Algoritmo:
 * 1. **Stage 1 (Peak Flattening):** Atenúa picos fuera de banda (OOB) que superen un umbral basado en la mediana.
 * 2. **Stage 2 (Mask):** Aplica una máscara de transferencia con flancos de Coseno Alzado (Raised Cosine).
 *
 * @param[in,out] sig    Estructura con el buffer de señal IQ.
 * @param[in]     cfg    Configuración del filtro deseado.
 * @param[in]     fc_hz  Frecuencia central del hardware (Hz).
 * @param[in]     fs_hz  Frecuencia de muestreo (Hz).
 * @return int           0 si tuvo éxito, negativo en caso de fallo.
 */
int chan_filter_apply_inplace_abs(
    signal_iq_t *sig,
    const filter_t *cfg,
    uint64_t fc_hz,
    double fs_hz
);

/**
 * @brief Obtiene la región espectral del último filtro aplicado.
 * @return const char* "POSITIVE", "NEGATIVE", "CROSS_DC" o "UNKNOWN".
 */
const char* chan_filter_last_region(void);

/**
 * @brief Libera los planes de FFTW y la memoria caché interna.
 * Debe llamarse al finalizar el programa para evitar fugas de memoria.
 */
void chan_filter_free_cache(void);

#endif