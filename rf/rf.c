/**
 * @file rf.c
 * @brief Implementación del módulo principal de Radio para el procesamiento y transmisión de señales SDR.
 *
 * @details Este módulo actúa como el controlador primario del sistema de Radio. 
 * Coordina el ciclo de vida del hardware HackRF a través de la capa HAL, gestiona 
 * la ingesta de datos IQ de alta velocidad en memorias intermedias circulares (ring buffers) 
 * y maneja el flujo de procesamiento de señales digitales (demodulación AM/FM y cálculo de PSD).
 * * El módulo también facilita la transmisión de audio en tiempo real a través de la red 
 * utilizando codificación Opus y ZMQ para el comando y control.
 *
 * @author GCPDS
 * @date 2026
 */

#ifndef _GNU_SOURCE
/** @brief Habilita las extensiones GNU para afinidad de CPU y funciones avanzadas de sockets. */
#define _GNU_SOURCE
#endif

/* Librerías Estándar e Incluidos del Sistema ... */
#include <stdio.h>
#include <stdbool.h>
#include <stdlib.h>
#include <unistd.h>
#include <math.h>
#include <string.h>
#include <inttypes.h>
#include <pthread.h>
#include <time.h>
#include <signal.h>
#include <complex.h>
#include <sys/time.h>
#include <errno.h>
#include <stdatomic.h>
#include <sys/socket.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <sys/types.h>
#include <libhackrf/hackrf.h>
#include <cjson/cJSON.h>

#include "psd.h"
#include "datatypes.h" 
#include "sdr_HAL.h"     
#include "ring_buffer.h" 
#include "zmq_util.h" 
#include "utils.h"
#include "parser.h"
#include "chan_filter.h"
#include "audio_stream_ctx.h"
#include "am_radio_local.h"
#include "net_audio_retry.h"
#include "iq_iir_filter.h"
#include "opus_tx.h"

#ifndef NO_COMMON_LIBS
    #include "bacn_gpio.h"
#endif

/** * @defgroup rf_binary Binario RF
 * @brief Lógica, Procesamiento de Señales Digitales (DSP) y transmisión de Audio para el módulo de Radio.
 * @{ 
 */

/** * @name Configuración DSP
 * Constantes y selectores para el flujo de procesamiento de señales.
 * @{ 
 */
static int    IQ_FILTER_ENABLE        = 1;      /**< Interruptor para habilitar/deshabilitar el filtrado IIR en datos IQ crudos. */
static float  IQ_FILTER_BW_AM_HZ      = 20000.0f; /**< Ancho de banda en Hz para el pre-filtro de demodulación AM. */

/** @} */

/**
 * @name Comunicación y Manejadores de Hardware
 * Interfaces para mensajería de red y hardware físico SDR.
 * @{
 */
zpair_t *zmq_channel = NULL;          /**< Par de sockets ZMQ para comando y control de red/IPC. */
hackrf_device* device = NULL;         /**< Puntero a la instancia inicializada del hardware HackRF. */
/** @} */

/**
 * @name Sistema de Buffers
 * Buffers circulares utilizados para desacoplar el muestreo de hardware de alta velocidad de los hilos de procesamiento.
 * @{
 */
ring_buffer_t rb;                     /**< Buffer circular primario para muestras IQ crudas del HackRF. */
ring_buffer_t audio_rb;               /**< Buffer circular para muestras de audio demoduladas, listas para transmitir. */
/** @} */

/**
 * @name Estado Global e Hilos (Threading)
 * Banderas y primitivas que gestionan el flujo de ejecución y la seguridad entre hilos.
 * @{
 */
atomic_bool   audio_enabled = false;  /**< Bandera atómica que indica si el subsistema de audio está activo. */
atomic_bool   calibration_running = false; /**< Indica calibración en curso para evitar cierre concurrente de HW. */
volatile bool stop_streaming = true;  /**< Señal para detener la adquisición de datos del hardware. */
volatile bool config_received = false;/**< Se activa cuando se procesa un nuevo paquete de configuración. */
volatile bool keep_running = true;    /**< Bandera maestra de salida para el bucle principal de la aplicación. */

pthread_t       audio_thread;         /**< Identificador del hilo para la tarea de red/audio Opus. */
volatile bool   audio_thread_running = false; /**< Bandera de estado para el ciclo de vida del hilo de audio. */
pthread_mutex_t cfg_mutex = PTHREAD_MUTEX_INITIALIZER; /**< Protege el acceso a @ref desired_config. */

pthread_cond_t  rb_cond  = PTHREAD_COND_INITIALIZER;
pthread_mutex_t rb_mutex = PTHREAD_MUTEX_INITIALIZER;

/** @} */

/**
 * @name Estructuras de Configuración
 * Instantáneas de los ajustes deseados, de hardware y de procesamiento.
 * @{
 */
DesiredCfg_t desired_config = {0};    /**< Configuración objetivo solicitada por el usuario o la red. */
PsdConfig_t  psd_cfg = {0};           /**< Parámetros para FFT y Densidad Espectral de Potencia (PSD). */
SDR_cfg_t    hack_cfg = {0};          /**< Ajustes de bajo nivel del hardware HackRF (Ganancia, IF, etc.). */
RB_cfg_t     rb_cfg = {0};            /**< Parámetros de configuración para el tamaño del buffer circular. */
SDR_cfg_t    current_hw_cfg = {0};    /**< Estado actual real del hardware para comparaciones de sintonización optimizada. */
/** @} */

static double g_request_cooldown_s = 1.0;

int rx_callback(hackrf_transfer* transfer);

typedef struct {
    int8_t *linear_buffer;
    size_t linear_capacity_bytes;
    signal_iq_t sig;
    size_t sig_capacity_samples;
    double *freq;
    double *psd;
    int spectrum_capacity;
} rf_processing_workspace_t;

static void rf_workspace_release(rf_processing_workspace_t *ws) {
    if (!ws) return;
    free(ws->linear_buffer);
    free(ws->sig.signal_iq);
    free(ws->freq);
    free(ws->psd);
    memset(ws, 0, sizeof(*ws));
}

static int rf_workspace_ensure(rf_processing_workspace_t *ws, size_t iq_bytes, int nperseg) {
    if (!ws || iq_bytes == 0 || nperseg <= 0) return -1;

    const size_t iq_samples = iq_bytes / 2U;
    if (ws->linear_capacity_bytes < iq_bytes) {
        int8_t *new_linear = (int8_t*)realloc(ws->linear_buffer, iq_bytes);
        if (!new_linear) return -1;
        ws->linear_buffer = new_linear;
        ws->linear_capacity_bytes = iq_bytes;
    }

    if (ws->sig_capacity_samples < iq_samples) {
        double complex *new_sig = (double complex*)realloc(ws->sig.signal_iq, iq_samples * sizeof(double complex));
        if (!new_sig) return -1;
        ws->sig.signal_iq = new_sig;
        ws->sig_capacity_samples = iq_samples;
    }
    ws->sig.n_signal = iq_samples;

    if (ws->spectrum_capacity < nperseg) {
        double *new_freq = (double*)realloc(ws->freq, (size_t)nperseg * sizeof(double));
        if (!new_freq) return -1;
        ws->freq = new_freq;

        double *new_psd = (double*)realloc(ws->psd, (size_t)nperseg * sizeof(double));
        if (!new_psd) return -1;
        ws->psd = new_psd;

        ws->spectrum_capacity = nperseg;
    }

    return 0;
}

