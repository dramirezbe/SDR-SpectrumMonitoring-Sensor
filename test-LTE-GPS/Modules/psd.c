/**
 * @file Modules/psd.c
 */

#include "psd.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <fftw3.h>
#include <alloca.h>
#include <complex.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

signal_iq_t* load_iq_from_buffer(const int8_t* buffer, size_t buffer_size) {
    size_t n_samples = buffer_size / 2;
    signal_iq_t* signal_data = (signal_iq_t*)malloc(sizeof(signal_iq_t));
    
    signal_data->n_signal = n_samples;
    signal_data->signal_iq = (double complex*)malloc(n_samples * sizeof(double complex));

    for (size_t i = 0; i < n_samples; i++) {
        signal_data->signal_iq[i] = (double)buffer[2 * i] + (double)buffer[2 * i + 1] * I;
    }

    return signal_data;
}

void free_signal_iq(signal_iq_t* signal) {
    if (signal) {
        if (signal->signal_iq) free(signal->signal_iq);
        free(signal);
    }
}


// ----------------------------------------------------------------------
// Scaling Logic (Modified to match your reference)
// ----------------------------------------------------------------------

/**
 * @brief Scales PSD. 
 * CRITICAL: This uses the user's formula P = PSD[i] / 50.
 * It does NOT multiply by RBW, ensuring the noise floor stays at ~-70dBm.
 */
