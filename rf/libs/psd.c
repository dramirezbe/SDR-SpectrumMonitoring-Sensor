/**
 * @file psd.c
 * @brief Implementación de algoritmos de estimación espectral y pre-procesamiento IQ.
 */
#include "psd.h"

/**
 * @addtogroup psd_module
 * @{
 */

signal_iq_t* load_iq_from_buffer(const int8_t* buffer, size_t buffer_size) {
    if (!buffer || buffer_size == 0) return NULL;

    size_t n_samples = buffer_size / 2;
    signal_iq_t* signal_data = (signal_iq_t*)malloc(sizeof(signal_iq_t));
    if (!signal_data) return NULL;
    
    signal_data->n_signal = n_samples;
    // Use calloc to ensure zero-init if something fails partially
    signal_data->signal_iq = (double complex*)calloc(n_samples, sizeof(double complex));
    
    if (!signal_data->signal_iq) {
        free(signal_data);
        return NULL;
    }

    // Convert interleaved 8-bit I/Q to complex double
    // Buffer format: [I0, Q0, I1, Q1, ...]
    for (size_t i = 0; i < n_samples; i++) {
        signal_data->signal_iq[i] = (double)buffer[2 * i] + (double)buffer[2 * i + 1] * I;
    }

    return signal_data;
}

void iq_compensation(signal_iq_t* signal_data) {
    if (!signal_data || !signal_data->signal_iq || signal_data->n_signal == 0)
        return;

    size_t N = signal_data->n_signal;
    double complex* x = signal_data->signal_iq;

    double meanI = 0.0, meanQ = 0.0;
    double pI = 0.0, pQ = 0.0;
    double crossIQ = 0.0;

    // -------------------------------------------------
    // 1) DC offset removal
    // -------------------------------------------------
    #pragma omp parallel for reduction(+:meanI, meanQ)
    for (size_t n = 0; n < N; n++) {
        meanI += creal(x[n]);
        meanQ += cimag(x[n]);
    }
    meanI /= N;
    meanQ /= N;

    #pragma omp parallel for
    for (size_t n = 0; n < N; n++) {
        x[n] = (creal(x[n]) - meanI) + (cimag(x[n]) - meanQ) * I;
    }

    // -------------------------------------------------
    // 2) Gain imbalance
    // -------------------------------------------------
    #pragma omp parallel for reduction(+:pI, pQ, crossIQ)
    for (size_t n = 0; n < N; n++) {
        double I_n = creal(x[n]);
        double Q_n = cimag(x[n]);
        pI += I_n * I_n;
        pQ += Q_n * Q_n;
        crossIQ += I_n * Q_n;
    }

    if (pI <= 0.0 || pQ <= 0.0)
        return;

    double gain = sqrt(pI / pQ);

    #pragma omp parallel for
    for (size_t n = 0; n < N; n++) {
        x[n] = creal(x[n]) + (cimag(x[n]) * gain) * I;
    }

    // -------------------------------------------------
    // 3) Phase correction (decorrelation)
    // -------------------------------------------------
    double rho = crossIQ / pI;

    for (size_t n = 0; n < N; n++) {
        double I_n = creal(x[n]);
        double Q_n = cimag(x[n]);
        x[n] = I_n + (Q_n - rho * I_n) * I;
    }
}

void free_signal_iq(signal_iq_t* signal) {
    if (signal) {
        if (signal->signal_iq) {
            free(signal->signal_iq);
            signal->signal_iq = NULL;
        }
        free(signal);
    }
}

/**
 * @brief Restringe un valor de punto flotante a un rango específico [lo, hi].
 * @param x Valor de entrada a evaluar.
 * @param lo Límite inferior permitido.
 * @param hi Límite superior permitido.
 * @return El valor x si está dentro del rango, de lo contrario devuelve el límite excedido.
 */
static inline double clampd(double x, double lo, double hi) {
    return (x < lo) ? lo : (x > hi) ? hi : x;
}