static inline float calibration_finish(float final_ppm) {
    printf("final_ppm = %.3f\n", final_ppm);
    printf("calibration done\n");
    
    // Guardar ppm_error en ShmStore solo si la calibración fue exitosa
    if (final_ppm != 0.0f) {
        char ppm_str[64];
        snprintf(ppm_str, sizeof(ppm_str), "%.6f", (double)final_ppm);
        if (shm_add_to_persistent("ppm_error", ppm_str) == 0) {
            printf("[SHM] ppm_error guardado en ShmStore: %.6f\n", final_ppm);
        } else {
            printf("[SHM] Error guardando ppm_error en ShmStore\n");
        }
    } else {
        printf("[SHM] Calibración falló (ppm=0.0), no se guarda en ShmStore\n");
    }
    
    atomic_store(&calibration_running, false);
    return final_ppm;
}

static int cmp_double_asc(const void *a, const void *b) {
    double x = *(const double*)a;
    double y = *(const double*)b;
    return (x < y) ? -1 : (x > y);
}

static double median_of_double_copy(const double *v, int n) {
    if (!v || n <= 0) return 0.0;
    double *tmp = (double*)malloc((size_t)n * sizeof(double));
    if (!tmp) return 0.0;
    memcpy(tmp, v, (size_t)n * sizeof(double));
    qsort(tmp, (size_t)n, sizeof(double), cmp_double_asc);
    double med = (n & 1) ? tmp[n / 2] : 0.5 * (tmp[n / 2 - 1] + tmp[n / 2]);
    free(tmp);
    return med;
}

static double interp_linear(const double *x, const double *y, int n, double xq) {
    if (!x || !y || n <= 0) return 0.0;
    if (xq <= x[0]) return y[0];
    if (xq >= x[n - 1]) return y[n - 1];

    int lo = 0;
    int hi = n - 1;
    while (hi - lo > 1) {
        int mid = lo + (hi - lo) / 2;
        if (x[mid] <= xq) lo = mid;
        else hi = mid;
    }

    double x0 = x[lo], x1 = x[hi];
    double y0 = y[lo], y1 = y[hi];
    double t = (xq - x0) / (x1 - x0 + 1e-20);
    return y0 + t * (y1 - y0);
}

static float g_last_cal_ppm = 0.0f;
static int g_has_last_cal_ppm = 0;


