#include "parser.h"

/**
 * @brief Helper to map normalized strings to Enum
 */
static PsdWindowType_t resolve_window_enum(const char *window_str_lower) {
    if (!window_str_lower) return HAMMING_TYPE;

    if (strcmp(window_str_lower, "hann") == 0)      return HANN_TYPE;
    if (strcmp(window_str_lower, "rectangular") == 0) return RECTANGULAR_TYPE;
    if (strcmp(window_str_lower, "blackman") == 0)  return BLACKMAN_TYPE;
    if (strcmp(window_str_lower, "hamming") == 0)   return HAMMING_TYPE;
    if (strcmp(window_str_lower, "flattop") == 0)   return FLAT_TOP_TYPE;
    if (strcmp(window_str_lower, "kaiser") == 0)    return KAISER_TYPE;
    if (strcmp(window_str_lower, "tukey") == 0)     return TUKEY_TYPE;
    if (strcmp(window_str_lower, "bartlett") == 0)  return BARTLETT_TYPE;

    return HAMMING_TYPE; // Default
}

static type_filter_t resolve_filter_enum(const char *str) {
    if (!str) return LOWPASS_TYPE;
    if (strcmp(str, "highpass") == 0) return HIGHPASS_TYPE;
    if (strcmp(str, "bandpass") == 0) return BANDPASS_TYPE;
    if (strcmp(str, "bandstop") == 0) return BANDSTOP_TYPE;
    return LOWPASS_TYPE;
}

static demod_type_t resolve_demod_enum(const char *str) {
    if (!str) return DEMOD_OFF;
    if (strcmp(str, "fm") == 0) return DEMOD_FM;
    if (strcmp(str, "am") == 0) return DEMOD_AM;
    return DEMOD_OFF;
}

/**
 * @brief Duplicates a string and converts it to lowercase in one pass.
 * Caller must free the result.
 */
char* strdup_lowercase(const char *str) {
    if (!str) return NULL;
    
    size_t len = strlen(str);
    char *lower_str = (char*)malloc(len + 1);
    if (!lower_str) return NULL;

    for (size_t i = 0; i < len; ++i) {
        lower_str[i] = tolower((unsigned char)str[i]);
    }
    lower_str[len] = '\0';
    return lower_str;
}

void free_desired_psd(DesiredCfg_t *target) {
    if (target) {
        if (target->scale) {
            free(target->scale);
            target->scale = NULL;
        }
    }
}

// =========================================================
// Configuration & Parsing
// =========================================================