/**
 * @brief Convierte un valor de ganancia/amplitud de decibelios (dB) a escala lineal.
 * La conversión sigue la fórmula de amplitud:
 * \f[
 * A_{lineal} = 10^{\frac{dB}{20}}
 * \f]
 * @param db Valor en decibelios.
 * @return Amplitud en escala lineal.
 */
static inline double db_to_lin_amp(double db) {
    return pow(10.0, db / 20.0);
}

/**
 * @brief Calcula una función de coseno alzado (Raised Cosine) en el intervalo [0, 1].
 * Esta función genera una transición suave (suavizado) entre 0 y 1, útil para 
 * funciones de ventana o desvanecimientos (fading).
 * * La fórmula aplicada es:
 * \f[
 * f(t) = 0.5 - 0.5 \cdot \cos(\pi \cdot t)
 * \f]
 * donde \f$ t \f$ se restringe internamente al rango \f$ [0, 1] \f$.
 * * @param t Parámetro de entrada (típicamente tiempo normalizado o fase).
 * @return Valor suavizado entre 0.0 y 1.0.
 */
static inline double raised_cos(double t) {
    t = clampd(t, 0.0, 1.0);
    return 0.5 - 0.5 * cos(M_PI * t);
}

int find_params_psd(DesiredCfg_t desired, SDR_cfg_t *hack_cfg, PsdConfig_t *psd_cfg, RB_cfg_t *rb_cfg) {
    double enbw_factor = get_window_enbw_factor(desired.window_type);
    
    double safe_rbw = (desired.rbw > 0) ? (double)desired.rbw : 1000.0;
    
    double required_nperseg_val = enbw_factor * desired.sample_rate / safe_rbw;
    int exponent = (int)ceil(log2(required_nperseg_val));
    
    psd_cfg->nperseg = (int)pow(2, exponent);
    // Clamp to minimum 256
    if (psd_cfg->nperseg < 256) psd_cfg->nperseg = 256; 

    // Calculate overlap
    psd_cfg->noverlap = (int)(psd_cfg->nperseg * desired.overlap);
    if (psd_cfg->noverlap >= psd_cfg->nperseg) {
        psd_cfg->noverlap = psd_cfg->nperseg - 1;
    }

    psd_cfg->window_type = desired.window_type;
    psd_cfg->sample_rate = desired.sample_rate;

    // Map to HW config
    if (hack_cfg) {
        hack_cfg->sample_rate = desired.sample_rate;
        hack_cfg->center_freq = desired.center_freq;
        hack_cfg->amp_enabled = desired.amp_enabled;
        hack_cfg->lna_gain = desired.lna_gain;
        hack_cfg->vga_gain = desired.vga_gain;
        hack_cfg->ppm_error = desired.ppm_error;
    }

    // Default to ~1 second of data if not specified
    rb_cfg->total_bytes = (size_t)(desired.sample_rate * 2);
    return 0;
}

/**
 * @brief Conversión de densidad de potencia lineal a dBm.
 *
 * Convierte valores de potencia normalizados (W/Hz) a escala logarítmica dBm,
 * asumiendo una impedancia de carga de 50 Ω:
 * \f[
 * P_{dBm} = 10 \log_{10}(P_{W} \cdot 1000)
 * \f]
 *
 * @note Esta conversión asume que la señal IQ está correctamente escalada.
 * Los valores obtenidos representan potencia relativa al ADC y no potencia
 * RF absoluta sin una calibración del sistema.
 */
static void convert_to_dbm_inplace(double* psd, int length) {
    #pragma omp parallel for
    for (int i = 0; i < length; i++) {
        // Convert normalized power to Watts (assuming 50 Ohm)
        double p_watts = psd[i] / IMPEDANCE_50_OHM;
        
        // Prevent log(0) or negative values
        if (p_watts < POWER_FLOOR_WATTS) p_watts = POWER_FLOOR_WATTS;
        
        // Convert Watts to dBm
        psd[i] = 10.0 * log10(p_watts * 1000.0);
    }
}