static float calibrate_hackrf(void) {
    float final_ppm = 0.0f;
    printf("calibrating\n");
    printf("[CALDBG] enter calibrate_hackrf\n");

    if (atomic_exchange(&calibration_running, true)) {
        printf("[CALDBG] calibration already running, skipping\n");
        return calibration_finish(final_ppm);
    }

    if (!stop_streaming) {
        printf("[CALDBG] stop_streaming=false, RX busy, skipping calibration\n");
        return calibration_finish(final_ppm);
    }

    if (device == NULL) {
        printf("[CALDBG] device is NULL, opening HackRF\n");
        if (hackrf_open(&device) != HACKRF_SUCCESS) {
            printf("[CALDBG] hackrf_open failed\n");
            return calibration_finish(final_ppm);
        }

        SDR_cfg_t cfg_to_apply = current_hw_cfg;
        if (cfg_to_apply.center_freq == 0 || cfg_to_apply.sample_rate <= 0.0) {
            cfg_to_apply.center_freq = 98000000ULL;
            cfg_to_apply.sample_rate = 20000000.0;
            cfg_to_apply.lna_gain = 32;
            cfg_to_apply.vga_gain = 32;
            cfg_to_apply.amp_enabled = true;
            cfg_to_apply.ppm_error = 0.0f;
            printf("[CALDBG] applying default cfg for calibration\n");
        }

        hackrf_apply_cfg(device, &cfg_to_apply);
        memcpy(&current_hw_cfg, &cfg_to_apply, sizeof(SDR_cfg_t));
    }

    const double fs = (current_hw_cfg.sample_rate > 0.0) ? current_hw_cfg.sample_rate : 20000000.0;
    const uint64_t fc = (current_hw_cfg.center_freq > 0) ? current_hw_cfg.center_freq : 98000000ULL;
    printf("[CALDBG] using fs=%.3f Hz fc=%" PRIu64 " Hz ppm=%.6f\n", fs, fc, current_hw_cfg.ppm_error);

    size_t iq_samples = (size_t)(fs * 0.5); // ~0.5 s (balance entre robustez y memoria)
    if (iq_samples < 262144U) iq_samples = 262144U;
    if (iq_samples > 10000000U) iq_samples = 10000000U;
    const size_t iq_bytes = iq_samples * 2U;
    printf("[CALDBG] capture plan: iq_samples=%zu iq_bytes=%zu\n", iq_samples, iq_bytes);

    rb_reset(&rb);
    stop_streaming = false;
    printf("[CALDBG] starting RX for calibration\n");
    if (hackrf_start_rx(device, rx_callback, NULL) != HACKRF_SUCCESS) {
        stop_streaming = true;
        printf("[CALDBG] hackrf_start_rx failed\n");
        return calibration_finish(final_ppm);
    }

    struct timespec ts_timeout;
    clock_gettime(CLOCK_REALTIME, &ts_timeout);
    ts_timeout.tv_sec += 6;

    pthread_mutex_lock(&rb_mutex);
    while (keep_running && rb_available(&rb) < iq_bytes) {
        int rc = pthread_cond_timedwait(&rb_cond, &rb_mutex, &ts_timeout);
        if (rc == ETIMEDOUT) break;
    }
    pthread_mutex_unlock(&rb_mutex);
    printf("[CALDBG] rb_available after wait=%zu\n", rb_available(&rb));

    if (rb_available(&rb) < iq_bytes) {
        stop_streaming = true;
        hackrf_stop_rx(device);
        printf("[CALDBG] insufficient IQ bytes, timeout path\n");
        return calibration_finish(final_ppm);
    }

    int8_t *linear_buffer = (int8_t*)malloc(iq_bytes);
    if (!linear_buffer) {
        stop_streaming = true;
        hackrf_stop_rx(device);
        printf("[CALDBG] malloc linear_buffer failed\n");
        return calibration_finish(final_ppm);
    }

    rb_read(&rb, linear_buffer, iq_bytes);
    stop_streaming = true;
    hackrf_stop_rx(device);
    printf("[CALDBG] read IQ buffer and stopped RX\n");

    signal_iq_t *sig = load_iq_from_buffer(linear_buffer, iq_bytes);
    free(linear_buffer);
    if (!sig || !sig->signal_iq || sig->n_signal < 4096) {
        printf("[CALDBG] load_iq_from_buffer failed or n_signal too small\n");
        if (sig) free_signal_iq(sig);
        return calibration_finish(final_ppm);
    }
    printf("[CALDBG] IQ loaded: n_signal=%zu\n", sig->n_signal);

    iq_compensation(sig);
    printf("[CALDBG] iq_compensation done\n");

    PsdConfig_t sweep_cfg = {0};
    int nperseg = 65536;
    while ((size_t)nperseg > sig->n_signal && nperseg > 2048) nperseg >>= 1;
    sweep_cfg.nperseg = nperseg;
    sweep_cfg.noverlap = nperseg / 2;
    sweep_cfg.sample_rate = fs;
    sweep_cfg.window_type = HAMMING_TYPE;

    double *f_sweep = (double*)malloc((size_t)nperseg * sizeof(double));
    double *p_sweep = (double*)malloc((size_t)nperseg * sizeof(double));
    if (!f_sweep || !p_sweep) {
        if (f_sweep) free(f_sweep);
        if (p_sweep) free(p_sweep);
        free_signal_iq(sig);
        return calibration_finish(final_ppm);
    }
    execute_welch_psd(sig, &sweep_cfg, f_sweep, p_sweep);
    printf("[CALDBG] sweep welch done nperseg=%d\n", nperseg);

    double sweep_median_db = median_of_double_copy(p_sweep, nperseg);
    double sweep_thresh_db = sweep_median_db + 5.0;
    printf("[CALDBG] sweep median=%.3f dB threshold=%.3f dB\n", sweep_median_db, sweep_thresh_db);

    int top_idx[6] = {-1, -1, -1, -1, -1, -1};
    double top_pow[6] = {-1e300, -1e300, -1e300, -1e300, -1e300, -1e300};
    int min_peak_dist = (int)llround(300.0 * ((double)nperseg / 65536.0));
    if (min_peak_dist < 8) min_peak_dist = 8;

    for (int i = 1; i < nperseg - 1; ++i) {
        if (p_sweep[i] < sweep_thresh_db) continue;
        if (!(p_sweep[i] > p_sweep[i - 1] && p_sweep[i] >= p_sweep[i + 1])) continue;

        int too_close = 0;
        for (int k = 0; k < 6; ++k) {
            if (top_idx[k] >= 0 && abs(i - top_idx[k]) < min_peak_dist) {
                too_close = 1;
                break;
            }
        }
        if (too_close) continue;

        int pos = -1;
        for (int k = 0; k < 6; ++k) {
            if (p_sweep[i] > top_pow[k]) {
                pos = k;
                break;
            }
        }
        if (pos >= 0) {
            for (int m = 5; m > pos; --m) {
                top_pow[m] = top_pow[m - 1];
                top_idx[m] = top_idx[m - 1];
            }
            top_pow[pos] = p_sweep[i];
            top_idx[pos] = i;
        }
    }

    for (int c = 0; c < 6; ++c) {
        if (top_idx[c] >= 0) {
            double cand_f = (double)fc + f_sweep[top_idx[c]];
            printf("[CALDBG] cand[%d]: idx=%d f=%.3fHz p=%.3fdB\n", c, top_idx[c], cand_f, top_pow[c]);
        } else {
            printf("[CALDBG] cand[%d]: none\n", c);
        }
    }

    int best_k = -1;
    int best_stereo = 0;
    double best_snr = -1e300;
    double best_sweep = -1e300;

    double strongest_sweep = -1e300;
    for (int c = 0; c < 6; ++c) {
        if (top_idx[c] >= 0 && top_pow[c] > strongest_sweep) strongest_sweep = top_pow[c];
    }
    const double sweep_gate_db = 8.0; // descarta candidatos muy débiles respecto al más fuerte
    printf("[CALDBG] strongest_sweep=%.3f dB gate=%.3f dB\n", strongest_sweep, sweep_gate_db);

    for (int c = 0; c < 6; ++c) {
        if (top_idx[c] < 0) continue;

        double cand_freq = (double)fc + f_sweep[top_idx[c]];
        double offset_hz = cand_freq - (double)fc;

        signal_iq_t cand_sig = {0};
        cand_sig.n_signal = sig->n_signal;
        cand_sig.signal_iq = (double complex*)malloc(sig->n_signal * sizeof(double complex));
        if (!cand_sig.signal_iq) continue;

        double phase = 0.0;
        double dphi = -2.0 * M_PI * (offset_hz / fs);
        for (size_t n = 0; n < sig->n_signal; ++n) {
            double complex rot = cos(phase) + I * sin(phase);
            cand_sig.signal_iq[n] = sig->signal_iq[n] * rot;
            phase += dphi;
            if (phase > M_PI) phase -= 2.0 * M_PI;
            if (phase < -M_PI) phase += 2.0 * M_PI;
        }

        fm_radio_t fm = {0};
        const int cal_audio_fs = 200000;
        fm_radio_init(&fm, fs, cal_audio_fs, 0);
        fm.enable_lpf = 0;
        fm.enable_dc_block = 0;
        fm.gain = 4000.0f;

        int16_t *pcm = (int16_t*)malloc(cand_sig.n_signal * sizeof(int16_t));
        if (!pcm) {
            free(cand_sig.signal_iq);
            continue;
        }

        int n_audio = fm_radio_iq_to_pcm(&fm, &cand_sig, pcm, NULL, (int)llround(fs));
        free(cand_sig.signal_iq);
        if (n_audio < 2048) {
            printf("[CALDBG] cand[%d] rejected: n_audio=%d\n", c, n_audio);
            free(pcm);
            continue;
        }
        printf("[CALDBG] cand[%d] demod ok: n_audio=%d fs_audio_eff_pre=%.3f\n", c, n_audio, fs * ((double)n_audio / (double)sig->n_signal));

        signal_iq_t audio_sig = {0};
        audio_sig.n_signal = (size_t)n_audio;
        audio_sig.signal_iq = (double complex*)malloc((size_t)n_audio * sizeof(double complex));
        if (!audio_sig.signal_iq) {
            free(pcm);
            continue;
        }
        for (int n = 0; n < n_audio; ++n) {
            audio_sig.signal_iq[n] = (double)pcm[n] + I * 0.0;
        }
        free(pcm);

        double fs_audio_eff = fs * ((double)n_audio / (double)sig->n_signal);
        if (!(fs_audio_eff > 0.0)) fs_audio_eff = (double)cal_audio_fs;

        PsdConfig_t aud_cfg = {0};
        int aud_nperseg = 65536;
        while (aud_nperseg > n_audio && aud_nperseg > 1024) aud_nperseg >>= 1;
        aud_cfg.nperseg = aud_nperseg;
        aud_cfg.noverlap = aud_nperseg / 2;
        aud_cfg.sample_rate = fs_audio_eff;
        aud_cfg.window_type = HAMMING_TYPE;

        double *f_a = (double*)malloc((size_t)aud_nperseg * sizeof(double));
        double *p_a = (double*)malloc((size_t)aud_nperseg * sizeof(double));
        if (!f_a || !p_a) {
            if (f_a) free(f_a);
            if (p_a) free(p_a);
            free(audio_sig.signal_iq);
            continue;
        }

        execute_welch_psd(&audio_sig, &aud_cfg, f_a, p_a);
        free(audio_sig.signal_iq);

        double max_lin = 0.0;
        int pilot_max_idx = -1;
        double *pilot_lin = (double*)malloc((size_t)aud_nperseg * sizeof(double));
        int cnt_db = 0;
        for (int i = 0; i < aud_nperseg; ++i) {
            if (f_a[i] >= 18000.0 && f_a[i] <= 20000.0) {
                double plin = pow(10.0, p_a[i] / 10.0);
                pilot_lin[cnt_db] = plin;
                if (plin > max_lin) {
                    max_lin = plin;
                    pilot_max_idx = i;
                }
                cnt_db++;
            }
        }

        double median_lin = median_of_double_copy(pilot_lin, cnt_db);
        free(pilot_lin);

        double snr_db = -1e300;
        if (cnt_db > 0 && median_lin > 0.0 && max_lin > 0.0) {
            snr_db = 10.0 * log10(max_lin / median_lin);
        }

         double pilot_freq_hz = (pilot_max_idx >= 0) ? f_a[pilot_max_idx] : 0.0;

        int stereo = (snr_db > 8.0) ? 1 : 0;
        int strong_enough = (top_pow[c] >= (strongest_sweep - sweep_gate_db)) ? 1 : 0;
         printf("[CALDBG] cand[%d] pilot: freq=%.3fHz bins=%d max_lin=%.6e med_lin=%.6e snr=%.3fdB stereo=%d\n",
             c, pilot_freq_hz, cnt_db, max_lin, median_lin, snr_db, stereo);
        printf("[CALDBG] cand[%d] sweep=%.3f strong_enough=%d\n", c, top_pow[c], strong_enough);

        if (!strong_enough) {
            free(f_a);
            free(p_a);
            continue;
        }

        if ((stereo > best_stereo) ||
            (stereo == best_stereo && top_pow[c] > best_sweep) ||
            (stereo == best_stereo && fabs(top_pow[c] - best_sweep) < 1e-9 && snr_db > best_snr)) {
            best_stereo = stereo;
            best_snr = snr_db;
            best_sweep = top_pow[c];
            best_k = c;
            printf("[CALDBG] cand[%d] is new best\n", c);
        }

        free(f_a);
        free(p_a);
    }

    printf("[CALDBG] best_k=%d best_stereo=%d best_snr=%.3f best_sweep=%.3f\n", best_k, best_stereo, best_snr, best_sweep);
    if (best_k >= 0 && top_idx[best_k] >= 0) {
        double best_freq = (double)fc + f_sweep[top_idx[best_k]];
        double best_offset = best_freq - (double)fc;
        printf("[CALDBG] best_freq=%.3fHz best_offset=%.3fHz\n", best_freq, best_offset);

        int decim = 100;
        size_t n_dec = sig->n_signal / (size_t)decim;
        if (n_dec >= 4096) {
            signal_iq_t bb_dec = {0};
            bb_dec.n_signal = n_dec;
            bb_dec.signal_iq = (double complex*)malloc(n_dec * sizeof(double complex));

            if (bb_dec.signal_iq) {
                double phase = 0.0;
                double dphi = -2.0 * M_PI * (best_offset / fs);
                size_t out = 0;
                for (size_t i = 0; i + (size_t)decim <= sig->n_signal; i += (size_t)decim) {
                    double complex acc = 0.0 + I * 0.0;
                    for (int m = 0; m < decim; ++m) {
                        double complex rot = cos(phase) + I * sin(phase);
                        acc += sig->signal_iq[i + (size_t)m] * rot;
                        phase += dphi;
                        if (phase > M_PI) phase -= 2.0 * M_PI;
                        if (phase < -M_PI) phase += 2.0 * M_PI;
                    }
                    bb_dec.signal_iq[out++] = acc / (double)decim;
                }
                bb_dec.n_signal = out;

                if (bb_dec.n_signal >= 4096) {
                    PsdConfig_t bb_cfg = {0};
                    int bb_nperseg = 16384;
                    while ((size_t)bb_nperseg > bb_dec.n_signal && bb_nperseg > 1024) bb_nperseg >>= 1;
                    bb_cfg.nperseg = bb_nperseg;
                    bb_cfg.noverlap = bb_nperseg / 2;
                    bb_cfg.sample_rate = fs / (double)decim;
                    bb_cfg.window_type = HAMMING_TYPE;

                    double *f_bb = (double*)malloc((size_t)bb_nperseg * sizeof(double));
                    double *p_bb_db = (double*)malloc((size_t)bb_nperseg * sizeof(double));
                    double *p_bb_lin = (double*)malloc((size_t)bb_nperseg * sizeof(double));
                    if (f_bb && p_bb_db && p_bb_lin) {
                        execute_welch_psd(&bb_dec, &bb_cfg, f_bb, p_bb_db);
                        for (int i = 0; i < bb_nperseg; ++i) {
                            p_bb_lin[i] = pow(10.0, p_bb_db[i] / 10.0);
                        }

                        double f0 = f_bb[0];
                        double f1 = f_bb[bb_nperseg - 1];
                        double df = (f1 - f0) / (double)(bb_nperseg - 1);

                        if (df > 0.0) {
                            double best_cost = 1e300;
                            double best_delta = 0.0;

                            const int N_DELTA = 1000;
                            const int N_U = 1000;
                            double delta_center = 0.0;
                            double delta_span = 4000.0; // +/- 4 kHz (~40 ppm @100MHz)
                            if (g_has_last_cal_ppm) {
                                delta_center = -((double)g_last_cal_ppm * best_freq) / 1000000.0;
                                delta_span = 2500.0;
                            }
                            if (delta_center > 10000.0) delta_center = 10000.0;
                            if (delta_center < -10000.0) delta_center = -10000.0;
                            double delta_min = delta_center - delta_span;
                            double delta_max = delta_center + delta_span;
                            if (delta_min < -15000.0) delta_min = -15000.0;
                            if (delta_max > 15000.0) delta_max = 15000.0;
                            printf("[CALDBG] fine search window: center=%.3f span=%.3f -> [%.3f, %.3f]\n",
                                   delta_center, delta_span, delta_min, delta_max);

                            for (int id = 0; id < N_DELTA; ++id) {
                                double d = delta_min + ((delta_max - delta_min) * (double)id) / (double)(N_DELTA - 1);
                                double cost = 0.0;
                                int ncost = 0;

                                for (int iu = 0; iu < N_U; ++iu) {
                                    double u = 30000.0 + (60000.0 * (double)iu) / (double)(N_U - 1);
                                    double fl = d - u;
                                    double fr = d + u;

                                    if (fl < f0 || fl > f1 || fr < f0 || fr > f1) continue;

                                    double pl = interp_linear(f_bb, p_bb_lin, bb_nperseg, fl);
                                    double pr = interp_linear(f_bb, p_bb_lin, bb_nperseg, fr);
                                    double denom = (pl + pr);
                                    if (denom < 1e-20) denom = 1e-20;
                                    double e = (pl - pr);
                                    cost += (e * e) / (denom * denom);
                                    ncost++;
                                }

                                if (ncost > 0) {
                                    cost /= (double)ncost;
                                    if (cost < best_cost) {
                                        best_cost = cost;
                                        best_delta = d;
                                    }
                                }
                            }

                            double ppm_suggest = -(best_delta / best_freq) * 1000000.0;
                            final_ppm = (float)ppm_suggest;
                            printf("[CALDBG] fine: best_delta=%.3fHz ppm_suggest=%.6f\n", best_delta, ppm_suggest);
                            if (fabs((double)final_ppm) > 80.0) {
                                printf("[CALDBG] ppm out of guardrail, forcing 0\n");
                                final_ppm = 0.0f;
                            }

                            if (!g_has_last_cal_ppm) {
                                g_last_cal_ppm = final_ppm;
                                g_has_last_cal_ppm = 1;
                                printf("[CALDBG] lock first ppm=%.6f\n", g_last_cal_ppm);
                            } else {
                                float delta_ppm = fabsf(final_ppm - g_last_cal_ppm);
                                if (delta_ppm > 20.0f) {
                                    printf("[CALDBG] ppm outlier detected (delta=%.3f), keep last=%.6f\n", delta_ppm, g_last_cal_ppm);
                                    final_ppm = g_last_cal_ppm;
                                } else {
                                    g_last_cal_ppm = 0.7f * g_last_cal_ppm + 0.3f * final_ppm;
                                    final_ppm = g_last_cal_ppm;
                                    printf("[CALDBG] ppm smoothed -> %.6f\n", final_ppm);
                                }
                            }
                        }
                        else {
                            printf("[CALDBG] invalid df<=0 in fine stage\n");
                        }
                    }
                    else {
                        printf("[CALDBG] fine buffers alloc failed\n");
                    }

                    if (f_bb) free(f_bb);
                    if (p_bb_db) free(p_bb_db);
                    if (p_bb_lin) free(p_bb_lin);
                }

                free(bb_dec.signal_iq);
            }
            else {
                printf("[CALDBG] bb_dec allocation failed\n");
            }
        }
        else {
            printf("[CALDBG] n_dec too small for fine stage: %zu\n", n_dec);
        }
    }
    else {
        printf("[CALDBG] no best candidate found\n");
    }

    free(f_sweep);
    free(p_sweep);
    free_signal_iq(sig);
    printf("[CALDBG] calibration pipeline end final_ppm=%.6f\n", final_ppm);
    return calibration_finish(final_ppm);
}

