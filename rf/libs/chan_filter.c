/**
 * @file chan_filter.c
 * @brief Implementación del filtrado por dominio de frecuencia.
 */
#include "chan_filter.h"

/**
 * @addtogroup chan_filter_module
 * @{
 */

#ifndef CLAMPD
/** @brief Limita un valor doble entre un rango mínimo y máximo. */
#define CLAMPD(x,lo,hi) (((x)<(lo))?(lo):(((x)>(hi))?(hi):(x)))
#endif

/**
 * @brief Convierte decibelios a amplitud lineal.
 * \f[ A_{lin} = 10^{\frac{dB}{20}} \f]
 * @param db Decibelios.
 * @return Amplitud lineal.
 */
static inline double db_to_lin_amp_chan_filt(double db) {
    return pow(10.0, db / 20.0);
}

/**
 * @brief Función de Coseno Alzado para transiciones suaves.
 * \f[ f(t) = 0.5 - 0.5 \cdot \cos(\pi \cdot t) \f]
 * @param t Parámetro de entrada (típicamente tiempo normalizado o fase).
 * @return Valor suavizado entre 0.0 y 1.0.
 */
static inline double raised_cos_chan_filt(double t) {
    t = CLAMPD(t, 0.0, 1.0);
    return 0.5 - 0.5 * cos(M_PI * t);
}

// Constantes de diseño del filtro
static const double OOB_REJECT_DB = -15.0; /**< Suelo de rechazo fuera de banda (Etapa 2). */
static const double TRANS_FRAC    = 0.30;  /**< Fracción del ancho de banda usada para la transición. */
static const double CAP_OOB_DB    = 6.0;   /**< Umbral sobre la mediana para recorte de picos (Etapa 1). */
static const double MIN_OOB_FRAC  = 0.05;  /**< Porcentaje mínimo de bins OOB para activar Etapa 1. */

/**
 * @brief Estructura interna para caché de planes FFT y máscara de frecuencia.
 */
typedef struct {
    int N;                  /**< Tamaño de la FFT actual. */
    fftw_complex *in;       /**< Buffer de entrada para FFTW. */
    fftw_complex *out;      /**< Buffer de salida para FFTW. */
    fftw_plan fwd;          /**< Plan de FFT directa. */
    fftw_plan inv;          /**< Plan de FFT inversa. */
    double *mask_stage2;    /**< Valores precalculados de la máscara de magnitud. */

    uint64_t last_fc;       /**< Última frecuencia central procesada. */
    double last_fs;         /**< Última frecuencia de muestreo procesada. */
    int last_start;         /**< Última frecuencia de inicio. */
    int last_end;           /**< Última frecuencia de fin. */
} cache_t;

static cache_t g = {0};
static const char *g_region = "UNKNOWN";

const char* chan_filter_last_region(void) { return g_region; }

/**
 * @brief Libera los recursos de la caché global.
 */
static void cache_free(void) {
    if (g.fwd) fftw_destroy_plan(g.fwd);
    if (g.inv) fftw_destroy_plan(g.inv);
    if (g.in)  fftw_free(g.in);
    if (g.out) fftw_free(g.out);
    if (g.mask_stage2) free(g.mask_stage2);
    memset(&g, 0, sizeof(g));
}

void chan_filter_free_cache(void) { cache_free(); }

/**
 * @brief Determina si la caché actual es inválida para los nuevos parámetros.
 * @param N      Número de muestras actual.
 * @param cfg    Configuración del filtro.
 * @param fc     Frecuencia central actual (Hz).
 * @param fs     Frecuencia de muestreo actual (Hz).
 * @return int   1 si requiere reconstrucción, 0 si la caché es reutilizable.
 */
static int need_rebuild(int N, const filter_t *cfg, uint64_t fc, double fs) {
    if (g.N != N) return 1;
    if (g.last_fc != fc) return 1;
    if (fabs(g.last_fs - fs) > 1e-9) return 1;
    if (g.last_start != cfg->start_freq_hz) return 1;
    if (g.last_end != cfg->end_freq_hz) return 1;
    return 0;
}

