// libs/parser.c
#include "parser.h"

/**
 * @brief Helper to map normalized strings to Enum
 */
static PsdWindowType_t resolve_window_enum(const char *window_str_lower) {
    if (!window_str_lower) return HAMMING_TYPE;

    if (strcmp(window_str_lower, "hann") == 0)        return HANN_TYPE;
    if (strcmp(window_str_lower, "rectangular") == 0)   return RECTANGULAR_TYPE;
    if (strcmp(window_str_lower, "blackman") == 0)    return BLACKMAN_TYPE;
    if (strcmp(window_str_lower, "hamming") == 0)     return HAMMING_TYPE;
    if (strcmp(window_str_lower, "flattop") == 0)     return FLAT_TOP_TYPE;
    if (strcmp(window_str_lower, "kaiser") == 0)      return KAISER_TYPE;
    if (strcmp(window_str_lower, "tukey") == 0)       return TUKEY_TYPE;
    if (strcmp(window_str_lower, "bartlett") == 0)    return BARTLETT_TYPE;

    return HAMMING_TYPE; 
}

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

// =========================================================
// Configuration & Parsing
// =========================================================

// Helper to set specific default values as requested
static void set_default_config(DesiredCfg_t *target) {
    if (!target) return;

    // Core Settings
    target->rf_mode        = PSD_MODE;      // Default: PSD
    target->method_psd     = WELCH;         // Default: Welch
    
    // Hardware Settings
    target->center_freq    = 98000000ULL;   // Default: 98 MHz
    target->sample_rate    = 8000000.0;     // Default: 8 MHz
    target->lna_gain       = 0;
    target->vga_gain       = 0;
    target->amp_enabled    = true;          // Default: true
    target->antenna_port   = 1;             // Default: 1
    target->ppm_error      = 0;

    // PSD Settings
    target->rbw            = 100000;        // Default: 100 kHz
    target->overlap        = 0.5;           // Standard 50% default
    target->window_type    = HAMMING_TYPE;  // Default: 0

    // Filter Settings
    target->filter_enabled = false;         // Default: Filter NULL/Off
    target->filter_cfg.start_freq_hz = 0;
    target->filter_cfg.end_freq_hz   = 0;
}

int parse_config_rf(const char *json_string, DesiredCfg_t *target) {
    if (json_string == NULL || target == NULL) return -1;

    // 1. Initialize with hardcoded defaults
    set_default_config(target);
    
    cJSON *root = cJSON_Parse(json_string);
    if (root == NULL) {
        // Log error but keep defaults
        printf("[PARSER] Warning: JSON is NULL or invalid. Using defaults.\n");
        return 0; 
    } 

    // 2. Parse Core Hardware Parameters FIRST (Required for clamping logic)
    cJSON *cf = cJSON_GetObjectItemCaseSensitive(root, "center_freq_hz");
    if (cJSON_IsNumber(cf)) target->center_freq = (uint64_t)cf->valuedouble;

    cJSON *sr = cJSON_GetObjectItemCaseSensitive(root, "sample_rate_hz");
    if (cJSON_IsNumber(sr)) target->sample_rate = sr->valuedouble;

    // 3. Filter logic with Boundary Validation (Clamping)
    cJSON *filt_obj = cJSON_GetObjectItemCaseSensitive(root, "filter");
    if (cJSON_IsObject(filt_obj)) {
        target->filter_enabled = true;
        
        cJSON *start = cJSON_GetObjectItemCaseSensitive(filt_obj, "start_freq_hz");
        cJSON *end   = cJSON_GetObjectItemCaseSensitive(filt_obj, "end_freq_hz");

        // Temporary storage for requested values
        int req_start = cJSON_IsNumber(start) ? (int)start->valuedouble : 0;
        int req_end   = cJSON_IsNumber(end)   ? (int)end->valuedouble : 0;

        // Calculate Nyquist Boundaries [Fc - Fs/2, Fc + Fs/2]
        double lower_bound = (double)target->center_freq - (target->sample_rate / 2.0);
        double upper_bound = (double)target->center_freq + (target->sample_rate / 2.0);

        // --- CLAMPING LOGIC ---
        // Force start frequency to be no lower than the hardware floor
        target->filter_cfg.start_freq_hz = (req_start < lower_bound) ? (int)lower_bound : req_start;

        // Force end frequency to be no higher than the hardware ceiling
        target->filter_cfg.end_freq_hz = (req_end > upper_bound) ? (int)upper_bound : req_end;
        
        // Safety: Ensure end is not less than start after clamping
        if (target->filter_cfg.end_freq_hz < target->filter_cfg.start_freq_hz) {
             target->filter_cfg.end_freq_hz = target->filter_cfg.start_freq_hz;
        }
    }

    // 4. Engine Mode / Demodulation
    cJSON *demod = cJSON_GetObjectItemCaseSensitive(root, "demodulation");
    if (cJSON_IsString(demod) && demod->valuestring) {
        if (strcasecmp(demod->valuestring, "fm") == 0)      target->rf_mode = FM_MODE;
        else if (strcasecmp(demod->valuestring, "am") == 0) target->rf_mode = AM_MODE;
        else target->rf_mode = PSD_MODE;
    }

    // 5. PSD & Windowing
    cJSON *m_psd = cJSON_GetObjectItemCaseSensitive(root, "method_psd");
    if (cJSON_IsString(m_psd)) {
        target->method_psd = (strcasecmp(m_psd->valuestring, "pfb") == 0) ? PFB : WELCH;
    }

    cJSON *rbw = cJSON_GetObjectItemCaseSensitive(root, "rbw_hz");
    if (cJSON_IsNumber(rbw)) target->rbw = (int)rbw->valuedouble;

    cJSON *ov = cJSON_GetObjectItemCaseSensitive(root, "overlap");
    if (cJSON_IsNumber(ov)) target->overlap = ov->valuedouble;

    cJSON *win = cJSON_GetObjectItemCaseSensitive(root, "window");
    if (cJSON_IsString(win)) {
        char *clean_win = strdup_lowercase(win->valuestring);
        target->window_type = resolve_window_enum(clean_win);
        free(clean_win);
    }

    // 6. Hardware Gains & Peripheral Settings
    cJSON *lna = cJSON_GetObjectItemCaseSensitive(root, "lna_gain");
    if (cJSON_IsNumber(lna)) target->lna_gain = (int)lna->valuedouble;

    cJSON *vga = cJSON_GetObjectItemCaseSensitive(root, "vga_gain");
    if (cJSON_IsNumber(vga)) target->vga_gain = (int)vga->valuedouble;

    cJSON *amp = cJSON_GetObjectItemCaseSensitive(root, "antenna_amp");
    if (cJSON_IsBool(amp)) target->amp_enabled = cJSON_IsTrue(amp);

    cJSON *port = cJSON_GetObjectItemCaseSensitive(root, "antenna_port");
    if (cJSON_IsNumber(port)) target->antenna_port = (int)port->valuedouble;

    cJSON *ppm = cJSON_GetObjectItemCaseSensitive(root, "ppm_error");
    if (cJSON_IsNumber(ppm)) target->ppm_error = (int)ppm->valuedouble;

    cJSON_Delete(root);
    return 0;
}

