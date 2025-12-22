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
 * @brief Final conversion of raw Power Spectral Density to dBm.
 */
static void convert_to_dbm_inplace(double* psd, int length) {
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

   
    convert_to_dbm_inplace(p_out, nfft);

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

    // --- FFT Shift ---
    fftshift(p_out, M);

    convert_to_dbm_inplace(p_out, M);

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