/**
 * @brief Función de comparación para qsort.
 * @param a Puntero al primer elemento.
 * @param b Puntero al segundo elemento.
 * @return -1 si a < b, 0 si a == b, 1 si a > b.
 */
static int cmp_double(const void *a, const void *b) {
    double x = *(const double*)a;
    double y = *(const double*)b;
    return (x < y) ? -1 : (x > y);
}

/**
 * @brief Calcula la mediana de un array de doubles.
 * @param v Puntero al array.
 * @param n Tamaño del array.
 * @return Mediana del array.
 */
static double median_of_array(double *v, int n) {
    if (!v || n <= 0) return 0.0;
    qsort(v, (size_t)n, sizeof(double), cmp_double);
    if (n & 1) return v[n/2];
    return 0.5 * (v[n/2 - 1] + v[n/2]);
}

/**
 * @brief Valida la configuración del filtro contra los límites físicos de Nyquist.
 * @param cfg Configuración del filtro.
 * @param fc_hz Frecuencia central.
 * @param fs_hz Frecuencia de muestreo.
 * @param err Mensaje de error.
 * @param err_sz Tamaño del buffer de error.
 */
int chan_filter_validate_cfg_abs(
    const filter_t *cfg,
    uint64_t fc_hz,
    double fs_hz,
    char *err,
    size_t err_sz
) {
    if (!err || err_sz == 0) return -100;
    err[0] = '\0';

    if (!cfg) { snprintf(err, err_sz, "cfg NULL"); return -1; }
    if (fs_hz <= 0.0) { snprintf(err, err_sz, "fs_hz <= 0"); return -2; }
    if (cfg->end_freq_hz <= cfg->start_freq_hz) {
        snprintf(err, err_sz, "end_freq must be > start_freq");
        return -5;
    }

    double fc = (double)fc_hz;
    double nyq = 0.5 * fs_hz;
    double cap_lo = fc - nyq;
    double cap_hi = fc + nyq;

    // Validate that the filter band is within the sampled bandwidth
    if ((double)cfg->start_freq_hz < cap_lo || (double)cfg->end_freq_hz > cap_hi) {
        snprintf(err, err_sz, "band [%d, %d] outside capture range [%.0f, %.0f]", 
                 cfg->start_freq_hz, cfg->end_freq_hz, cap_lo, cap_hi);
        return -6;
    }

    return 0;
}

/**
 * @brief Inicializa planes FFTW y calcula la máscara de magnitud de la Etapa 2.
 * @param N Tamaño de la FFT.
 * @param cfg Configuración del filtro.
 * @param fc_hz Frecuencia central.
 * @param fs_hz Frecuencia de muestreo.
 */