/**
 * @brief Obtiene el tiempo actual del sistema en milisegundos.
 * @return Entero de 64 bits sin signo que representa los milisegundos desde la Época (Epoch).
 */
static inline uint64_t now_ms(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (uint64_t)tv.tv_sec * 1000ULL + (tv.tv_usec / 1000ULL);
}

/**
 * @brief Envoltura (Wrapper) de usleep para proporcionar precisión en milisegundos.
 * @param[in] ms Tiempo a dormir en milisegundos.
 */
static inline void msleep_int(int ms) {
    if (ms <= 0) return;
    usleep((useconds_t)ms * 1000);
}

/**
 * @brief Resuelve la frecuencia de muestreo IQ para demodulación en tiempo de ejecución.
 * @details Prioriza el valor atómico actualizado por el hilo principal con la configuración
 * activa de HackRF. Si aún no está disponible, reconstruye la tasa a partir del estado
 * del demodulador (decimation_factor * audio_fs), evitando asumir 2 MS/s fijos.
 */
static inline double resolve_demod_fs_hz(const audio_stream_ctx_t *ctx, int mode) {
    if (!ctx) return (double)AUDIO_FS;

    double fs_hz = atomic_load(&ctx->current_fs_hz);
    if (fs_hz > 0.0) return fs_hz;

    int audio_fs = (ctx->opus_sample_rate > 0) ? ctx->opus_sample_rate : AUDIO_FS;

    if (mode == AM_MODE && ctx->am_radio && ctx->am_radio->decim_factor > 0) {
        return (double)ctx->am_radio->decim_factor * (double)audio_fs;
    }

    if (ctx->fm_radio && ctx->fm_radio->decim_factor > 0) {
        return (double)ctx->fm_radio->decim_factor * (double)audio_fs;
    }

    return (double)audio_fs;
}