double get_window_enbw_factor(PsdWindowType_t type) {
    switch (type) {
        case RECTANGULAR_TYPE: return 1.000;
        case HAMMING_TYPE:     return 1.363;
        case HANN_TYPE:        return 1.500;
        case BLACKMAN_TYPE:    return 1.730;
        case FLAT_TOP_TYPE:    return 3.770;
        case BARTLETT_TYPE:    return 1.330;
        // Approximations for configurable windows
        case KAISER_TYPE:      return 1.800; // Typical for Beta=6
        case TUKEY_TYPE:       return 1.500; // Typical for Alpha=0.5
        default:               return 1.363;
    }
}

/**
 * @brief Genera los coeficientes de la función de ventana seleccionada.
 * * Las funciones de ventana se utilizan para reducir el "spectral leakage" (filtración espectral) 
 * al truncar la señal en el tiempo antes de aplicar la FFT. Para todas las fórmulas, 
 * se define \f$ M = L - 1 \f$, donde \f$ L \f$ es la longitud de la ventana.
 * * 
 * * Dependiendo del @p window_type, se aplica una de las siguientes ecuaciones para \f$ 0 \le n \le M \f$:
 * * - **Rectangular:** No aplica atenuación.
 * \f[ w[n] = 1 \f]
 * - **Hann:** Excelente para propósitos generales y buena resolución de frecuencia.
 * \f[ w[n] = 0.5 \left( 1 - \cos\left( \frac{2\pi n}{M} \right) \right) \f]
 * - **Hamming:** Optimiza la cancelación del primer lóbulo lateral.
 * \f[ w[n] = 0.54 - 0.46 \cos\left( \frac{2\pi n}{M} \right) \f]
 * - **Blackman:** Mayor atenuación de lóbulos laterales a costa de un lóbulo principal más ancho.
 * \f[ w[n] = 0.42 - 0.5 \cos\left( \frac{2\pi n}{M} \right) + 0.08 \cos\left( \frac{4\pi n}{M} \right) \f]
 * - **Bartlett (Triangular):**
 * \f[ w[n] = 1 - \left| \frac{n - M/2}{M/2} \right| \f]
 * - **Flat Top:** Diseñada para una medición precisa de la amplitud de los picos.
 * \f[ w[n] = a_0 - a_1 \cos\left(\frac{2\pi n}{M}\right) + a_2 \cos\left(\frac{4\pi n}{M}\right) - a_3 \cos\left(\frac{6\pi n}{M}\right) + a_4 \cos\left(\frac{8\pi n}{M}\right) \f]
 * Donde: \f$ a_0=1, a_1=1.93, a_2=1.29, a_3=0.388, a_4=0.032 \f$.
 * * @param window_type   Identificador de la ventana (PsdWindowType_t).
 * @param window_buffer Búfer donde se almacenarán los @p window_length coeficientes calculados.
 * @param window_length Número total de puntos de la ventana (típicamente NPERSEG).
 */
static void generate_window(PsdWindowType_t window_type, double* window_buffer, int window_length) {
    for (int n = 0; n < window_length; n++) {
        double N_minus_1 = (double)(window_length - 1);
        
        switch (window_type) {
            case HANN_TYPE:
                window_buffer[n] = 0.5 * (1.0 - cos((2.0 * M_PI * n) / N_minus_1));
                break;
            case RECTANGULAR_TYPE:
                window_buffer[n] = 1.0;
                break;
            case BLACKMAN_TYPE:
                window_buffer[n] = 0.42 - 0.5 * cos((2.0 * M_PI * n) / N_minus_1) 
                                 + 0.08 * cos((4.0 * M_PI * n) / N_minus_1);
                break;
            case FLAT_TOP_TYPE:
                // a0=1, a1=1.93, a2=1.29, a3=0.388, a4=0.032
                window_buffer[n] = 1.0 
                                 - 1.93 * cos((2.0 * M_PI * n) / N_minus_1)
                                 + 1.29 * cos((4.0 * M_PI * n) / N_minus_1)
                                 - 0.388 * cos((6.0 * M_PI * n) / N_minus_1)
                                 + 0.032 * cos((8.0 * M_PI * n) / N_minus_1);
                break;
            case BARTLETT_TYPE:
                window_buffer[n] = 1.0 - fabs((n - N_minus_1 / 2.0) / (N_minus_1 / 2.0));
                break;
            case HAMMING_TYPE:
            default: // Defaults to Hamming for any unimplemented types
                window_buffer[n] = 0.54 - 0.46 * cos((2.0 * M_PI * n) / N_minus_1);
                break;
        }
    }
}