int scale_psd(double* psd, int nperseg, const char* scale_str) {
    if (!psd) return -1;
    
    const double Z = 50.0; // Impedance
    
    typedef enum { UNIT_DBM, UNIT_DBUV, UNIT_DBMV, UNIT_WATTS, UNIT_VOLTS } Unit_t;
    Unit_t unit = UNIT_DBM;
    
    if (scale_str) {
        if (strcmp(scale_str, "dBuV") == 0) unit = UNIT_DBUV;
        else if (strcmp(scale_str, "dBmV") == 0) unit = UNIT_DBMV;
        else if (strcmp(scale_str, "W") == 0)    unit = UNIT_WATTS;
        else if (strcmp(scale_str, "V") == 0)    unit = UNIT_VOLTS;
    }

    for (int i = 0; i < nperseg; i++) {
        
        // 1. YOUR BASE FORMULA (Direct V^2 to Watts)
        // We assume psd[i] is already V^2 magnitude, not density.
        double p_watts = psd[i] / Z;

        // Safety for log10
        if (p_watts < 1.0e-20) p_watts = 1.0e-20; 

        // 2. CALCULATE dBm (The Anchor)
        // Formula: 10 * log10(Watts * 1000)
        double val_dbm = 10.0 * log10(p_watts * 1000.0);

        // 3. CONVERT TO TARGET (Relative to your dBm)
        switch (unit) {
            case UNIT_DBUV:
                // dBuV = dBm + 107
                psd[i] = val_dbm + 107.0;
                break;
            case UNIT_DBMV:
                // dBmV = dBm + 47
                psd[i] = val_dbm + 47.0;
                break;
            case UNIT_WATTS:
                psd[i] = p_watts;
                break;
            case UNIT_VOLTS:
                // V = sqrt(P * R)
                psd[i] = sqrt(p_watts * Z);
                break;
            case UNIT_DBM:
            default:
                psd[i] = val_dbm;
                break;
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
        default:               return 1.0;
    }
}

static void generate_window(PsdWindowType_t window_type, double* window_buffer, int window_length) {
    for (int n = 0; n < window_length; n++) {
        switch (window_type) {
            case HANN_TYPE:
                window_buffer[n] = 0.5 * (1 - cos((2.0 * M_PI * n) / (window_length - 1)));
                break;
            case RECTANGULAR_TYPE:
                window_buffer[n] = 1.0;
                break;
            case BLACKMAN_TYPE:
                window_buffer[n] = 0.42 - 0.5 * cos((2.0 * M_PI * n) / (window_length - 1)) + 0.08 * cos((4.0 * M_PI * n) / (window_length - 1));
                break;
            case HAMMING_TYPE:
            default:
                window_buffer[n] = 0.54 - 0.46 * cos((2.0 * M_PI * n) / (window_length - 1));
                break;
        }
    }
}

static PsdWindowType_t get_window_type_from_string(const char *window_str) {
    if (window_str == NULL) return HAMMING_TYPE; // Default
    
    if (strcasecmp(window_str, "hamming") == 0) return HAMMING_TYPE;
    if (strcasecmp(window_str, "hann") == 0) return HANN_TYPE;
    if (strcasecmp(window_str, "blackman") == 0) return BLACKMAN_TYPE;
    if (strcasecmp(window_str, "rectangular") == 0) return RECTANGULAR_TYPE;

    printf("[PSD]ERROR: Window does not exist, returning rectangular");

    return RECTANGULAR_TYPE;
}

/**
 * Parses JSON string and fills the DesiredCfg_t struct.
 * Returns 0 on success, -1 on failure.
 */
int parse_psd_config(const char *json_string, DesiredCfg_t *target) {
    if (json_string == NULL || target == NULL) {
        return -1;
    }

    // Parse the JSON string
    cJSON *root = cJSON_Parse(json_string);
    if (root == NULL) {
        const char *error_ptr = cJSON_GetErrorPtr();
        if (error_ptr != NULL) {
            fprintf(stderr, "Error before: %s\n", error_ptr);
        }
        return -1;
    }

    // 1. Center Freq (uint64_t)
    cJSON *cf = cJSON_GetObjectItemCaseSensitive(root, "center_freq_hz");
    if (cJSON_IsNumber(cf)) {
        target->center_freq = (uint64_t)cf->valuedouble;
    } else {
        target->center_freq = 0; // Default or Error handling
    }

    // 2. RBW (int)
    cJSON *rbw = cJSON_GetObjectItemCaseSensitive(root, "rbw_hz");
    if (cJSON_IsNumber(rbw)) {
        target->rbw = (int)rbw->valuedouble;
    }

    // 3. Sample Rate (double)
    // RENAMED from 'sr' to 'sample_rate_json'
    cJSON *sample_rate_json = cJSON_GetObjectItemCaseSensitive(root, "sample_rate_hz");
    if (cJSON_IsNumber(sample_rate_json)) {
        target->sample_rate = sample_rate_json->valuedouble;
    }

    // 4. span (double)
    // RENAMED from 'sr' to 'span_json'
    cJSON *span_json = cJSON_GetObjectItemCaseSensitive(root, "span");
    if (cJSON_IsNumber(span_json)) {
        target->span = span_json->valuedouble;
    }

    // 5. overlap (double)
    // RENAMED from 'sr' to 'overlap_json'
    cJSON *overlap_json = cJSON_GetObjectItemCaseSensitive(root, "overlap");
    if (cJSON_IsNumber(overlap_json)) {
        target->overlap = overlap_json->valuedouble;
    }

    // 4. Scale (char*) - Deep Copy
    cJSON *scale = cJSON_GetObjectItemCaseSensitive(root, "scale");
    if (cJSON_IsString(scale) && (scale->valuestring != NULL)) {
        // We use strdup to give the struct ownership of the string
        // Remember to free(target->scale) later!
        target->scale = strdup(scale->valuestring);
    } else {
        target->scale = NULL;
    }

    // 5. Window (Enum)
    cJSON *win = cJSON_GetObjectItemCaseSensitive(root, "window");
    if (cJSON_IsString(win)) {
        target->window_type = get_window_type_from_string(win->valuestring);
    } else {
        target->window_type = RECTANGULAR_TYPE; // Default
    }

    // 6. LNA Gain (int)
    cJSON *lna = cJSON_GetObjectItemCaseSensitive(root, "lna_gain");
    if (cJSON_IsNumber(lna)) {
        target->lna_gain = (int)lna->valuedouble;
    }

    // 7. VGA Gain (int)
    cJSON *vga = cJSON_GetObjectItemCaseSensitive(root, "vga_gain");
    if (cJSON_IsNumber(vga)) {
        target->vga_gain = (int)vga->valuedouble;
    }

    // 8. Antenna Amp (bool)
    cJSON *amp = cJSON_GetObjectItemCaseSensitive(root, "antenna_amp");
    if (cJSON_IsBool(amp)) {
        target->amp_enabled = cJSON_IsTrue(amp);
    }

    // 9. PPM Error (Not in JSON, set default)
    target->ppm_error = 0;

    // Clean up cJSON object
    cJSON_Delete(root);
    return 0;
}

// Helper function to free the memory allocated inside parse_psd_config
void free_desired_psd(DesiredCfg_t *target) {
    if (target && target->scale) {
        free(target->scale);
        target->scale = NULL;
    }
}

static void fftshift(double* data, int n) {
    int half = n / 2;
    double* temp = (double*)alloca(half * sizeof(double));
    memcpy(temp, data, half * sizeof(double));
    memcpy(data, &data[half], (n - half) * sizeof(double));
    memcpy(&data[n - half], temp, half * sizeof(double));
}

void execute_welch_psd(signal_iq_t* signal_data, const PsdConfig_t* config, double* f_out, double* p_out) {
    double complex* signal = signal_data->signal_iq;
    size_t n_signal = signal_data->n_signal;
    int nperseg = config->nperseg;
    int noverlap = config->noverlap;
    double fs = config->sample_rate;
    
    int nfft = nperseg;
    int step = nperseg - noverlap;
    int k_segments = (n_signal - noverlap) / step;

    double* window = (double*)malloc(nperseg * sizeof(double));
    generate_window(config->window_type, window, nperseg);

    double u_norm = 0.0;
    for (int i = 0; i < nperseg; i++) u_norm += window[i] * window[i];
    u_norm /= nperseg;

    double complex* fft_in = fftw_alloc_complex(nfft);
    double complex* fft_out = fftw_alloc_complex(nfft);
    fftw_plan plan = fftw_plan_dft_1d(nfft, fft_in, fft_out, FFTW_FORWARD, FFTW_ESTIMATE);

    memset(p_out, 0, nfft * sizeof(double));

    for (int k = 0; k < k_segments; k++) {
        int start = k * step;
        
        for (int i = 0; i < nperseg; i++) {
            fft_in[i] = signal[start + i] * window[i];
        }

        fftw_execute(plan);

        for (int i = 0; i < nfft; i++) {
            double mag = cabs(fft_out[i]);
            p_out[i] += (mag * mag);
        }
    }

    double scale = 1.0 / (fs * u_norm * k_segments * nperseg);
    for (int i = 0; i < nfft; i++) p_out[i] *= scale;

    fftshift(p_out, nfft);

    // --- DC SPIKE REMOVAL ---
    // Flatten center 7 bins (indices -3 to +3 relative to DC)
    // Replace them with the average of the neighbors at -4 and +4
    int c = nfft / 2; 
    if (nfft > 8) {
        double neighbor_mean = (p_out[c - 4] + p_out[c + 4]) / 2.0;
        for (int i = -3; i <= 3; i++) {
            p_out[c + i] = neighbor_mean;
        }
    }

    double df = fs / nfft;
    for (int i = 0; i < nfft; i++) {
        f_out[i] = -fs / 2.0 + i * df;
    }

    free(window);
    fftw_destroy_plan(plan);
    fftw_free(fft_in);
    fftw_free(fft_out);
}