/**
 * @brief Manejador de señales para SIGINT (Ctrl+C).
 * @details Cambia la bandera global @ref keep_running a falso para iniciar un apagado controlado.
 * @param[in] sig El número de señal (ignorado).
 */
void handle_sigint(int sig) {
    (void)sig;
    keep_running = false;
}

/**
 * @brief Función de retorno (Callback) activada por libhackrf cuando hay nuevas muestras disponibles.
 * @details Esta función se ejecuta en el contexto del hilo del controlador HackRF. Realiza 
 * un procesamiento mínimo para evitar la pérdida de muestras:
 * 1. Escribe los datos IQ crudos en el buffer primario (@ref rb).
 * 2. Si @ref audio_enabled es verdadero, clona los datos en @ref audio_rb.
 * @param[in] transfer Puntero a la estructura hackrf_transfer que contiene los bytes crudos.
 * @return 0 para continuar la transmisión, distinto de cero para detenerla.
 * @note Ejecución de alta frecuencia; evite llamadas bloqueantes o lógica pesada aquí.
 */
int rx_callback(hackrf_transfer* transfer) {
    if (stop_streaming) return 0; 
    if (transfer->valid_length > 0) {
        rb_write(&rb, transfer->buffer, transfer->valid_length);
        if (atomic_load(&audio_enabled)) {
            rb_write(&audio_rb, transfer->buffer, transfer->valid_length);
        }
        // Signal the main thread that data is available
        pthread_mutex_lock(&rb_mutex);
        pthread_cond_signal(&rb_cond);
        pthread_mutex_unlock(&rb_mutex);
    }
    return 0;
}

/**
 * @brief Intenta recuperar el dispositivo HackRF tras una pérdida de conexión.
 * @details Esta función detiene las transmisiones existentes, cierra el dispositivo e intenta 
 * volver a abrirlo hasta 3 veces con un retraso de 1 segundo entre intentos.
 * @return 0 si tiene éxito, -1 si el dispositivo no pudo recuperarse.
 * @note Esta es una llamada bloqueante.
 */
int recover_hackrf(void) {
    printf("\n[RECOVERY] Initiating Hardware Reset sequence...\n");
    if (device != NULL) {
        stop_streaming = true;
        hackrf_stop_rx(device);
        usleep(200000); 
        hackrf_close(device);
        device = NULL;
    }

    int attempts = 0;
    while (attempts < 3 && keep_running) {
        usleep(1000000); 
        if (hackrf_open(&device) == HACKRF_SUCCESS) {
            printf("[RECOVERY] Device Re-opened successfully.\n");
            memset(&current_hw_cfg, 0, sizeof(SDR_cfg_t)); 
            return 0;
        }
        attempts++;
        fprintf(stderr, "[RECOVERY] Attempt %d failed.\n", attempts);
    }
    return -1;
}

/**
 * @brief Serializa los datos de PSD y metadatos de RF en JSON y los envía vía ZMQ.
 * @details Utiliza cJSON para construir una carga útil que contiene los límites de frecuencia, 
 * métricas específicas del modo (profundidad AM o excursión FM) y el arreglo de PSD crudo.
 * @param[in] psd_array Arreglo de valores de densidad espectral de potencia en doble precisión.
 * @param[in] length Tamaño del arreglo psd_array.
 * @param[in] local_hack Configuración actual del hardware para cálculos de frecuencia.
 * @param[in] rf_mode Modo de operación actual (ej. FM_MODE, AM_MODE, PSD_MODE).
 * @param[in] am_depth Profundidad de modulación AM calculada.
 * @param[in] fm_dev Desviación de frecuencia FM calculada.
 */
void publish_results(double* psd_array, int length, SDR_cfg_t *local_hack, uint64_t original_center_freq, int rf_mode, float am_depth, float fm_dev) {
    if (!zmq_channel || !psd_array || length <= 0) return;
    
    cJSON *root = cJSON_CreateObject();
    double fs = local_hack->sample_rate;
    /* Use original center_freq (without PPM correction) for frequency labels.
       This ensures the payload reports nominal frequencies, not corrected ones. */
    double start_freq = (double)original_center_freq - (fs / 2.0);
    double end_freq   = (double)original_center_freq + (fs / 2.0);
    
    cJSON_AddNumberToObject(root, "start_freq_hz", start_freq);
    cJSON_AddNumberToObject(root, "end_freq_hz", end_freq);
    
    if (rf_mode == FM_MODE) {
        cJSON_AddNumberToObject(root, "excursion_hz", (double)fm_dev);
    } else if (rf_mode == AM_MODE){
        cJSON_AddNumberToObject(root, "depth", (double)am_depth * 100.0);
    }
    
    cJSON_AddItemToObject(root, "Pxx", cJSON_CreateDoubleArray(psd_array, length));
    
    char *json_string = cJSON_PrintUnformatted(root); 
    if (json_string) {
        zpair_send(zmq_channel, json_string);
        free(json_string);
    }
    cJSON_Delete(root);
}

/**
 * @brief Procesa comandos de configuración entrantes de ZMQ.
 * @details Analiza la carga útil JSON, actualiza las estructuras de configuración globales 
 * utilizando @ref cfg_mutex para seguridad entre hilos y gestiona el estado de @ref audio_enabled.
 * @param[in] payload La cadena JSON cruda recibida de ZMQ.
 */
void on_command_received(const char *payload) {
    DesiredCfg_t temp_desired;
    SDR_cfg_t temp_hack;
    PsdConfig_t temp_psd;
    RB_cfg_t temp_rb;

    if (parse_config_rf(payload, &temp_desired) == 0) {
        printf("[RF]<<<<<zmq\n");

        if (temp_desired.calibrate) {
            float _cal_ppm = calibrate_hackrf();
            
            // Enviar respuesta de calibración
            cJSON *response = cJSON_CreateObject();
            cJSON_AddStringToObject(response, "status", "calibration_complete");
            cJSON_AddNumberToObject(response, "ppm_error", (double)_cal_ppm);
            char *response_str = cJSON_PrintUnformatted(response);
            cJSON_Delete(response);
            
            if (zpair_send(zmq_channel, response_str) >= 0) {
                printf("[RF] Respuesta de calibración enviada: ppm=%.6f\n", _cal_ppm);
            } else {
                printf("[RF] Error al enviar respuesta de calibración\n");
            }
            free(response_str);
            return;
        }

        //Enable or disable audio based on RF mode
        if (temp_desired.rf_mode == PSD_MODE) {
            atomic_store(&audio_enabled, false);
        } else {
            // If we were OFF and are turning ON, reset the buffer to ensure fresh audio
            if (!atomic_load(&audio_enabled)) {
                rb_reset(&audio_rb); 
            }
            atomic_store(&audio_enabled, true);
        }

        find_params_psd(temp_desired, &temp_hack, &temp_psd, &temp_rb);
        
        pthread_mutex_lock(&cfg_mutex);
        if (temp_desired.cooldown_request_set) {
            g_request_cooldown_s = temp_desired.cooldown_request;
        }
        temp_desired.cooldown_request = g_request_cooldown_s;
        desired_config = temp_desired;
        hack_cfg = temp_hack;
        psd_cfg = temp_psd;
        rb_cfg = temp_rb;
        config_received = true; 
        pthread_mutex_unlock(&cfg_mutex);

        print_config_summary_DEPLOY(&desired_config, &hack_cfg, &psd_cfg, &rb_cfg);

        #ifndef NO_COMMON_LIBS
            select_ANTENNA(temp_desired.antenna_port);
        #else
            printf("[GPIO] selected port: %d\n", temp_desired.antenna_port);
        #endif
    }
}