/**
 * @brief Realiza un desplazamiento circular para centrar la frecuencia cero (DC).
 * * Los algoritmos de FFT devuelven los datos en el orden estándar de salida:
 * [0 a Fs/2] seguido de [-Fs/2 a 0]. Esta función intercambia la primera mitad 
 * del búfer con la segunda para obtener un eje de frecuencias ordenado de:
 * \f[ [-F_s/2, \dots, 0, \dots, F_s/2] \f]
 * * 
 * * La operación consiste en un swap de bloques:
 * - El bloque \f$ [0, \frac{n}{2}-1] \f$ se mueve al final.
 * - El bloque \f$ [\frac{n}{2}, n-1] \f$ se mueve al principio.
 * * @param data Puntero al arreglo de datos (double) que se desea desplazar.
 * @param n    Número de elementos en el arreglo (debe coincidir con el tamaño de la FFT).
 * * @note Utiliza asignación dinámica temporal mediante `malloc` para evitar el 
 * desbordamiento de pila (stack overflow) en FFTs de gran tamaño, a diferencia de `alloca`.
 */
static void fftshift(double* data, int n) {
    int half = n / 2;
    // Use malloc instead of alloca for large FFTs to avoid stack overflow
    double* temp = (double*)malloc(half * sizeof(double));
    if (!temp) return; // Fail silently or handle error

    memcpy(temp, data, half * sizeof(double));
    memcpy(data, &data[half], (n - half) * sizeof(double));
    memcpy(&data[n - half], temp, half * sizeof(double));
    
    free(temp);
}

void execute_welch_psd(signal_iq_t* signal_data, const PsdConfig_t* config, double* f_out, double* p_out) {
    if (!signal_data || !config || !f_out || !p_out) return;

    double complex* signal = signal_data->signal_iq;
    size_t n_signal = signal_data->n_signal;
    int nperseg = config->nperseg;
    int noverlap = config->noverlap;
    double fs = config->sample_rate;
    
    int nfft = nperseg;
    int step = nperseg - noverlap;
    if (step < 1) step = 1;
    
    // Ensure we don't calculate negative segments
    int k_segments = 0;
    if (n_signal >= (size_t)nperseg) {
        k_segments = (int)((n_signal - nperseg) / step) + 1;
    }

    double* window = (double*)malloc(nperseg * sizeof(double));
    if (!window) return; 
    
    generate_window(config->window_type, window, nperseg);

    // Calculate window power (S2) safely
    double u_norm = 0.0;
    #pragma omp parallel for reduction(+:u_norm)
    for (int i = 0; i < nperseg; i++) {
        u_norm += window[i] * window[i];
    }
    u_norm /= nperseg;

    // Reset Output
    memset(p_out, 0, nfft * sizeof(double));

    // Welch Averaging Loop - Parallelized
    #pragma omp parallel
    {
        // Thread-local FFT arrays
        double complex* local_fft_in = fftw_alloc_complex(nfft);
        double complex* local_fft_out = fftw_alloc_complex(nfft);
        fftw_plan local_plan;

        // FFTW planning must be isolated
        #pragma omp critical
        {
            local_plan = fftw_plan_dft_1d(nfft, local_fft_in, local_fft_out, FFTW_FORWARD, FFTW_ESTIMATE);
        }

        // [PATCH C] Use dynamic scheduling to handle load imbalance
        #pragma omp for schedule(dynamic, 1)
        for (int k = 0; k < k_segments; k++) {
            size_t start = k * step;
            
            for (int i = 0; i < nperseg; i++) {
                if ((start + i) < n_signal) {
                    local_fft_in[i] = signal[start + i] * window[i];
                } else {
                    local_fft_in[i] = 0;
                }
            }

            fftw_execute(local_plan);

            // Accumulate Magnitude Squared safely
            for (int i = 0; i < nfft; i++) {
                double mag = cabs(local_fft_out[i]);
                double mag2 = mag * mag;
                
                #pragma omp atomic
                p_out[i] += mag2;
            }
        }

        #pragma omp critical
        {
            fftw_destroy_plan(local_plan);
        }
        fftw_free(local_fft_in);
        fftw_free(local_fft_out);
    }

    // Normalization
    if (k_segments > 0 && u_norm > 0) {
        double scale = 1.0 / (fs * u_norm * k_segments * nperseg);
        
        #pragma omp parallel for
        for (int i = 0; i < nfft; i++) {
            p_out[i] *= scale;
        }
    }

    // Shift zero frequency to center
    fftshift(p_out, nfft);

    convert_to_dbm_inplace(p_out, nfft);

    // Generate Frequency Axis
    double df = fs / nfft;
    
    #pragma omp parallel for
    for (int i = 0; i < nfft; i++) {
        f_out[i] = -fs / 2.0 + i * df;
    }

    // Cleanup
    free(window);
}

