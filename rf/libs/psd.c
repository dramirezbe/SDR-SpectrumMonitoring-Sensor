#include "psd.h"

#define PFB_TAPS_PER_CHANNEL 8
#define KAISER_BETA 8.6   // ~80 dB sidelobes

// =========================================================
// Static Helper: Robust String Lowercasing
// =========================================================

// =========================================================
// IQ & Memory Management
// =========================================================

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

void free_signal_iq(signal_iq_t* signal) {
    if (signal) {
        if (signal->signal_iq) {
            free(signal->signal_iq);
            signal->signal_iq = NULL;
        }
        free(signal);
    }
}

// =========================================================
// Filtering Implementation
// =========================================================
static inline double clampd(double x, double lo, double hi) {
    return (x < lo) ? lo : (x > hi) ? hi : x;
}

static inline double db_to_lin_amp(double db) {
    return pow(10.0, db / 20.0);
}

// Raised-cosine 0..1
static inline double raised_cos(double t) {
    t = clampd(t, 0.0, 1.0);
    return 0.5 - 0.5 * cos(M_PI * t);
}

static void apply_half_spectrum_mask_inplace(
    signal_iq_t *sig,
    int keep_negative_half,
    double fs_hz,
    double bw_hz,
    int order
)
{
    if (!sig || !sig->signal_iq || sig->n_signal < 2) return;
    if (fs_hz <= 0.0 || bw_hz <= 0.0) return;
    if (sig->n_signal > (size_t)INT_MAX) return;

    const int N = (int)sig->n_signal;
    order = (order <= 0) ? 1 : order;
    printf("Applying half-spectrum mask...");
    // ---------- Leak depende de BW y orden (robusto) ----------
    double nyq = 0.5 * fs_hz;
    double norm_bw = bw_hz / (nyq + 1e-12);
    norm_bw = clampd(norm_bw, 1e-6, 1.0);

    // BW pequeño => MÁS rechazo (más negativo)
    // BW grande  => MENOS rechazo (menos negativo)
    double leak_db = -22.0
                     - 3.0 * (double)order
                     + 12.0 * log10(norm_bw);

    leak_db = clampd(leak_db, -80.0, -15.0);
    const double leak = db_to_lin_amp(leak_db);

    // ---------- Transición mínima en bins (para BW pequeño y BW grande) ----------
    const double df0 = fs_hz / (double)N;

    // transición base (proporcional a BW, reduce con orden)
    double f_t = 0.25 * bw_hz / sqrt((double)order);

    // mínimo fijo en bins para evitar “cuchillo” y picos
    const int min_bins = 96 + 8 * order;        // perilla principal (sube si aún ves pico)
    const double f_min = (double)min_bins * df0;
    if (f_t < f_min) f_t = f_min;

    // máximo por seguridad (no hagas transición gigante)
    const double f_max = 0.20 * fs_hz;
    if (f_t > f_max) f_t = f_max;

    // ---------- CACHE FFTW ----------
    static int cachedN = 0;
    static fftw_complex *buf_in = NULL;
    static fftw_complex *buf_out = NULL;
    static fftw_plan plan_fwd = NULL;
    static fftw_plan plan_inv = NULL;

    static double *mask_keep_neg = NULL;
    static double *mask_keep_pos = NULL;

    static double last_fs = 0.0, last_leak = -1.0, last_ft = -1.0;
    static int last_order = 0;

    int need_rebuild = 0;

    if (cachedN != N) need_rebuild = 1;
    if (fabs(last_fs - fs_hz) > 1e-9) need_rebuild = 1;
    if (fabs(last_leak - leak) > 1e-12) need_rebuild = 1;
    if (fabs(last_ft - f_t) > 1e-9) need_rebuild = 1;
    if (last_order != order) need_rebuild = 1;

    if (cachedN != N) {
        if (plan_fwd) fftw_destroy_plan(plan_fwd);
        if (plan_inv) fftw_destroy_plan(plan_inv);
        if (buf_in) fftw_free(buf_in);
        if (buf_out) fftw_free(buf_out);
        free(mask_keep_neg);
        free(mask_keep_pos);

        buf_in  = (fftw_complex*)fftw_malloc(sizeof(fftw_complex) * N);
        buf_out = (fftw_complex*)fftw_malloc(sizeof(fftw_complex) * N);
        if (!buf_in || !buf_out) {
            if (buf_in) fftw_free(buf_in);
            if (buf_out) fftw_free(buf_out);
            buf_in = buf_out = NULL;
            cachedN = 0;
            return;
        }

        plan_fwd = fftw_plan_dft_1d(N, buf_in, buf_out, FFTW_FORWARD,  FFTW_ESTIMATE);
        plan_inv = fftw_plan_dft_1d(N, buf_out, buf_in, FFTW_BACKWARD, FFTW_ESTIMATE);

        mask_keep_neg = (double*)malloc(sizeof(double) * N);
        mask_keep_pos = (double*)malloc(sizeof(double) * N);
        if (!plan_fwd || !plan_inv || !mask_keep_neg || !mask_keep_pos) {
            if (plan_fwd) fftw_destroy_plan(plan_fwd);
            if (plan_inv) fftw_destroy_plan(plan_inv);
            if (buf_in) fftw_free(buf_in);
            if (buf_out) fftw_free(buf_out);
            free(mask_keep_neg);
            free(mask_keep_pos);
            plan_fwd = plan_inv = NULL;
            buf_in = buf_out = NULL;
            mask_keep_neg = mask_keep_pos = NULL;
            cachedN = 0;
            return;
        }

        cachedN = N;
        need_rebuild = 1;
    }

    if (!buf_in || !buf_out || !plan_fwd || !plan_inv) return;

    // ---------- Recalcular máscaras (ROBUSTAS, ASIMÉTRICAS) ----------
    if (need_rebuild) {
        const double df = fs_hz / (double)N;

        for (int k = 0; k < N; k++) {
            int ks = (k <= N/2) ? k : (k - N);
            double f = (double)ks * df;

            // keep NEG:
            //  f <= -f_t : 1
            //  -f_t < f < 0 : transicion 1 -> leak
            //  f >= 0 : leak
            double g_neg;
            if (f <= -f_t) {
                g_neg = 1.0;
            } else if (f < 0.0) {
                double t = (f + f_t) / f_t;     // 0..1
                double s = raised_cos(t);
                g_neg = 1.0 + (leak - 1.0) * s; // 1 -> leak
            } else {
                g_neg = leak;
            }

            // keep POS:
            //  f >= +f_t : 1
            //  0 < f < +f_t : transicion leak -> 1
            //  f <= 0 : leak
            double g_pos;
            if (f >= +f_t) {
                g_pos = 1.0;
            } else if (f > 0.0) {
                double t = f / f_t;             // 0..1
                double s = raised_cos(t);
                g_pos = leak + (1.0 - leak) * s;// leak -> 1
            } else {
                g_pos = leak;
            }

            mask_keep_neg[k] = g_neg;
            mask_keep_pos[k] = g_pos;
        }

        last_fs = fs_hz;
        last_leak = leak;
        last_ft = f_t;
        last_order = order;
    }

    // ---------- Ejecutar FFT, aplicar máscara, iFFT ----------
    double *in  = (double*)buf_in;   // [Re0, Im0, Re1, Im1, ...]
    double *out = (double*)buf_out;

    for (int i = 0; i < N; i++) {
        double complex x = sig->signal_iq[i];
        in[2*i + 0] = creal(x);
        in[2*i + 1] = cimag(x);
    }

    fftw_execute(plan_fwd);

    double *mask = keep_negative_half ? mask_keep_neg : mask_keep_pos;
    for (int k = 0; k < N; k++) {
        out[2*k + 0] *= mask[k];
        out[2*k + 1] *= mask[k];
    }

    fftw_execute(plan_inv);

    const double invN = 1.0 / (double)N;
    for (int i = 0; i < N; i++) {
        sig->signal_iq[i] = (in[2*i + 0] * invN) + (in[2*i + 1] * invN) * I;
    }
}