void print_config_summary_DEBUG(DesiredCfg_t *des, SDR_cfg_t *hw, PsdConfig_t *psd, RB_cfg_t *rb) {
    if (!des || !hw || !psd || !rb) return;

    const char* window_names[] = {"Hamming", "Hann", "Rectangular", "Blackman", "Flat Top", "Kaiser", "Tukey", "Bartlett"};
    const char* mode_names[]   = {"PSD (No Demod)", "FM Demodulation", "AM Demodulation"};
    const char* psd_methods[]  = {"Welch", "PFB"};

    printf("\n"
           "┌──────────────────────────────────────────────────────────┐\n"
           "│                RF ENGINE SYSTEM CONFIG                   │\n"
           "└──────────────────────────────────────────────────────────┘\n");

    printf("--- CORE MODE ---\n");
    printf("  Engine Mode   : %s\n", mode_names[des->rf_mode]);

    printf("\n--- HARDWARE (SDR) ---\n");
    printf("  Center Freq   : %" PRIu64 " Hz\n", hw->center_freq);
    printf("  Sample Rate   : %.2f MS/s\n", hw->sample_rate / 1e6);
    printf("  Gains (L/V)   : %d dB / %d dB\n", hw->lna_gain, hw->vga_gain);
    printf("  Antenna       : Port %d (Amp: %s)\n", des->antenna_port, des->amp_enabled ? "ON" : "OFF");
    printf("  PPM Error     : %d\n", des->ppm_error);

    printf("\n--- SPECTRAL (PSD) ---\n");
    printf("  Method        : %s\n", psd_methods[des->method_psd]);
    printf("  Window        : %s\n", window_names[psd->window_type]);
    printf("  RBW           : %d Hz\n", des->rbw);
    printf("  Overlap       : %.1f%%\n", des->overlap * 100.0);

    printf("\n--- FILTERING ---\n");
    if (des->filter_enabled) {
        printf("  Status        : [ACTIVE]\n");
        printf("  Range         : %d Hz -> %d Hz\n", 
                des->filter_cfg.start_freq_hz, 
                des->filter_cfg.end_freq_hz);
    } else {
        printf("  Status        : [BYPASSED]\n");
    }
    printf("────────────────────────────────────────────────────────────\n\n");
}

void print_config_summary_DEPLOY(DesiredCfg_t *des, SDR_cfg_t *hw, PsdConfig_t *psd, RB_cfg_t *rb) {
    if (!des || !hw || !psd || !rb) return;

    // Compact Lookup Tables
    const char* m_n[] = {"PSD", "FM", "AM"};
    const char* p_m[] = {"WCH", "PFB"};
    const char* w_n[] = {"HMNG", "HANN", "RECT", "BLCK", "FTOP", "KSR", "TUKY", "BRTL"};

    // Line 1: Hardware, Gain, and FFT Resolution
    // Format: [CFG] MODE | FREQ (MHz) | SAMPLE RATE | GAIN | AMP | PSD POINTS
    printf("[CFG] %s | %" PRIu64 "Hz (%.2fM) | FS:%.1fM | G:%d/%d | AMP:%c | PTS:%d\n",
           m_n[des->rf_mode % 3], 
           hw->center_freq, (double)hw->center_freq / 1e6,
           hw->sample_rate / 1e6,
           hw->lna_gain, hw->vga_gain,
           des->amp_enabled ? 'Y' : 'N',
           psd->nperseg);

    // Line 2: DSP, Windowing, Buffer, and Filter Range
    // Format: Method | RBW | Overlap | Window Name | Buffer Size | Filter Range
    printf("      %s | RBW:%d | OVP:%.0f%% | WIN:%s | BUF:%zuMB",
           p_m[des->method_psd % 2],
           des->rbw,
           des->overlap * 100.0,
           w_n[psd->window_type % 8],
           rb->total_bytes / (1024 * 1024));

    if (des->filter_enabled) {
        printf(" | FILT:%d-%dHz\n", 
               des->filter_cfg.start_freq_hz, 
               des->filter_cfg.end_freq_hz);
    } else {
        printf(" | FILT:OFF\n");
    }
}