/**
 * @brief Función de Bessel de primera especie de orden cero modificada \f$ I_0(x) \f$.
 * * Esta función calcula una aproximación numérica de la función de Bessel mediante 
 * su expansión en serie de potencias:
 * \f[
 * I_0(x) = \sum_{k=0}^{\infty} \frac{(\frac{1}{4}x^2)^k}{(k!)^2}
 * \f]
 * * 
 * * Se utiliza específicamente para el diseño de la **Ventana de Kaiser**, la cual 
 * es óptima para maximizar la energía en el lóbulo principal.
 * * @param x Valor de entrada (argumento de la función).
 * @return La aproximación de \f$ I_0(x) \f$. La iteración se detiene cuando el 
 * término incremental es menor a \f$ 10^{-12} \f$ para garantizar precisión de doble flotante.
 */
static double bessi0(double x) {
    double sum = 1.0, y = x * x / 4.0;
    double t = y;
    int k = 1;

    while (t > 1e-12) {
        sum += t;
        k++;
        t *= y / (k * k);
    }
    return sum;
}

/**
 * @brief Genera los coeficientes de una ventana Kaiser para el filtro prototipo del PFB.
 * * Esta función implementa la ventana de Kaiser, la cual es una aproximación a la 
 * función de onda esferoidal alargada que maximiza la concentración de energía en 
 * el lóbulo principal. Se utiliza como filtro prototipo en la arquitectura PFB.
 * * La ventana se define mediante la fórmula:
 * \f[
 * w[n] = \frac{I_0 \left( \beta \sqrt{1 - \left( \frac{2n}{L-1} - 1 \right)^2} \right)}{I_0(\beta)}
 * \f]
 * donde \f$ L \f$ es la longitud total del filtro y \f$ I_0 \f$ es la función de 
 * Bessel modificada de primera especie y orden cero.
 * * 
 * * **Impacto del parámetro Beta (\f$ \beta \f$):**
 * - \f$ \beta = 0 \f$: Equivale a una ventana Rectangular.
 * - \f$ \beta = 5.0 \f$: Similar a una ventana Hamming.
 * - \f$ \beta = 8.6 \f$: Valor por defecto en este módulo, proporciona ~80 dB de rechazo.
 * * @param h    Búfer de salida donde se almacenarán los coeficientes (tamaño @p len).
 * @param len  Longitud total del filtro (calculada como \f$ M \cdot T \f$).
 * @param beta Parámetro de forma que controla la relación entre el ancho del lóbulo y la atenuación.
 */