int parse_config_rf(const char *json_string, DesiredCfg_t *target) {
    if (json_string == NULL || target == NULL) return -1;

    // Reset target structure safely
    memset(target, 0, sizeof(DesiredCfg_t));
    
    // Set sane defaults
    target->window_type = HAMMING_TYPE;
    target->antenna_port = 1;
    target->rf_mode = REALTIME_MODE;
    target->scale = NULL; // Will be allocated if present

    cJSON *root = cJSON_Parse(json_string);
    if (root == NULL) return -1;

    // 1. RF Mode (Strict Lowercase Parsing)
    cJSON *rf_mode = cJSON_GetObjectItemCaseSensitive(root, "rf_mode");
    if (cJSON_IsString(rf_mode) && rf_mode->valuestring) {
        char *clean_mode = strdup_lowercase(rf_mode->valuestring);
        if (clean_mode) {
            if(strcmp(clean_mode, "realtime") == 0) target->rf_mode = REALTIME_MODE;
            else if(strcmp(clean_mode, "campaign") == 0) target->rf_mode = CAMPAIGN_MODE;
            else if(strcmp(clean_mode, "fm") == 0) target->rf_mode = FM_MODE;
            else if(strcmp(clean_mode, "am") == 0) target->rf_mode = AM_MODE;
            free(clean_mode);
        }
    }

    cJSON *filt_obj = cJSON_GetObjectItemCaseSensitive(root, "filter");
    if (cJSON_IsObject(filt_obj)) {
        target->filter_enabled = true;
        cJSON *f_type = cJSON_GetObjectItemCaseSensitive(filt_obj, "type");
        cJSON *f_bw   = cJSON_GetObjectItemCaseSensitive(filt_obj, "bw_hz");
        cJSON *f_ord  = cJSON_GetObjectItemCaseSensitive(filt_obj, "order");

        if (cJSON_IsString(f_type)) target->filter_cfg.type_filter = resolve_filter_enum(f_type->valuestring);
        if (cJSON_IsNumber(f_bw))   target->filter_cfg.bw_filter_hz = (float)f_bw->valuedouble;
        if (cJSON_IsNumber(f_ord))  target->filter_cfg.order_filter = (int)f_ord->valuedouble;
        target->filter_cfg.sample_rate = target->sample_rate; // Link SR
    }

    cJSON *demod_obj = cJSON_GetObjectItemCaseSensitive(root, "demodulation");
    if (cJSON_IsObject(demod_obj)) {
        target->demod_enabled = true;
        cJSON *d_type = cJSON_GetObjectItemCaseSensitive(demod_obj, "type");
        cJSON *d_bw   = cJSON_GetObjectItemCaseSensitive(demod_obj, "bw_hz");

        if (cJSON_IsString(d_type)) target->demod_type = resolve_demod_enum(d_type->valuestring);
        if (cJSON_IsNumber(d_bw))   target->demod_cfg.bw_hz = d_bw->valuedouble;
        target->demod_cfg.center_freq = 0; // Relative to DC
    }

    cJSON *m_psd = cJSON_GetObjectItemCaseSensitive(root, "method_psd");
    if (cJSON_IsString(m_psd)) {
        if (strcasecmp(m_psd->valuestring, "pfb") == 0) target->method_psd = PFB;
        else target->method_psd = WELCH;
    }

    // 2. Numeric params
    cJSON *cf = cJSON_GetObjectItemCaseSensitive(root, "center_freq_hz");
    if (cJSON_IsNumber(cf)) target->center_freq = (uint64_t)cf->valuedouble;

    cJSON *span = cJSON_GetObjectItemCaseSensitive(root, "span");
    if (cJSON_IsNumber(span)) target->span = span->valuedouble;

    cJSON *sr = cJSON_GetObjectItemCaseSensitive(root, "sample_rate_hz");
    if (cJSON_IsNumber(sr)) target->sample_rate = sr->valuedouble;

    cJSON *rbw = cJSON_GetObjectItemCaseSensitive(root, "rbw_hz");
    if (cJSON_IsNumber(rbw)) target->rbw = (int)rbw->valuedouble;

    cJSON *ov = cJSON_GetObjectItemCaseSensitive(root, "overlap");
    if (cJSON_IsNumber(ov)) target->overlap = ov->valuedouble;

    // 3. Window (Strict Lowercase Parsing)
    cJSON *win = cJSON_GetObjectItemCaseSensitive(root, "window");
    if (cJSON_IsString(win) && win->valuestring) {
        char *clean_win = strdup_lowercase(win->valuestring);
        if (clean_win) {
            target->window_type = resolve_window_enum(clean_win);
            free(clean_win);
        }
    }

    // 4. Scale (Allocated as Lowercase)
    cJSON *sc = cJSON_GetObjectItemCaseSensitive(root, "scale");
    if (cJSON_IsString(sc) && sc->valuestring) {
        // We strictly store it as lowercase as requested
        target->scale = strdup_lowercase(sc->valuestring);
    } else {
        // Default scale if not provided
        target->scale = strdup("dbm");
    }

    // 5. Gains
    cJSON *lna = cJSON_GetObjectItemCaseSensitive(root, "lna_gain");
    if (cJSON_IsNumber(lna)) target->lna_gain = (int)lna->valuedouble;

    cJSON *vga = cJSON_GetObjectItemCaseSensitive(root, "vga_gain");
    if (cJSON_IsNumber(vga)) target->vga_gain = (int)vga->valuedouble;

    // 6. Antenna
    cJSON *amp = cJSON_GetObjectItemCaseSensitive(root, "antenna_amp");
    if (cJSON_IsBool(amp)) target->amp_enabled = cJSON_IsTrue(amp);

    cJSON *port = cJSON_GetObjectItemCaseSensitive(root, "antenna_port");
    if (cJSON_IsNumber(port)) target->antenna_port = (int)port->valuedouble;

    cJSON *ppm = cJSON_GetObjectItemCaseSensitive(root, "ppm_error");
    if (cJSON_IsNumber(ppm)) target->ppm_error = (int)ppm->valuedouble;
    
    // Validation
    if (target->center_freq == 0 && target->sample_rate == 0) {
        cJSON_Delete(root);
        free_desired_psd(target); // Cleanup default scale alloc
        return -1;
    }

    cJSON_Delete(root);
    return 0;
}