static int build_mask_and_plans(int N, const filter_t *cfg, uint64_t fc_hz, double fs_hz) {
    if (N < 2) return -1;

    if (g.N != N) {
        cache_free();
        g.N = N;
        g.in  = (fftw_complex*)fftw_malloc(sizeof(fftw_complex) * (size_t)N);
        g.out = (fftw_complex*)fftw_malloc(sizeof(fftw_complex) * (size_t)N);
        if (!g.in || !g.out) return -2;

        g.fwd = fftw_plan_dft_1d(N, g.in, g.out, FFTW_FORWARD, FFTW_ESTIMATE);
        g.inv = fftw_plan_dft_1d(N, g.out, g.in, FFTW_BACKWARD, FFTW_ESTIMATE);
        g.mask_stage2 = (double*)malloc(sizeof(double) * (size_t)N);
        if (!g.fwd || !g.inv || !g.mask_stage2) return -3;
    }

    double fc = (double)fc_hz;
    double fi_off = (double)cfg->start_freq_hz - fc;
    double ff_off = (double)cfg->end_freq_hz - fc;

    if (ff_off <= 0.0) g_region = "NEGATIVE";
    else if (fi_off >= 0.0) g_region = "POSITIVE";
    else g_region = "CROSS_DC";

    double B  = ff_off - fi_off;
    double tr = TRANS_FRAC * B;
    
    // Bounds are +/- Nyquist because Span = Fs
    double nyq_lo = -0.5 * fs_hz;
    double nyq_hi = +0.5 * fs_hz;

    double lo1 = fi_off;
    double lo0 = CLAMPD(fi_off - tr, nyq_lo, nyq_hi);
    double hi1 = ff_off;
    double hi0 = CLAMPD(ff_off + tr, nyq_lo, nyq_hi);

    double stop = db_to_lin_amp_chan_filt(OOB_REJECT_DB);
    double df = fs_hz / (double)N;

    for (int k = 0; k < N; k++) {
        int ks = (k <= N/2) ? k : (k - N);
        double f = (double)ks * df;
        double g2;

        if (f <= lo0 || f >= hi0) {
            g2 = stop;
        } else if (f < lo1) {
            g2 = stop + (1.0 - stop) * raised_cos_chan_filt((f - lo0) / (lo1 - lo0));
        } else if (f <= hi1) {
            g2 = 1.0;
        } else {
            g2 = 1.0 + (stop - 1.0) * raised_cos_chan_filt((f - hi1) / (hi0 - hi1));
        }
        g.mask_stage2[k] = g2;
    }

    g.last_fc = fc_hz; g.last_fs = fs_hz;
    g.last_start = cfg->start_freq_hz; g.last_end = cfg->end_freq_hz;

    return 0;
}

int chan_filter_apply_inplace_abs(
    signal_iq_t *sig,
    const filter_t *cfg,
    uint64_t fc_hz,
    double fs_hz
) {
    if (!sig || !sig->signal_iq || sig->n_signal < 2) return -1;
    
    char err[256];
    if (chan_filter_validate_cfg_abs(cfg, fc_hz, fs_hz, err, sizeof(err)) < 0) {
        return -4;
    }

    int N = (int)sig->n_signal;
    if (need_rebuild(N, cfg, fc_hz, fs_hz)) {
        if (build_mask_and_plans(N, cfg, fc_hz, fs_hz) < 0) return -5;
    }

    // Load data into FFTW input
    for (int i = 0; i < N; i++) {
        g.in[i] = sig->signal_iq[i];
    }

    fftw_execute(g.fwd);

    // Stage 1: Out-of-band peak flattening
    double fi_off = (double)cfg->start_freq_hz - (double)fc_hz;
    double ff_off = (double)cfg->end_freq_hz - (double)fc_hz;
    double df = fs_hz / (double)N;

    double *oob_mag = (double*)malloc(sizeof(double) * N);
    int oob_n = 0;

    for (int k = 0; k < N; k++) {
        int ks = (k <= N/2) ? k : (k - N);
        double f = (double)ks * df;
        if (f < fi_off || f > ff_off) {
            oob_mag[oob_n++] = cabs(g.out[k]);
        }
    }

    if (oob_n > 16 && ((double)oob_n / N) >= MIN_OOB_FRAC) {
        double med = median_of_array(oob_mag, oob_n);
        if (med > 0.0) {
            double cap = med * db_to_lin_amp_chan_filt(CAP_OOB_DB);
            for (int k = 0; k < N; k++) {
                int ks = (k <= N/2) ? k : (k - N);
                double f = (double)ks * df;
                if (f < fi_off || f > ff_off) {
                    double mag = cabs(g.out[k]);
                    if (mag > cap) {
                        double s = cap / mag;
                        g.out[k] *= s;
                    }
                }
            }
        }
    }
    free(oob_mag);

    // Stage 2: Apply frequency mask
    for (int k = 0; k < N; k++) {
        g.out[k] *= g.mask_stage2[k];
    }

    fftw_execute(g.inv);

    // Normalize and write back to signal_iq_t
    double invN = 1.0 / (double)N;
    for (int i = 0; i < N; i++) {
        sig->signal_iq[i] = g.in[i] * invN;
    }

    return 0;
}
/** @} */