/**
 * @file psd.h
 * @brief Funciones para el cálculo de Densidad Espectral de Potencia (PSD).
 */

#ifndef PSD_H
#define PSD_H

#include "datatypes.h"
#include "parser.h"
#include "sdr_HAL.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <fftw3.h>
#include <alloca.h>
#include <complex.h>
#include <ctype.h>
#include <stdio.h>
#include <limits.h>

/**
 * @defgroup psd_module PSD (Densidad Espectral de Potencia)
 * @ingroup rf_binary
 * @brief Algoritmos avanzados de estimación espectral (Welch y PFB).
 * @{
 */

/**
 * @brief Número de taps (coeficientes) por canal en el Banco de Filtros Polifásicos.
 * Un valor de 8 ofrece un compromiso óptimo entre la selectividad del filtro 
 * y la carga computacional (latencia).
 */
#define PFB_TAPS_PER_CHANNEL 8

/**
 * @brief Parámetro Beta para la generación de la ventana Kaiser.
 * Un valor de 8.6 resulta en una atenuación de lóbulos laterales de aproximadamente 80 dB,
 * minimizando drásticamente el leakage espectral en señales de gran rango dinámico.
 */
#define KAISER_BETA 8.6

/**
 * @brief Impedancia de referencia del sistema (Ohmios).
 * Utilizada para la conversión de la potencia digital a unidades físicas (Watts),
 * asumiendo que el front-end de radio está acoplado a 50 \f$\Omega\f$.
 */
#define IMPEDANCE_50_OHM 50.0

/**
 * @brief Suelo de potencia mínimo (Watts) para evitar inestabilidad numérica.
 * Se utiliza como "clamp" antes del cálculo logarítmico para prevenir \f$\log(0)\f$ 
 * o valores de dBm excesivamente negativos en ausencia de señal.
 */
#define POWER_FLOOR_WATTS 1.0e-20

/**
 * @brief Carga y convierte un búfer de bytes interleaved en señal compleja IQ.
 * @param buffer Puntero a datos [I0, Q0, I1, Q1, ...].
 * @param buffer_size Tamaño total en bytes.
 * @return Puntero a estructura signal_iq_t con datos en double complex.
 */
signal_iq_t* load_iq_from_buffer(const int8_t* buffer, size_t buffer_size);

/**
 * @brief Compensación de desequilibrios IQ (IQ Imbalance Compensation).
 *
 * Esta función corrige defectos comunes introducidos por el front-end analógico
 * y el proceso de digitalización de señales complejas IQ. La corrección se realiza
 * in-situ y consta de tres etapas secuenciales:
 *
 * @par 1. Eliminación de DC Offset
 * Se calcula y elimina la componente continua (DC) de los canales I y Q,
 * reduciendo el pico central en el espectro:
 * \f[
 * I'_{n} = I_{n} - \frac{1}{N}\sum_{k=0}^{N-1} I_k,\quad
 * Q'_{n} = Q_{n} - \frac{1}{N}\sum_{k=0}^{N-1} Q_k
 * \f]
 *
 * @par 2. Corrección de Desequilibrio de Ganancia
 * Se ajusta la ganancia del canal Q para igualar su potencia media con la del canal I:
 * \f[
 * G = \sqrt{\frac{\sum I_n^2}{\sum Q_n^2}},\quad
 * Q''_{n} = G \cdot Q'_{n}
 * \f]
 *
 * @par 3. Corrección de Fase (Decorrelación Lineal)
 * Se elimina la proyección lineal del canal I sobre Q, corrigiendo la
 * falta de ortogonalidad entre ambos canales.
 * \f[
 * \rho = \frac{\sum I_n Q_n}{\sum I_n^2} \implies Q^{final}_{n} = Q''_{n} - \rho \cdot I'_{n}
 * \f]
 *
 * @param[in,out] signal_data Puntero a la estructura que contiene el búfer de muestras 
 * complejas y el número de muestras. Los datos se modifican 
 * directamente en memoria (in-place).
 *
 * @note Esta compensación mejora significativamente el rechazo de la imagen espectral,
 * pero no sustituye una calibración analógica completa del receptor.
 */
void iq_compensation(signal_iq_t* signal_data);

/**
 * @brief Libera la memoria utilizada por una estructura signal_iq_t.
 * @param signal Estructura a liberar.
 */
void free_signal_iq(signal_iq_t* signal);

/**
 * @brief Calcula el factor ENBW (Equivalent Noise Bandwidth) de una ventana.
 * @param type Tipo de ventana.
 * @return Factor multiplicativo (ej. 1.5 para Hann).
 */
double get_window_enbw_factor(PsdWindowType_t type); 