void print_config_summary(DesiredCfg_t *des, SDR_cfg_t *hw, PsdConfig_t *psd, RB_cfg_t *rb) {
    double capture_duration = 0.0;
    if (hw->sample_rate > 0) {
        capture_duration = (double)rb->total_bytes / 2.0 / hw->sample_rate;
    }

    printf("\n================ [ CONFIGURATION SUMMARY ] ================\n");
    printf("--- ACQUISITION (Hardware) ---\n");
    printf("Center Freq : %" PRIu64 " Hz\n", hw->center_freq);
    printf("Sample Rate : %.2f MS/s\n", hw->sample_rate / 1e6);
    printf("LNA / VGA   : %d dB / %d dB\n", hw->lna_gain, hw->vga_gain);
    printf("Amp / Port  : %s / %d\n", hw->amp_enabled ? "ON" : "OFF", des->antenna_port);
    printf("Buffer Req  : %zu bytes (~%.4f sec)\n", rb->total_bytes, capture_duration);

    printf("\n--- PSD PROCESS (DSP) ---\n");
    printf("Window Enum : %d\n", psd->window_type);
    printf("FFT Size    : %d bins\n", psd->nperseg);
    printf("Overlap     : %d bins\n", psd->noverlap);
    printf("Scale Unit  : %s\n", des->scale ? des->scale : "dbm");
    printf("===========================================================\n\n");
}

/**
 * @brief Finalized Debug Print for all SDR and DSP parameters.
 */
void print_config_summary_DEBUG(DesiredCfg_t *des, SDR_cfg_t *hw, PsdConfig_t *psd, RB_cfg_t *rb) {
    if (!des || !hw || !psd || !rb) {
        printf("[ERROR] Cannot print summary: NULL pointer detected.\n");
        return;
    }

    double capture_duration = 0.0;
    if (hw->sample_rate > 0) {
        capture_duration = (double)rb->total_bytes / 2.0 / hw->sample_rate;
    }

    // String Mappings for Enums
    const char* window_names[] = {"Hamming", "Hann", "Rectangular", "Blackman", "Flat Top", "Kaiser", "Tukey", "Bartlett"};
    const char* filter_names[] = {"Low-Pass", "High-Pass", "Band-Pass", "Band-Stop"};
    const char* demod_names[]  = {"OFF", "FM", "AM", "USB", "LSB"};
    const char* psd_methods[]  = {"Welch", "PFB"};

    printf("\n"
           "┌──────────────────────────────────────────────────────────┐\n"
           "│                SDR CONFIGURATION SUMMARY                 │\n"
           "└──────────────────────────────────────────────────────────┘\n");

    // 1. HARDWARE SECTION
    printf("--- ACQUISITION (Hardware) ---\n");
    printf("  Center Freq   : %" PRIu64 " Hz\n", hw->center_freq);
    printf("  Sample Rate   : %.2f MS/s\n", hw->sample_rate / 1e6);
    printf("  LNA / VGA     : %d dB / %d dB\n", hw->lna_gain, hw->vga_gain);
    printf("  Amp / Port    : %s / Port %d\n", hw->amp_enabled ? "ENABLED" : "DISABLED", des->antenna_port);
    printf("  PPM Error     : %d\n", des->ppm_error);
    printf("  Buffer Info   : %zu bytes (~%.4f sec)\n", rb->total_bytes, capture_duration);

    // 2. PSD / DSP SECTION
    printf("\n--- SPECTRAL ANALYSIS (PSD) ---\n");
    printf("  Method        : [%s]\n", psd_methods[des->method_psd]);
    printf("  Window Type   : %s\n", window_names[psd->window_type]);
    printf("  FFT Size      : %d bins\n", psd->nperseg);
    printf("  Overlap       : %d bins (%.1f%%)\n", psd->noverlap, des->overlap * 100.0);
    printf("  Target RBW    : %d Hz\n", des->rbw);
    printf("  Scale Unit    : %s\n", des->scale ? des->scale : "dbm");

    // 3. FILTER SECTION
    printf("\n--- PRE-FILTERING ---\n");
    if (des->filter_enabled) {
        printf("  Status        : [ACTIVE]\n");
        printf("  Type          : %s\n", filter_names[des->filter_cfg.type_filter]);
        printf("  Bandwidth     : %.2f kHz\n", des->filter_cfg.bw_filter_hz / 1000.0);
        printf("  Order         : %d\n", des->filter_cfg.order_filter);
    } else {
        printf("  Status        : [BYPASSED]\n");
    }

    // 4. DEMODULATION SECTION
    printf("\n--- DEMODULATION ---\n");
    if (des->demod_enabled) {
        printf("  Status        : [ACTIVE]\n");
        printf("  Mode          : %s\n", demod_names[des->demod_type]);
        printf("  Bandwidth     : %.2f kHz\n", des->demod_cfg.bw_hz / 1000.0);
    } else {
        printf("  Status        : [OFF]\n");
    }

    printf("────────────────────────────────────────────────────────────\n\n");
}