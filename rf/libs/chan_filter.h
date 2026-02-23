/**
 * @file chan_filter.h
 * @brief Filtrado de canal por bloque en el dominio de la frecuencia.
 *
 * Implementa un filtro de "muro de ladrillo suave" mediante FFT. El proceso
 * se divide en una etapa de normalización de ruido OOB y una etapa de filtrado
 * mediante máscara con transiciones de coseno alzado para minimizar el rizado
 * en el dominio del tiempo (fenómeno de Gibbs).
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
#include <omp.h>

/**
 * @defgroup chan_filter_module Channel Filter
 * @ingroup rf_binary
 * @brief Procesamiento espectral para aislamiento de señales.
 * @{
 */

/**
 * @brief Valida los límites del filtro respecto al ancho de banda de captura.
 *
 * Asegura que el rango solicitado \f$ [f_{start}, f_{end}] \f$ no exceda los
 * límites de Nyquist definidos por la frecuencia central y de muestreo.
 *
 * @param[in]  cfg    Parámetros de corte (frecuencias absolutas en Hz).
 * @param[in]  fc_hz  Frecuencia central del receptor (Hz).
 * @param[in]  fs_hz  Frecuencia de muestreo (Hz).
 * @param[out] err    Buffer donde se escribirá el motivo del fallo.
 * @param[in]  err_sz Capacidad del buffer de error.
 * @return int        0 si la configuración es físicamente realizable, < 0 en caso contrario.
 */
int chan_filter_validate_cfg_abs(
    const filter_t *cfg,
    uint64_t fc_hz,
    double fs_hz,
    char *err,
    size_t err_sz
);

/**
 * @brief Filtra una señal IQ utilizando una máscara de frecuencia de dos etapas.
 *
 * El procesamiento se realiza in-place siguiendo este flujo:
 * 1. **Transformada Directa:** Se proyecta la señal al dominio de la frecuencia (FFT).
 * 2. **Etapa 1 (Anti-Blooming):** Se calcula la mediana de magnitud fuera de la banda
 * de paso. Los picos que exceden la mediana por un umbral dinámico son recortados.
 * 3. **Etapa 2 (Máscara):** Se aplica la ganancia de la banda de paso (1.0) y la
 * atenuación de banda de parada con transiciones suaves (Raised Cosine).
 * 4. **Transformada Inversa:** Retorno al dominio del tiempo (IFFT) con normalización \f$ 1/N \f$.
 *
 * @param[in,out] sig    Señal IQ de entrada/salida.
 * @param[in]     cfg    Definición de la banda de paso.
 * @param[in]     fc_hz  Frecuencia central (Hz).
 * @param[in]     fs_hz  Frecuencia de muestreo (Hz).
 * @return int           0 en éxito, -1 si los datos son inválidos, -5 si falla la FFTW.
 */
int chan_filter_apply_inplace_abs(
    signal_iq_t *sig,
    const filter_t *cfg,
    uint64_t fc_hz,
    double fs_hz
);

/**
 * @brief Informa la ubicación espectral de la última banda filtrada.
 *
 * Utilizado para depuración o lógica de decodificación que dependa de si la
 * señal es puramente positiva, negativa o si cruza la frecuencia de 0 Hz (DC).
 *
 * @return const char* Cadena estática: "POSITIVE", "NEGATIVE", "CROSS_DC" o "UNKNOWN".
 */
const char* chan_filter_last_region(void);

/**
 * @brief Libera los planes FFTW y buffers de máscara precalculados.
 *
 * Debe invocarse antes de cerrar la aplicación para limpiar la caché global
 * de la biblioteca.
 */
void chan_filter_free_cache(void);

/** @} */

#endif