/**
 * @brief Hilo principal de procesamiento y transmisión de audio.
 * @details Implementa el siguiente flujo de trabajo (pipeline):
 * - **Adquisición**: Extrae muestras IQ de 8 bits desde @ref audio_rb.
 * - **Filtrado**: Aplica un filtro de paso de banda IIR mediante @ref iq_iir_filter_apply_inplace.
 * - **Demodulación**: Alterna entre @ref am_radio_local_iq_to_pcm y @ref fm_radio_iq_to_pcm.
 * - **Resampleo/Enmarcado**: Almacena PCM en un buffer para coincidir con el tamaño de trama de Opus (ej. 20ms).
 * - **Red**: Transmite vía @ref opus_tx_send_frame con lógica de reconexión automática.
 * * @param[in,out] arg Puntero a una estructura @ref audio_stream_ctx_t.
 * @return NULL al finalizar el hilo.
 * @warning Se bloquea en @ref ensure_tx_with_retry si la red está caída.
 */
void* audio_thread_fn(void* arg) {
    audio_stream_ctx_t *ctx = (audio_stream_ctx_t*)arg;
    if (!ctx || !ctx->fm_radio || !ctx->am_radio) {
        fprintf(stderr, "[AUDIO] FATAL: ctx or radios NULL\n");
        return NULL;
    }

    // sanity: Opus expects one of the standard rates; we use 48000
    if (!(ctx->opus_sample_rate == 8000  || ctx->opus_sample_rate == 12000 ||
          ctx->opus_sample_rate == 16000 || ctx->opus_sample_rate == 24000 ||
          ctx->opus_sample_rate == 48000)) {
        fprintf(stderr, "[AUDIO] FATAL: invalid opus_sample_rate=%d\n", ctx->opus_sample_rate);
        return NULL;
    }

    const int frame_samples = (ctx->opus_sample_rate * ctx->frame_ms) / 1000; // e.g., 960 @48k/20ms
    if (frame_samples <= 0) {
        fprintf(stderr, "[AUDIO] FATAL: invalid frame_samples\n");
        return NULL;
    }

    int8_t  *raw_iq_chunk = (int8_t*)malloc((size_t)AUDIO_CHUNK_SAMPLES * 2);
    int16_t *pcm_out      = (int16_t*)malloc((size_t)AUDIO_CHUNK_SAMPLES * sizeof(int16_t));

    signal_iq_t audio_sig;
    audio_sig.n_signal = AUDIO_CHUNK_SAMPLES;
    audio_sig.signal_iq = (double complex*)malloc((size_t)AUDIO_CHUNK_SAMPLES * sizeof(double complex));

    int16_t *pcm_accum = (int16_t*)malloc((size_t)frame_samples * sizeof(int16_t));
    int accum_len = 0;

    if (!raw_iq_chunk || !pcm_out || !audio_sig.signal_iq || !pcm_accum) {
        fprintf(stderr, "[AUDIO] FATAL: malloc failed\n");
        free(raw_iq_chunk);
        free(pcm_out);
        free(audio_sig.signal_iq);
        free(pcm_accum);
        return NULL;
    }

    opus_tx_t *tx = NULL;

    // local helper: (re)connect opus tx


    audio_thread_running = true;

    // track mode/fs changes to reconfig IQ filter cleanly
    int    last_mode = -1;
    double last_fs   = 0.0;

    // metrics reporter (added only for metrics)
    uint64_t last_metrics_ms = now_ms();
    const uint64_t METRICS_EVERY_MS = 500;

    while (audio_thread_running) {

        // Ensure TCP/Opus encoder is ready (infinite retries, 3s)
        if (ensure_tx_with_retry(ctx, &tx, &audio_thread_running) != 0) {
            // thread stopping
            break;
        }

        // Wait for enough IQ bytes
        if (rb_available(&audio_rb) < (size_t)(AUDIO_CHUNK_SAMPLES * 2)) {
            msleep_int(10);
            continue;
        }

        // Drain one chunk
        rb_read(&audio_rb, raw_iq_chunk, AUDIO_CHUNK_SAMPLES * 2);

        // Convert int8 IQ -> complex double (normalized)
        for (int i = 0; i < AUDIO_CHUNK_SAMPLES; ++i) {
            double real = ((double)raw_iq_chunk[2*i]) / 128.0;
            double imag = ((double)raw_iq_chunk[2*i + 1]) / 128.0;
            audio_sig.signal_iq[i] = real + imag * I;
        }

        // Read current mode/fs (set by main thread)
        int mode = atomic_load(&ctx->current_mode);
        double fs_hz = resolve_demod_fs_hz(ctx, mode);

        // ===== IQ CHANNEL FILTER =====
        if (IQ_FILTER_ENABLE) {
            float bw = (mode == AM_MODE) ? IQ_FILTER_BW_AM_HZ : IQ_FILTER_BW_FM_HZ;

            ctx->iqf_cfg.type_filter  = BANDPASS_TYPE;
            ctx->iqf_cfg.order_fliter = IQ_FILTER_ORDER;
            ctx->iqf_cfg.bw_filter_hz = bw;

            // init or reconfig if mode/fs changed
            if (!ctx->iqf_ready) {
                if (iq_iir_filter_init(&ctx->iqf, fs_hz, &ctx->iqf_cfg, 1) == 0) {
                    ctx->iqf_ready = 1;
                    last_mode = mode;
                    last_fs = fs_hz;
                }
            } else {
                if (mode != last_mode || fabs(fs_hz - last_fs) > 1e-6) {
                    iq_iir_filter_config(&ctx->iqf, fs_hz, &ctx->iqf_cfg);
                    iq_iir_filter_reset(&ctx->iqf);
                    last_mode = mode;
                    last_fs = fs_hz;
                }
            }

            if (ctx->iqf_ready) {
                iq_iir_filter_apply_inplace(&ctx->iqf, &audio_sig);
            }
        }

        // ===== Demod IQ -> PCM (FM or AM) =====
        int samples_gen = 0;
        if (mode == AM_MODE) {
            samples_gen = am_radio_local_iq_to_pcm(ctx->am_radio, &audio_sig, pcm_out, &ctx->am_depth);
        } else {
            // default: FM
            // >>> FIX: pass metrics state + fs_demod <<<
            samples_gen = fm_radio_iq_to_pcm(
                ctx->fm_radio,
                &audio_sig,
                pcm_out,
                &ctx->fm_dev,
                (int)llround(fs_hz)
            );
        }

        // ===== metrics print (added only for metrics) =====
        uint64_t tnow = now_ms();
        if (tnow - last_metrics_ms >= METRICS_EVERY_MS) {
            last_metrics_ms = tnow;

            if (mode == AM_MODE) {
                float depth_pct = 100.0f * ctx->am_depth.depth_ema;
                if (isfinite(depth_pct)) {
                    fprintf(stderr, "[AM] depth=%.1f %%\n", depth_pct);
                }
            } else {
                float dev_ema = ctx->fm_dev.dev_ema_hz;
                float dev_pk  = ctx->fm_dev.dev_max_hz;
                if (isfinite(dev_ema) || isfinite(dev_pk)) {
                    fprintf(stderr, "[FM] dev_ema=%.1f Hz  dev_peak=%.1f Hz  fs=%d\n",
                            dev_ema, dev_pk, (int)llround(fs_hz));
                }
            }
        }

        if (samples_gen <= 0) continue;

        // Ensure TCP/Opus encoder is ready
        if (ensure_tx_with_retry(ctx, &tx, &audio_thread_running) != 0) {
            // Se solicitó detener el hilo o el programa
            break;
        }

        // Accumulate into exact Opus frames
        int idx = 0;
        while (idx < samples_gen) {
            int space = frame_samples - accum_len;
            int take  = samples_gen - idx;
            if (take > space) take = space;

            memcpy(&pcm_accum[accum_len], &pcm_out[idx], (size_t)take * sizeof(int16_t));
            accum_len += take;
            idx += take;

            if (accum_len == frame_samples) {
                if (opus_tx_send_frame(tx, pcm_accum, frame_samples) != 0) {
                    fprintf(stderr, "[AUDIO] WARN: opus_tx_send_frame failed. Reconnecting in 3s...\n");
                    opus_tx_destroy(tx);
                    tx = NULL;
                    accum_len = 0;
                    sleep_cancelable_ms(RECONNECT_DELAY_MS, &audio_thread_running);
                    break;
                }
                accum_len = 0;
            }
        }
    }

    if (tx) opus_tx_destroy(tx);

    if (ctx->iqf_ready) {
        iq_iir_filter_free(&ctx->iqf);
        ctx->iqf_ready = 0;
    }

    free(raw_iq_chunk);
    free(pcm_out);
    free(audio_sig.signal_iq);
    free(pcm_accum);
    return NULL;
}
/** @} */

