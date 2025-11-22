// main.c CORREGIDO

#define _POSIX_C_SOURCE 200809L
#include <stdlib.h>
#include <stdint.h>
#include <stdio.h>
#include <unistd.h>
#include <string.h>
#include <limits.h>
#include <errno.h>
#include <sys/inotify.h>
#include <stdbool.h>

#include "Drivers/utils.h"

// Drivers
#include "Drivers/cJSON.h"
#include "Drivers/bacn_gpio.h"
#include "Drivers/bacn_LTE.h"
#include "Drivers/bacn_GPS.h"

st_uart LTE;
gp_uart GPS;
GPSCommand GPSInfo;
bool LTE_open = false;
bool GPS_open = false;

// --- Callback (Igual que antes) ---
void handle_config_update(const char *watch_path, const char *filename, uint32_t mask, void *user_data) {
    if (filename == NULL || strcmp(filename, "persistent.json") != 0) return;

    char full_path[PATH_MAX];
    if (path_join(watch_path, filename, full_path, sizeof(full_path)) != 0) return;

    printf("\n📢 Detectado cambio en: %s\n", filename);

    char* json_text = read_file_to_string(full_path);
    if (!json_text) return;

    cJSON* root = cJSON_Parse(json_text);
    free(json_text);

    if (!root) {
        printf("Error parseando JSON\n");
        return;
    }

    cJSON* antenna = cJSON_GetObjectItem(root, "antenna_port");
    if (cJSON_IsNumber(antenna)) {
        int num_antenna = antenna->valueint;
        printf("🔧 Configuración aplicada: Puerto de antena = %d\n", num_antenna);
        select_ANTENNA(num_antenna);
    }
    cJSON_Delete(root);
}

// --- Inicialización de Módulos (CORREGIDA) ---
int initialize_modules() {
    printf("--- Inicializando Módulos ---\n");

    // 1. Inicializar UART LTE
    if (init_usart(&LTE) != 0) {
        printf("Error: Fallo al abrir UART LTE\n");
        return -1; 
        // NOTA: Si quieres que el programa siga aunque falle el LTE, cambia esto a return 0;
    }

    if (status_LTE()) {
        printf("El módulo LTE ya estaba encendido.\n");
    } else {
        printf("Encendiendo módulo LTE...\n");
        power_ON_LTE();
    }
    
    printf("Esperando respuesta del LTE (Timeout de 10 segundos)...\n");
    
    // --- CORRECCIÓN DE BLOQUEO ---
    int attempts = 0;
    int max_attempts = 100; // 100 intentos * 100ms = 10 segundos aprox
    bool lte_ready = false;

    while (attempts < max_attempts) {
        if (LTE_Start(&LTE)) {
            lte_ready = true;
            break;
        }
        usleep(100000); // Esperar 100ms entre intentos
        attempts++;
        if (attempts % 10 == 0) printf("."); // Feedback visual
        fflush(stdout);
    }
    printf("\n");

    if (lte_ready) {
        printf("✅ LTE iniciado correctamente.\n");
        LTE_open = true;
    } else {
        printf("⚠️ ADVERTENCIA: El LTE no respondió. El programa continuará sin LTE.\n");
        // No hacemos return -1 para permitir que inotify funcione aunque el LTE falle
    }

    // 2. GPS Initialization
    if (init_usart1(&GPS) != 0) {
        printf("Error: Fallo al abrir GPS\n");
        return -1;
    }
    GPS_open = true;
    printf("✅ GPS iniciado.\n");

    return 0;
}

// --- Bucle Principal (main) ---

int main(void) {
    int ret = 0;
    paths_t paths; 
    InotifyManager manager;

    // 1. Rutas
    if (fill_paths(&paths) != 0) return 1;
    printf("Directorio raíz: %s\n", paths.project_root);

    // 2. Inotify Init
    if (inotify_manager_init(&manager) != 0) return 1;

    // 3. Watch
    if (inotify_manager_add_watch(
            &manager,
            paths.project_root,
            IN_CLOSE_WRITE | IN_MOVED_TO, // Detecta escritura normal y atómica
            handle_config_update,
            NULL
        ) != 0) {
        inotify_manager_cleanup(&manager);
        return 1;
    }

    // Carga inicial
    handle_config_update(paths.project_root, "persistent.json", 0, NULL);

    // 4. Módulos (Ahora con timeout para no colgar el programa)
    //initialize_modules(); 

    printf("🚀 Sistema corriendo. Esperando cambios en JSON o datos GPS...\n");

    // 5. Bucle Principal
    while (1)
    {
        // A. Procesa eventos de archivo (CRÍTICO: Esto debe correr rápido)
        if (inotify_manager_process_events(&manager) < 0) {
            fprintf(stderr, "Error en inotify.\n");
            break;
        }

        //Implement GPS


        usleep(100000);
    }

    inotify_manager_cleanup(&manager);
    return ret;
}