/**
 * @brief Determina parámetros óptimos de PSD a partir de un RBW deseado.
 *
 * Calcula el tamaño de FFT necesario para alcanzar una resolución espectral
 * aproximada (RBW) considerando el ancho de banda equivalente al ruido (ENBW)
 * de la ventana seleccionada.
 *
 * El tamaño de segmento se fuerza a la siguiente potencia de dos por eficiencia
 * computacional en FFT:
 * \f[
 * N_{perseg} = 2^{\lceil \log_2(\text{ENBW} \cdot F_s / \text{RBW}) \rceil}
 * \f]
 *
 * @param[in]  desired  Configuración deseada por el usuario.
 * @param[out] hack_cfg Configuración resultante para el hardware SDR.
 * @param[out] psd_cfg  Configuración del algoritmo PSD.
 * @param[out] rb_cfg   Configuración del búfer circular de adquisición.
 *
 * @return 0 en caso de éxito.
 *
 * @note El RBW resultante es aproximado y depende del tipo de ventana seleccionada.
 */
int find_params_psd(DesiredCfg_t desired, SDR_cfg_t *hack_cfg, PsdConfig_t *psd_cfg, RB_cfg_t *rb_cfg);

/**
 * @brief Estimación de PSD mediante Banco de Filtros Polifásicos (PFB).
 *
 * Este método utiliza un filtro FIR prototipo de longitud \f$ L = M \cdot T \f$
 * (ventana Kaiser) descompuesto en \f$ T \f$ ramas polifásicas de longitud \f$ M \f$.
 *
 * Para cada bloque \f$ b \f$, la entrada a la FFT se calcula como:
 * \f[
 * X_{fft}[m] =
 * \sum_{t=0}^{T-1}
 * x[bM + tM + m] \cdot h[tM + m]
 * \f]
 *
 * donde:
 * - \f$ M \f$: número de canales (bins FFT).
 * - \f$ T \f$: taps por canal.
 * - \f$ h[tM + m] \f$: coeficientes del filtro prototipo reorganizados
 *   en componentes polifásicas.
 *
 * ### Propiedades
 * - Excelente rechazo de lóbulos laterales (≈ 80 dB con β = 8.6).
 * - Mejor aislamiento entre bins que Welch.
 * - Mayor costo computacional.
 *
 * @param[in]  signal_data Señal IQ compleja de entrada.
 * @param[in]  config      Configuración PSD (M se interpreta como número de canales).
 * @param[out] f_out       Eje de frecuencias centrado en DC (Hz).
 * @param[out] p_out       PSD estimada en dBm.
 *
 * @note La potencia resultante es una densidad espectral relativa a la escala
 * digital del sistema. No representa potencia RF absoluta sin calibración.
 */
void execute_pfb_psd(signal_iq_t* signal_data, const PsdConfig_t* config, double* f_out, double* p_out);

/**
 * @brief Estimación de la Densidad Espectral de Potencia mediante el método de Welch.
 *
 * El método de Welch divide la señal en segmentos solapados, aplica una ventana
 * temporal a cada uno, calcula su periodograma y promedia los resultados para
 * reducir la varianza del estimador.
 *
 * ### Definición Matemática
 * El estimador se define como:
 * \f[
 * \hat{P}(f) = \frac{1}{K}\sum_{k=0}^{K-1} P_k(f)
 * \f]
 *
 * donde el periodograma de cada segmento es:
 * \f[
 * P_k(f) =
 * \frac{1}{F_s \cdot L \cdot U}
 * \left|
 * \sum_{n=0}^{L-1} x_k[n]\, w[n]\, e^{-j2\pi fn/F_s}
 * \right|^2
 * \f]
 *
 * con:
 * \f[
 * U = \frac{1}{L}\sum_{n=0}^{L-1} |w[n]|^2
 * \f]
 *
 * ### Consideraciones de Implementación
 * - La PSD calculada es de **dos lados (two-sided)**.
 * - El resultado se normaliza a **W/Hz** antes de la conversión a dBm.
 * - La frecuencia cero se centra mediante un desplazamiento FFT (fftshift).
 *
 * @param[in]  signal_data Señal IQ compleja de entrada.
 * @param[in]  config      Parámetros de segmentación, solape y ventana.
 * @param[out] f_out       Eje de frecuencias en Hz.
 * @param[out] p_out       PSD estimada en dBm.
 *
 * @note Los valores en dBm son relativos a la escala digital del ADC.
 * Para obtener potencia RF absoluta es necesaria una calibración externa
 * del sistema de adquisición.
 */
void execute_welch_psd(signal_iq_t* signal_data, const PsdConfig_t* config, double* f_out, double* p_out);

/** @} */ // Fin de psd_module

#endif