static void generate_kaiser_proto(double* h, int len, double beta) {
    double denom = bessi0(beta);
    for (int n = 0; n < len; n++) {
        double x = 2.0 * n / (len - 1) - 1.0;
        h[n] = bessi0(beta * sqrt(1 - x * x)) / denom;
    }
}


void execute_pfb_psd(
    signal_iq_t* signal_data,
    const PsdConfig_t* config,
    double* f_out,
    double* p_out
) {
    if (!signal_data || !config || !f_out || !p_out) return;

    const int M = config->nperseg;              // Number of channels
    const int T = PFB_TAPS_PER_CHANNEL;
    const int L = M * T;                        // FIR length
    const double fs = config->sample_rate;

    size_t N = signal_data->n_signal;
    double complex* x = signal_data->signal_iq;

    memset(p_out, 0, M * sizeof(double));

    // -------------------------------------------------
    // Prototype filter
    // -------------------------------------------------
    double* h = (double*)malloc(L * sizeof(double));
    if (!h) return; 
    generate_kaiser_proto(h, L, KAISER_BETA);

    // Polyphase components
    double* poly[T];
    for (int t = 0; t < T; t++) {
        poly[t] = (double*)malloc(M * sizeof(double));
        for (int m = 0; m < M; m++) {
            poly[t][m] = h[t * M + m];
        }
    }

    // -------------------------------------------------
    // PFB Processing
    // -------------------------------------------------
    int blocks = (N - L) / M;
    if (blocks <= 0) goto cleanup;

    #pragma omp parallel
    {
        // Thread-local FFT buffers to prevent data corruption
        double complex* local_fft_in  = fftw_alloc_complex(M);
        double complex* local_fft_out = fftw_alloc_complex(M);
        fftw_plan local_plan;

        // FFTW planning is not thread-safe by default, must be isolated
        #pragma omp critical
        {
            local_plan = fftw_plan_dft_1d(M, local_fft_in, local_fft_out, FFTW_FORWARD, FFTW_ESTIMATE);
        }

        // [PATCH C] Use dynamic scheduling to handle load imbalance
        #pragma omp for schedule(dynamic, 1)
        for (int b = 0; b < blocks; b++) {
            memset(local_fft_in, 0, M * sizeof(double complex));

            for (int t = 0; t < T; t++) {
                size_t offset = b * M + t * M;
                for (int m = 0; m < M; m++) {
                    local_fft_in[m] += x[offset + m] * poly[t][m];
                }
            }

            fftw_execute(local_plan);

            for (int k = 0; k < M; k++) {
                double mag2 = creal(local_fft_out[k]) * creal(local_fft_out[k]) +
                              cimag(local_fft_out[k]) * cimag(local_fft_out[k]);
                
                // Safely accumulate the power bins across threads
                #pragma omp atomic
                p_out[k] += mag2;
            }
        }

        #pragma omp critical
        {
            fftw_destroy_plan(local_plan);
        }
        fftw_free(local_fft_in);
        fftw_free(local_fft_out);
    }

    // -------------------------------------------------
    // Normalization
    // -------------------------------------------------
    double scale = 1.0 / (blocks * fs * M);
    
    #pragma omp parallel for
    for (int i = 0; i < M; i++) {
        p_out[i] *= scale;
    }

    // --- FFT Shift ---
    fftshift(p_out, M);

    convert_to_dbm_inplace(p_out, M);

    // -------------------------------------------------
    // Frequency axis
    // -------------------------------------------------
    double df = fs / M;
    
    #pragma omp parallel for
    for (int i = 0; i < M; i++) {
        f_out[i] = -fs / 2.0 + i * df;
    }

cleanup:
    for (int t = 0; t < T; t++) free(poly[t]);
    free(h);
}

/** @} */