/**
 * @brief Punto de entrada de la aplicación y Máquina de Estados del Hardware.
 * @details Gestiona el ciclo de vida de alto nivel:
 * 1. Inicializa ZMQ y el hardware HackRF.
 * 2. **Estado Inactivo (Idle)**: Cierra la radio si no se reciben comandos en 15s para ahorrar energía/calor.
 * 3. **Estado Activo**: Detecta cambios de configuración, realiza "Sintonización Perezosa" (lazy tuning), 
 * y gestiona el bucle de procesamiento de PSD a alta velocidad.
 * 4. **Limpieza**: Asegura el cierre de hilos y liberación del hardware ante SIGINT/SIGTERM.
 */
int main() {

    // 1. Force OpenMP to yield CPU instead of spinning
    // must be set before OpenMP runtime initializes
    setenv("OMP_WAIT_POLICY", "PASSIVE", 1); 
    
    // 2. Prevent OpenMP from binding threads to specific cores (let OS scheduler decide)
    setenv("OMP_PROC_BIND", "FALSE", 1); 

    // 3. Limit threads to Cores - 1 (Assuming Pi has 4 cores, use 3)
    omp_set_num_threads(3);

    // Desactiva el buffering de stdout completamente
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);
    
    signal(SIGINT, handle_sigint);
    signal(SIGTERM, handle_sigint);
    signal(SIGPIPE, SIG_IGN); // Added to prevent crash on broken TCP audio pipes

    char *ipc_addr = getenv_c("IPC_ADDR");
    if (!ipc_addr) ipc_addr = strdup("ipc:///tmp/rf_engine");
    
    printf("[RF] Starting Engine. IPC=%s\n", ipc_addr);

    zmq_channel = zpair_init(ipc_addr, on_command_received, 0);
    if (!zmq_channel) return 1;
    zpair_start(zmq_channel); 

    printf("[RF] Initializing HackRF Library...\n");
    while (hackrf_init() != HACKRF_SUCCESS) {
        fprintf(stderr, "[RF] Error: HackRF Init failed. Retrying in 5s...\n");
        sleep(5);
    }
    printf("[RF] HackRF Library Initialized.\n");

    // --- AUDIO & RING BUFFER INIT ---
    size_t FIXED_BUFFER_SIZE = 100 * 1024 * 1024; 
    rb_init(&rb, FIXED_BUFFER_SIZE);
    
    // Audio ring buffer initialization
    size_t AUDIO_BUFFER_SIZE = AUDIO_CHUNK_SAMPLES * 2 * 8;
    rb_init(&audio_rb, AUDIO_BUFFER_SIZE);

    // Audio resource allocation
    fm_radio_t *radio_ptr = (fm_radio_t*)malloc(sizeof(fm_radio_t));
    am_radio_local_t *am_ptr = (am_radio_local_t*)malloc(sizeof(am_radio_local_t));
    if (!radio_ptr || !am_ptr) {
        fprintf(stderr, "[RF] FATAL: malloc radio resources failed\n");
        return 1;
    }
    memset(radio_ptr, 0, sizeof(fm_radio_t));
    memset(am_ptr, 0, sizeof(am_radio_local_t));

    bool audio_thread_created = false;
    double last_radio_sample_rate = 0.0;
    rf_processing_workspace_t proc_ws = {0};

    // Audio streaming context setup
    audio_stream_ctx_t audio_ctx;
    audio_stream_ctx_defaults(&audio_ctx, radio_ptr, am_ptr);

    fprintf(stderr, "[AUDIO] Stream target TCP %s:%d (Opus sr=%d ch=%d)\n",
            audio_ctx.tcp_host, audio_ctx.tcp_port,
            audio_ctx.opus_sample_rate, audio_ctx.opus_channels);

    struct timespec last_activity_time;
    clock_gettime(CLOCK_MONOTONIC, &last_activity_time);

    SDR_cfg_t local_hack;
    RB_cfg_t local_rb;
    PsdConfig_t local_psd;
    DesiredCfg_t local_desired;

    while (keep_running) {
        // --- 1. IDLE / TIMEOUT MANAGEMENT (Preserved) ---
        if (!config_received) {
            struct timespec now;
            clock_gettime(CLOCK_MONOTONIC, &now);
            double elapsed = (now.tv_sec - last_activity_time.tv_sec) + 
                             (now.tv_nsec - last_activity_time.tv_nsec) / 1e9;

            if (elapsed >= 15.0 && device != NULL && !atomic_load(&calibration_running)) {
                printf("[RF] Idle timeout (%.1fs). Closing radio.\n", elapsed);
                stop_streaming = true;
                hackrf_stop_rx(device);
                usleep(100000); 
                hackrf_close(device);
                device = NULL;
                memset(&current_hw_cfg, 0, sizeof(SDR_cfg_t)); 
            }
            usleep(10000); 
            continue;
        }

        // --- 2. SNAPSHOT CONFIG ---
        pthread_mutex_lock(&cfg_mutex);
        memcpy(&local_hack, &hack_cfg, sizeof(SDR_cfg_t));
        memcpy(&local_rb, &rb_cfg, sizeof(RB_cfg_t));
        memcpy(&local_psd, &psd_cfg, sizeof(PsdConfig_t));
        memcpy(&local_desired, &desired_config, sizeof(DesiredCfg_t));
        
        // Audio Logic: Update audio thread mode/fs atomics
        atomic_store(&audio_ctx.current_mode, (int)local_desired.rf_mode);
        atomic_store(&audio_ctx.current_fs_hz, (double)local_hack.sample_rate);

        config_received = false; 
        pthread_mutex_unlock(&cfg_mutex);
        clock_gettime(CLOCK_MONOTONIC, &last_activity_time);

        // --- 3. HARDWARE PREP ---
        if (device == NULL) {
            if (hackrf_open(&device) != HACKRF_SUCCESS) {
                recover_hackrf();
                continue;
            }
        }

        bool needs_tune = (local_hack.center_freq != current_hw_cfg.center_freq ||
                           local_hack.sample_rate != current_hw_cfg.sample_rate ||
                           local_hack.lna_gain    != current_hw_cfg.lna_gain    ||
                           local_hack.vga_gain    != current_hw_cfg.vga_gain    ||
                   fabs((double)local_hack.ppm_error - (double)current_hw_cfg.ppm_error) > 1e-6);

        if (needs_tune) {
            printf("[HAL] Tuning: %" PRIu64 " Hz | LNA: %u | VGA: %u\n", 
                    local_hack.center_freq, local_hack.lna_gain, local_hack.vga_gain);
            hackrf_apply_cfg(device, &local_hack);
            memcpy(&current_hw_cfg, &local_hack, sizeof(SDR_cfg_t));
            
            usleep(150000); 
            rb_reset(&rb); 
            rb_reset(&audio_rb); // Also reset audio buffer on tune
        }

        // --- AUDIO THREAD & RADIO INIT ---
        // Initialize or re-init radios only if sample_rate changed
        if (!audio_thread_created || fabs(last_radio_sample_rate - local_hack.sample_rate) > 1e-6) {
            fm_radio_init(radio_ptr, local_hack.sample_rate, audio_ctx.opus_sample_rate, 75);
            am_radio_local_init(am_ptr, local_hack.sample_rate, audio_ctx.opus_sample_rate);
            last_radio_sample_rate = local_hack.sample_rate;

            // Reset metrics window state
            memset(&audio_ctx.fm_dev, 0, sizeof(audio_ctx.fm_dev));
            memset(&audio_ctx.am_depth, 0, sizeof(audio_ctx.am_depth));
            audio_ctx.am_depth.env_min = 1e9f;
            audio_ctx.am_depth.report_samples = (uint32_t)audio_ctx.opus_sample_rate;
        }

        // Start audio thread once
        if (!audio_thread_created) {
            if (pthread_create(&audio_thread, NULL, audio_thread_fn, (void*)&audio_ctx) == 0) {
                audio_thread_created = true;
            } else {
                fprintf(stderr, "[RF] Warning: failed to create audio thread\n");
            }
        }

        if (stop_streaming) {
            rb_reset(&rb);
            rb_reset(&audio_rb);
            stop_streaming = false;
            if (hackrf_start_rx(device, rx_callback, NULL) != HACKRF_SUCCESS) {
                recover_hackrf();
                continue;
            }
        }

        // --- 4. DATA ACQUISITION ---
        // Calculate a 5-second safety timeout relative to now
        struct timespec ts_timeout;
        clock_gettime(CLOCK_REALTIME, &ts_timeout);
        ts_timeout.tv_sec += 5;

        pthread_mutex_lock(&rb_mutex);
        while (keep_running && rb_available(&rb) < local_rb.total_bytes) {
            // Wait until signaled OR timeout (5s)
            int rc = pthread_cond_timedwait(&rb_cond, &rb_mutex, &ts_timeout);
            if (rc == ETIMEDOUT) {
                break; // Break loop to trigger recovery logic below
            }
        }
        pthread_mutex_unlock(&rb_mutex);

        // Check if we actually got data or if we timed out
        if (rb_available(&rb) < local_rb.total_bytes && keep_running) {
            fprintf(stderr, "[RF] Error: Acquisition Timeout (buffer empty).\n");
            recover_hackrf();
            clock_gettime(CLOCK_MONOTONIC, &last_activity_time);
            continue;
        }

        // --- 5. PROCESSING WITH SAFETY CHECKS ---
        // With smaller IQ chunks, this loop runs more often.
        // Watchdog cadence: publish PSD using configurable cooldown.
        static struct timespec last_psd_run = {0};
        struct timespec now_psd;
        clock_gettime(CLOCK_MONOTONIC, &now_psd);

        double cooldown_s = local_desired.cooldown_request;
        if (cooldown_s < 0.0) {
            cooldown_s = 0.0;
        }

        double time_diff = (now_psd.tv_sec - last_psd_run.tv_sec) +
                           (now_psd.tv_nsec - last_psd_run.tv_nsec) / 1e9;

        // Watchdog pacing without dropping request/response:
        // if less than configured cooldown has elapsed, wait only remaining time,
        // then continue processing this same request.
        if (time_diff < cooldown_s) {
            double remaining_s = cooldown_s - time_diff;
            useconds_t remaining_us = (useconds_t)(remaining_s * 1000000.0);
            if (remaining_us > 0) {
                usleep(remaining_us);
            }
        }

        if (rf_workspace_ensure(&proc_ws, local_rb.total_bytes, local_psd.nperseg) == 0) {
            size_t buf_bytes = local_rb.total_bytes;
            size_t iq_points = buf_bytes / 2; /* interleaved I,Q each 1 byte */
            double buf_mb = (double)buf_bytes / (1024.0 * 1024.0);
            fprintf(stderr, "[RF] linear_buffer: %zu bytes (%zu IQ points), %.3f MB; PSD nperseg=%d\n",
                    buf_bytes, iq_points, buf_mb, local_psd.nperseg);
            rb_read(&rb, proc_ws.linear_buffer, local_rb.total_bytes);

            if (load_iq_into_signal(proc_ws.linear_buffer, local_rb.total_bytes, &proc_ws.sig) == 0) {
                iq_compensation(&proc_ws.sig);
                if (local_desired.filter_enabled) {
                    chan_filter_apply_inplace_abs(&proc_ws.sig, &local_desired.filter_cfg,
                                                  local_hack.center_freq_corrected, local_hack.sample_rate);
                }

                if (local_desired.method_psd == PFB) {
                    execute_pfb_psd(&proc_ws.sig, &local_psd, proc_ws.freq, proc_ws.psd);
                } else {
                    execute_welch_psd(&proc_ws.sig, &local_psd, proc_ws.freq, proc_ws.psd);
                }

                publish_results(
                    proc_ws.psd,
                    local_psd.nperseg,
                    &local_hack,
                    local_desired.center_freq,
                    (int)local_desired.rf_mode,
                    audio_ctx.am_depth.depth_ema,
                    audio_ctx.fm_dev.dev_ema_hz
                );

                {
                    uint64_t tuned_fc = local_hack.center_freq_corrected;
                    if (tuned_fc == 0) tuned_fc = local_hack.center_freq;
                    printf("[RF_PSD] Acquisition | PPM Error: %.3f | Fc_nom: %" PRIu64 " Hz | Fc_tuned: %" PRIu64 " Hz\n",
                           local_hack.ppm_error, local_hack.center_freq, tuned_fc);
                }

                clock_gettime(CLOCK_MONOTONIC, &last_psd_run);
            } else {
                fprintf(stderr, "[RF] Error: Failed to load IQ signal into reusable workspace.\n");
            }
        } else {
            fprintf(stderr, "[RF] Error: Workspace allocation failed (%zu bytes, nperseg=%d).\n",
                    local_rb.total_bytes, local_psd.nperseg);
        }

        clock_gettime(CLOCK_MONOTONIC, &last_activity_time);
    }

    // --- CLEANUP ---
    printf("[RF] Shutting down...\n");
    audio_thread_running = false; // Flag for audio thread to exit
    if (audio_thread_created) pthread_join(audio_thread, NULL);
    
    zpair_close(zmq_channel);
    rb_free(&rb);
    rb_free(&audio_rb);
    rf_workspace_release(&proc_ws);
    
    if (device) { 
        hackrf_stop_rx(device); 
        hackrf_close(device); 
    }
    hackrf_exit();
    
    if (ipc_addr) free(ipc_addr);
    if (radio_ptr) free(radio_ptr);
    if (am_ptr) free(am_ptr);
    chan_filter_free_cache();
    
    return 0;
}
