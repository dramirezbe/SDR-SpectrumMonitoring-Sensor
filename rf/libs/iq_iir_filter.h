/**
 * @file iq_iir_filter.h
 * @brief Filtro IIR Butterworth de precisión para señales en cuadratura (IQ).
 *
 * Este módulo implementa un filtro pasa-bajo digital mediante una cascada de secciones 
 * de segundo orden (SOS) o Biquads. La respuesta en frecuencia sigue una aproximación 
 * de Butterworth, caracterizada por una magnitud máximamente plana en la banda de paso:
 * * \f[ |H(j\omega)| = \frac{1}{\sqrt{1 + (\frac{\omega}{\omega_c})^{2N}}} \f]
 *
 * donde \f$ N \f$ es el orden del filtro y \f$ \omega_c \f$ es la frecuencia de corte.
 */

#ifndef IQ_IIR_FILTER_H
#define IQ_IIR_FILTER_H

#include "datatypes.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @defgroup iq_iir_filter_module IQ IIR Filter
 * @ingroup rf_binary
 * @brief Filtro IIR Butterworth de precisión para señales en cuadratura (IQ).
 */

/**
 * @brief Estado interno del filtro IIR.
 * * Almacena los coeficientes y registros de estado para procesar señales IQ. 
 * El filtrado se aplica de forma simétrica tanto a la componente real (I) 
 * como a la imaginaria (Q).
 */
typedef struct {
    int initialized;  /**< Estado de inicialización (1: OK). */

    double fs_hz;     /**< Frecuencia de muestreo \f$ f_s \f$ en Hz. */
    float  bw_hz;     /**< Ancho de banda total \f$ B_w \f$ (+/- BW/2). */
    int    order;     /**< Orden del filtro \f$ N \f$. */

    int sections;     /**< Número de secciones biquad \f$ N_s = N/2 \f$. */

    /** @name Coeficientes del Filtro (RBJ) */
    /**@{*/
    float *b0;        /**< Numerador tap 0. */
    float *b1;        /**< Numerador tap 1. */
    float *b2;        /**< Numerador tap 2. */
    float *a1;        /**< Denominador tap 1 (normalizado). */
    float *a2;        /**< Denominador tap 2 (normalizado). */
    /**@}*/

    /** @name Registros de Estado (Forma Directa II Transpuesta) */
    /**@{*/
    float *z1_i;      /**< Delay tap 1 para canal I. */
    float *z2_i;      /**< Delay tap 2 para canal I. */
    float *z1_q;      /**< Delay tap 1 para canal Q. */
    float *z2_q;      /**< Delay tap 2 para canal Q. */
    /**@}*/

    /** @name Bloqueador de DC */
    /**@{*/
    int   enable_dc;  /**< Control del filtro Notch en DC. */
    float dc_r;       /**< Radio del polo \f$ r \f$ (proximidad al círculo unitario). */
    float dc_x1_i, dc_y1_i; /**< Estados DC para canal I. */
    float dc_x1_q, dc_y1_q; /**< Estados DC para canal Q. */
    /**@}*/
} iq_iir_filter_t;

/**
 * @brief Inicializa la estructura del filtro y reserva memoria.
 *
 * @param[out] st              Puntero a la estructura de estado del filtro.
 * @param[in]  fs_hz           Frecuencia de muestreo del sistema.
 * @param[in]  cfg             Configuración de audio (contiene orden y ancho de banda).
 * @param[in]  enable_dc_block Indica si se debe activar el filtro eliminador de DC.
 * @return int 0 en caso de éxito, -1 si falla la reserva de memoria.
 */
int  iq_iir_filter_init(iq_iir_filter_t *st, double fs_hz, const filter_audio_t *cfg, int enable_dc_block);

/**
 * @brief Reconfigura dinámicamente los parámetros del filtro.
 * * Calcula nuevos coeficientes si cambian la frecuencia de muestreo, el orden o 
 * el ancho de banda. Si el orden cambia, se reasigna memoria automáticamente.
 *
 * @param[in,out] st    Puntero al estado del filtro.
 * @param[in]     fs_hz Nueva frecuencia de muestreo.
 * @param[in]     cfg   Nueva configuración de filtro.
 * @return int 0 en caso de éxito, -1 si los parámetros son inválidos.
 */
int  iq_iir_filter_config(iq_iir_filter_t *st, double fs_hz, const filter_audio_t *cfg);

/**
 * @brief Reinicia los registros de estado (historia) del filtro.
 * * Pone a cero las memorias internas del filtro para evitar transitorios, 
 * sin modificar los coeficientes ni la configuración.
 *
 * @param[in,out] st Puntero al estado del filtro.
 */
void iq_iir_filter_reset(iq_iir_filter_t *st);

/**
 * @brief Libera toda la memoria dinámica asociada al filtro.
 * * @param[in,out] st Puntero al estado del filtro. Se limpia la estructura tras liberar.
 */
void iq_iir_filter_free(iq_iir_filter_t *st);

/**
 * @brief Procesa un bloque de muestras IQ "in-place".
 * * El procesamiento sigue el flujo:
 * 1. Eliminación de DC (si aplica).
 * 2. Cascada de \f$ k \f$ secciones Biquad:
 * \f[
 * y_k[n] = b_{0,k}x_k[n] + z_{1,k}[n-1]
 * \f]
 *
 * @param[in,out] st  Contexto del filtro.
 * @param[in,out] sig Estructura con el buffer de muestras complejas.
 */
void iq_iir_filter_apply_inplace(iq_iir_filter_t *st, signal_iq_t *sig);

/** @} */

#ifdef __cplusplus
}
#endif

#endif // IQ_IIR_FILTER_H