void filter_iq(signal_iq_t *signal_iq, filter_t *filter_cfg) {
    // 1. Validaciones de seguridad
    if (!signal_iq || !signal_iq->signal_iq || !filter_cfg) return;
    if (filter_cfg->sample_rate <= 0.0) return;
    if (filter_cfg->bw_filter_hz <= 0.0) return;

    size_t n = signal_iq->n_signal;
    double complex *data = signal_iq->signal_iq;
    printf("Entering filter_iq\n");

    // 2. Coeficiente alpha (RC)
    double dt = 1.0 / filter_cfg->sample_rate;
    double rc = 1.0 / (2.0 * M_PI * filter_cfg->bw_filter_hz);
    double alpha = dt / (rc + dt);

    // 3. Estado previo (memoria)
    double prev_y_i = filter_cfg->prev_output_i;
    double prev_y_q = filter_cfg->prev_output_q;

    // Flags para los nuevos modos asimétricos
    int do_half_mask = 0;
    int keep_negative_half = 0;

    if (filter_cfg->type_filter == LOWPASS_TYPE) {
        // nuevo pasa bajas: NEG ok / POS casi 0
        do_half_mask = 1;
        keep_negative_half = 1;
    } else if (filter_cfg->type_filter == HIGHPASS_TYPE) {
        // nuevo pasa altas: POS ok / NEG casi 0
        do_half_mask = 1;
        keep_negative_half = 0;
    }

    // 4. Bucle principal (IIR 1er orden)
    for (size_t i = 0; i < n; i++) {
        double curr_i = creal(data[i]);
        double curr_q = cimag(data[i]);

        // Base LowPass (siempre)
        double low_i = prev_y_i + alpha * (curr_i - prev_y_i);
        double low_q = prev_y_q + alpha * (curr_q - prev_y_q);

        double out_i = 0.0;
        double out_q = 0.0;

        switch (filter_cfg->type_filter) {

            // ====== RENOMBRES (MISMO CÓDIGO/PRINCIPIO) ======
            case BANDPASS_TYPE:
                // (old LOWPASS)
                out_i = low_i;
                out_q = low_q;
                break;

            case BANDSTOP_TYPE:
                // (old HIGHPASS) : x - low
                out_i = curr_i - low_i;
                out_q = curr_q - low_q;
                break;

            // ====== NUEVOS ======
            case LOWPASS_TYPE:
            case HIGHPASS_TYPE:
                // Base: bandstop (x - low), luego se hace máscara por semieje con FFT
                out_i = curr_i - low_i;
                out_q = curr_q - low_q;
                break;

            default:
                out_i = curr_i;
                out_q = curr_q;
                break;
        }

        data[i] = out_i + out_q * I;

        // La memoria siempre sigue al LowPass (estable)
        prev_y_i = low_i;
        prev_y_q = low_q;
    }

    // 5. Guardar estado
    filter_cfg->prev_output_i = prev_y_i;
    filter_cfg->prev_output_q = prev_y_q;

    // 6. Post-proceso para los nuevos modos asimétricos (semieje +/-)
    if (do_half_mask) {
        apply_half_spectrum_mask_inplace(signal_iq, keep_negative_half,
                                 filter_cfg->sample_rate,
                                 filter_cfg->bw_filter_hz,
                                 filter_cfg->order_filter);
    }
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

// =========================================================
// DSP Logic
// =========================================================

int scale_psd(double* psd, int nperseg, const char* scale_str) {
    if (!psd || nperseg <= 0) return -1;
    
    const double Z = 50.0; 
    typedef enum { UNIT_DBM, UNIT_DBUV, UNIT_DBMV, UNIT_WATTS, UNIT_VOLTS } Unit_t;
    Unit_t unit = UNIT_DBM; // Default
    
    // STRICT LOWERCASE LOGIC as requested
    char *temp_scale = strdup_lowercase(scale_str); // NULL safe
    if (temp_scale) {
        if (strcmp(temp_scale, "dbuv") == 0) unit = UNIT_DBUV;
        else if (strcmp(temp_scale, "dbmv") == 0) unit = UNIT_DBMV;
        else if (strcmp(temp_scale, "w") == 0)    unit = UNIT_WATTS;
        else if (strcmp(temp_scale, "watts") == 0) unit = UNIT_WATTS;
        else if (strcmp(temp_scale, "v") == 0)    unit = UNIT_VOLTS;
        else if (strcmp(temp_scale, "volts") == 0) unit = UNIT_VOLTS;
        // else defaults to UNIT_DBM
        free(temp_scale);
    }

    // Apply scaling
    for (int i = 0; i < nperseg; i++) {
        double p_raw = psd[i];
        
        // Convert to Watts first (assuming PSD output is effectively V^2 or normalized power)
        // Adjust based on Z if input is V^2
        double p_watts = p_raw / Z; 

        // Floor noise prevention
        if (p_watts < 1.0e-20) p_watts = 1.0e-20; 

        double val_dbm = 10.0 * log10(p_watts * 1000.0);

        switch (unit) {
            case UNIT_DBUV: psd[i] = val_dbm + 107.0; break;
            case UNIT_DBMV: psd[i] = val_dbm + 47.0; break;
            case UNIT_WATTS: psd[i] = p_watts; break;
            case UNIT_VOLTS: psd[i] = sqrt(p_watts * Z); break;
            case UNIT_DBM:
            default: psd[i] = val_dbm; break;
        }
    }
    return 0;
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

    // Calculate window power (S2)
    double u_norm = 0.0;
    for (int i = 0; i < nperseg; i++) u_norm += window[i] * window[i];
    u_norm /= nperseg;

    // Allocate FFTW arrays
    double complex* fft_in = fftw_alloc_complex(nfft);
    double complex* fft_out = fftw_alloc_complex(nfft);
    if (!fft_in || !fft_out) {
        free(window);
        if(fft_in) fftw_free(fft_in);
        if(fft_out) fftw_free(fft_out);
        return;
    }

    fftw_plan plan = fftw_plan_dft_1d(nfft, fft_in, fft_out, FFTW_FORWARD, FFTW_ESTIMATE);

    // Reset Output
    memset(p_out, 0, nfft * sizeof(double));

    // Welch Averaging Loop
    for (int k = 0; k < k_segments; k++) {
        size_t start = k * step;
        
        for (int i = 0; i < nperseg; i++) {
            if ((start + i) < n_signal) {
                fft_in[i] = signal[start + i] * window[i];
            } else {
                fft_in[i] = 0;
            }
        }

        fftw_execute(plan);

        // Accumulate Magnitude Squared
        for (int i = 0; i < nfft; i++) {
            double mag = cabs(fft_out[i]);
            p_out[i] += (mag * mag);
        }
    }

    // Normalization
    if (k_segments > 0 && u_norm > 0) {
        // Average the periodograms
        // Scale by Fs * Sum(w^2)
        // Note: The logic here assumes NPERSEG scaling for ENBW
        double scale = 1.0 / (fs * u_norm * k_segments * nperseg);
        for (int i = 0; i < nfft; i++) p_out[i] *= scale;
    }

    // Shift zero frequency to center
    fftshift(p_out, nfft);

    // Generate Frequency Axis
    double df = fs / nfft;
    for (int i = 0; i < nfft; i++) {
        f_out[i] = -fs / 2.0 + i * df;
    }

    // Cleanup
    free(window);
    fftw_destroy_plan(plan);
    fftw_free(fft_in);
    fftw_free(fft_out);
}

//Funciones PFB

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
    if (!h) return; // Added null check
    generate_kaiser_proto(h, L, KAISER_BETA);

    // Polyphase components
    double* poly[T];
    for (int t = 0; t < T; t++) {
        poly[t] = (double*)malloc(M * sizeof(double));
        for (int m = 0; m < M; m++) {
            poly[t][m] = h[t * M + m];
        }
    }

    // FFT buffers
    double complex* fft_in  = fftw_alloc_complex(M);
    double complex* fft_out = fftw_alloc_complex(M);
    fftw_plan plan = fftw_plan_dft_1d(
        M, fft_in, fft_out, FFTW_FORWARD, FFTW_ESTIMATE
    );

    // -------------------------------------------------
    // PFB Processing
    // -------------------------------------------------
    int blocks = (N - L) / M;
    if (blocks <= 0) goto cleanup;

    for (int b = 0; b < blocks; b++) {
        memset(fft_in, 0, M * sizeof(double complex));

        for (int t = 0; t < T; t++) {
            size_t offset = b * M + t * M;
            for (int m = 0; m < M; m++) {
                fft_in[m] += x[offset + m] * poly[t][m];
            }
        }

        fftw_execute(plan);

        for (int k = 0; k < M; k++) {
            double mag2 = creal(fft_out[k]) * creal(fft_out[k]) +
                          cimag(fft_out[k]) * cimag(fft_out[k]);
            p_out[k] += mag2;
        }
    }

    // -------------------------------------------------
    // Normalization
    // -------------------------------------------------
    double scale = 1.0 / (blocks * fs * M);
    for (int i = 0; i < M; i++) {
        p_out[i] *= scale;
    }

    // --- FFT Shift (Crucial before DC removal) ---
    fftshift(p_out, M);

    // -------------------------------------------------
    // Frequency axis
    // -------------------------------------------------
    double df = fs / M;
    for (int i = 0; i < M; i++) {
        f_out[i] = -fs / 2.0 + i * df;
    }

cleanup:
    for (int t = 0; t < T; t++) free(poly[t]);
    free(h);
    fftw_destroy_plan(plan);
    fftw_free(fft_in);
    fftw_free(fft_out);
}
