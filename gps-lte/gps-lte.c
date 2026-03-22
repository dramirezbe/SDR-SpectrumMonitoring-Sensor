/**
 * @file gps-lte.c
 * @brief Manejador de GPS-LTE. Interfaz de control para el módulo LTE y selección de antenas. 
 *
 * @details Este módulo gestiona el ciclo de vida de la conexión celular y la adquisición de coordenadas
 * geográficas. Se encarga de la inicialización del hardware LTE (vía comandos AT/UART), la 
 * gestión de la interfaz de red PPP (Point-to-Point Protocol) y el reporte periódico (cada 10s) 
 * de la telemetría GPS hacia una API REST externa. Además, incluye lógica de redundancia para 
 * verificar la conectividad mediante ICMP (ping) y reiniciar la interfaz en caso de fallos críticos.
 *
 * @author GCPDS
 * @date 2026
 */

#ifndef _GNU_SOURCE
/** @brief Habilita extensiones GNU para funciones de cadenas y sistema. */
#define _GNU_SOURCE 
#endif

#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <stdbool.h>
#include <math.h>
#include <string.h>
#include <ctype.h>

#include "utils.h"       
#include "bacn_LTE.h"
#include "bacn_GPS.h"
#include "bacn_gpio.h"

void run_cmd(const char *cmd);
int get_ppp_ip(char *ip);

static bool parse_double_strict(const char *text, double *out) {
    if (text == NULL || out == NULL) {
        return false;
    }

    while (isspace((unsigned char)*text)) {
        text++;
    }

    if (*text == '\0') {
        return false;
    }

    char *endptr = NULL;
    double val = strtod(text, &endptr);
    if (endptr == text || !isfinite(val)) {
        return false;
    }

    while (endptr != NULL && isspace((unsigned char)*endptr)) {
        endptr++;
    }

    if (endptr != NULL && *endptr != '\0') {
        return false;
    }

    *out = val;
    return true;
}

static double nmea_to_decimal_local(double raw_coord) {
    double degrees = floor(raw_coord / 100.0);
    double minutes = raw_coord - (degrees * 100.0);
    return degrees + (minutes / 60.0);
}

static bool gps_to_decimal(const char *coord_str, const char *dir_str, bool is_longitude, double *out) {
    if (!parse_double_strict(coord_str, out)) {
        return false;
    }

    double val = *out;
    if (fabs(val) > 180.0) {
        val = nmea_to_decimal_local(fabs(val));
    }

    if (dir_str != NULL && dir_str[0] != '\0') {
        char dir = (char)toupper((unsigned char)dir_str[0]);
        if (dir == 'S' || dir == 'W') {
            val = -fabs(val);
        } else if (dir == 'N' || dir == 'E') {
            val = fabs(val);
        }
    } else if (is_longitude && val > 0.0) {
        val = -val;
    }

    *out = val;
    return true;
}

static double haversine_m(double lat1, double lon1, double lat2, double lon2) {
    const double earth_radius_m = 6371008.8;
    double lat1_rad = lat1 * M_PI / 180.0;
    double lon1_rad = lon1 * M_PI / 180.0;
    double lat2_rad = lat2 * M_PI / 180.0;
    double lon2_rad = lon2 * M_PI / 180.0;

    double dlat = lat2_rad - lat1_rad;
    double dlon = lon2_rad - lon1_rad;
    double a = sin(dlat / 2.0) * sin(dlat / 2.0) +
               cos(lat1_rad) * cos(lat2_rad) * sin(dlon / 2.0) * sin(dlon / 2.0);
    double c = 2.0 * atan2(sqrt(a), sqrt(1.0 - a));
    return earth_radius_m * c;
}

static bool has_nonzero_coordinate_rounded(const char *coord_str) {
    if (coord_str == NULL || coord_str[0] == '\0') {
        return false;
    }

    while (isspace((unsigned char)*coord_str)) {
        coord_str++;
    }

    if (*coord_str == '\0') {
        return false;
    }

    char *endptr = NULL;
    double val = strtod(coord_str, &endptr);
    if (endptr == coord_str) {
        return false;
    }

    while (endptr && isspace((unsigned char)*endptr)) {
        endptr++;
    }

    if (endptr != NULL && *endptr != '\0') {
        return false;
    }

    if (!isfinite(val)) {
        return false;
    }

    long rounded = lround(val);
    return rounded != 0;
}


/**
 * @defgroup gps_binary GPS-LTE Binary
 * @brief Logic and helper functions for the GPS-LTE module.
 * @{
 */

/** * @name Constantes de Buffer
 * @{ 
 */
#define CMD_BUF 256  /**< Tamaño máximo para comandos de sistema. */
#define IP_BUF 64    /**< Tamaño del buffer para almacenar direcciones IPv4. */
/** @} */

/** * @name Manejadores de Hardware y UART
 * @{ 
 */
st_uart LTE;         /**< Estructura de control para la UART vinculada al módem LTE. */
gp_uart GPS;         /**< Estructura de control para la UART vinculada al receptor GPS. */
/** @} */

/** @name Estado y Datos Globales
 * @{ 
 */
/** @cond DOXYGEN_SHOULD_SKIP_THIS */
GPSCommand GPSInfo;  
/** @endcond */

bool LTE_open = false; /**< Indica si el puerto serie LTE está abierto. */
bool GPS_open = false; /**< Indica si el puerto serie GPS está abierto. */
bool GPSRDY  = false;  /**< Bandera de sincronización para nueva trama GPS. */
/** @} */

/**
 * @brief Gestiona la conexión a la red de datos mediante el demonio PPP.
 * @details Ejecuta el script de marcado 'rnet'. Si la asignación de IP falla, intenta 
 * reiniciar la interfaz una vez tras un tiempo de espera. Utiliza @ref get_ppp_ip para 
 * validar el éxito de la operación.
 */
void connection_LTE(void)
{
    char ip[IP_BUF];

    // 2. Network / Internet Setup    
    run_cmd("sudo pon rnet");
    sleep(15);
    
    if(!get_ppp_ip(ip)) {
        printf("No IP address assigned! Restarting PPP...\n");
        run_cmd("sudo poff rnet");
        sleep(5);
        run_cmd("sudo pon rnet");
        sleep(15);

        if(!get_ppp_ip(ip)) {
            printf("PPP failed again. No IP assigned.\n");
        }
    }
    if(strlen(ip) > 0) {
        printf("PPP connected. IP = %s\n", ip);
    }
}

/**
 * @brief Ejecuta un comando de sistema e imprime su traza en consola.
 * @param[in] cmd Cadena de caracteres con el comando a ejecutar.
 */
void run_cmd(const char *cmd) {
    printf("[CMD] %s\n", cmd);
    system(cmd);
}

/**
 * @brief Obtiene la dirección IPv4 de la interfaz wlan0 (WiFi).
 * @param[out] ip Buffer donde se copiará la dirección IP encontrada.
 * @return 1 si se obtuvo con éxito, 0 en caso contrario.
 */
int get_wlan_ip(char *ip) {
    FILE *fp;
    char cmd[] = "ip -o -4 addr show wlan0 | awk '{print $4}' | cut -d/ -f1";
    char buffer[IP_BUF] = {0};

    fp = popen(cmd, "r");
    if (!fp) return 0;

    if (fgets(buffer, sizeof(buffer), fp) != NULL) {
        buffer[strcspn(buffer, "\n")] = 0;
        if (strlen(buffer) > 0) {
            strcpy(ip, buffer);
            pclose(fp);
            return 1;
        }
    }
    pclose(fp);
    return 0;
}

/**
 * @brief Obtiene la dirección IPv4 de la interfaz eth0 (Ethernet).
 * @param[out] ip Buffer donde se copiará la dirección IP encontrada.
 * @return 1 si se obtuvo con éxito, 0 en caso contrario.
 */
int get_eth_ip(char *ip) {
    FILE *fp;
    char cmd[] = "ip -o -4 addr show eth0 | awk '{print $4}' | cut -d/ -f1";
    char buffer[IP_BUF] = {0};

    fp = popen(cmd, "r");
    if (!fp) return 0;

    if (fgets(buffer, sizeof(buffer), fp) != NULL) {
        buffer[strcspn(buffer, "\n")] = 0;
        if (strlen(buffer) > 0) {
            strcpy(ip, buffer);
            pclose(fp);
            return 1;
        }
    }
    pclose(fp);
    return 0;
}

/**
 * @brief Obtiene la dirección IPv4 de la interfaz ppp0 (Módem LTE).
 * @param[out] ip Buffer donde se copiará la dirección IP encontrada.
 * @return 1 si se obtuvo con éxito, 0 en caso contrario.
 */
int get_ppp_ip(char *ip) {
    FILE *fp;
    char cmd[] = "ip -o -4 addr show ppp0 | awk '{print $4}' | cut -d/ -f1";
    char buffer[IP_BUF] = {0};

    fp = popen(cmd, "r");
    if (!fp) return 0;

    if (fgets(buffer, sizeof(buffer), fp) != NULL) {
        buffer[strcspn(buffer, "\n")] = 0;
        if (strlen(buffer) > 0) {
            strcpy(ip, buffer);
            pclose(fp);
            return 1;
        }
    }
    pclose(fp);
    return 0;
}

/** @} */

/**
 * @brief Punto de entrada principal para el servicio de geolocalización.
 * @details El flujo de ejecución es el siguiente:
 * 1. **Inicialización**: Verifica el estado del módulo LTE, activa la alimentación si es necesario e inicializa las UARTs.
 * 2. **Conexión**: Levanta la interfaz PPP mediante @ref connection_LTE.
 * 3. **Bucle de Telemetría**:
 * - Espera a que la bandera @ref GPSRDY sea verdadera.
 * - Cada 10 actualizaciones de GPS, envía Latitud, Longitud y Altitud a la API mediante un HTTP POST.
 * - Realiza una prueba de conectividad (ping) a una IP de referencia.
 * - Si el ping falla repetidamente (6 intentos), reinicia la conexión PPP para intentar recuperar el servicio.
 * * @return 0 en terminación normal, -1 en caso de error de apertura de hardware.
 */
int main(void)
{
    char *api_url = getenv_c_gps("API_URL"); 
    const char *ip_address = "10.10.1.254";
    char ping_cmd[100];
    int count = 0;
    int tryRB = 0;
    int status = 0;
    int ping_result = 0;

    system("clear");
    system("sudo poff rnet");

    // Check if module LTE is ON
	if(status_LTE()) {               
		printf("LTE module is ON\r\n");
	} else {
    	power_ON_LTE();
	}

    if(init_usart(&LTE) != 0)
    {
        printf("Error : LTE open failed\r\n");
        return -1;
    }

    printf("LTE module ready\r\n");

    while(!LTE_Start(&LTE));
    printf("LTE response OK\n");

    if(init_usart1(&GPS) != 0)
    {
        printf("Error : GPS open failed\r\n");
        return -1;
    }

    close_usart(&LTE);

    // 2. Network / Internet Setup    
    connection_LTE();

    // 3. Environment Setup    
    if (api_url == NULL) {
        printf("WARN: API_URL not set. Data sending will be skipped.\n");
    } else {
        printf("API URL found: %s\n", api_url);
    }

    // 4. Main Loop Variables    
    snprintf(ping_cmd, sizeof(ping_cmd), "ping -c 1 -W 1 %s", ip_address);
    
    while (1)
    {
        // Assume GPSRDY is set by an Interrupt Service Routine (ISR) or separate RX handler
        if(GPSRDY) {
            GPSRDY = false;
            count++;

            // Trigger every 10 GPS updates
            if(count >= 10) { 
                count = 0; // Reset counter
		        printf("Latitude: %s, Longitude: %s, Altitude: %s\n", GPSInfo.Latitude, GPSInfo.Longitude, GPSInfo.Altitude);
                // --- A. SEND DATA ---
                status = -1;
                if (GPSInfo.Latitude != NULL &&
                    GPSInfo.Longitude != NULL &&
                    has_nonzero_coordinate_rounded(GPSInfo.Latitude) &&
                    has_nonzero_coordinate_rounded(GPSInfo.Longitude)) {
                    status = post_gps_data(api_url, GPSInfo.Altitude, GPSInfo.Latitude, GPSInfo.Longitude);
                } else {
                    fprintf(stderr, "Skipping GPS POST: invalid/null coordinates\n");
                }

                if (status == 0) {
                    printf("Success: Data posted to %s\n", api_url);

                    // --- HISTÉRESIS (200 m) usando last_lat/last_lng ---
                    double cur_lat = 0.0;
                    double cur_lng = 0.0;
                    bool cur_ok = gps_to_decimal(GPSInfo.Latitude, GPSInfo.LatDir, false, &cur_lat) &&
                                  gps_to_decimal(GPSInfo.Longitude, GPSInfo.LonDir, true, &cur_lng);

                    if (!cur_ok) {
                        fprintf(stderr, "Skipping shared-memory GPS update: invalid current coordinates\n");
                        shm_add_to_persistent_gps("changed_gps", "false");
                    } else {
                        char *last_lat_str = shm_consult_persistent_gps("last_lat");
                        char *last_lng_str = shm_consult_persistent_gps("last_lng");

                        double last_lat = 0.0;
                        double last_lng = 0.0;
                        bool has_last = parse_double_strict(last_lat_str, &last_lat) &&
                                        parse_double_strict(last_lng_str, &last_lng);

                        bool should_update = true;
                        if (has_last) {
                            double distance_m = haversine_m(last_lat, last_lng, cur_lat, cur_lng);
                            should_update = (distance_m > 200.0);
                            printf("GPS hysteresis distance: %.2f m (threshold: 200.00 m)\n", distance_m);
                        }

                        if (should_update) {
                            char lat_text[32];
                            char lng_text[32];
                            snprintf(lat_text, sizeof(lat_text), "%.7f", cur_lat);
                            snprintf(lng_text, sizeof(lng_text), "%.7f", cur_lng);

                            shm_add_to_persistent_gps("last_lat", lat_text);
                            shm_add_to_persistent_gps("last_lng", lng_text);
                            
                            shm_add_to_persistent_gps("changed_gps", "true");
                        } else {
                            shm_add_to_persistent_gps("changed_gps", "false");
                        }

                        free(last_lat_str); // free(NULL) es seguro en C
                        free(last_lng_str);
                    }
                } else {
                    fprintf(stderr, "Failed gps POST with error code: %d\n", status);
                }

                // --- B. CHECK CONNECTIVITY ---
                // We run the ping command HERE to get the current status
                ping_result = system(ping_cmd); 

                if (ping_result == 0) {
                    // Success (0 return code)
                    printf("Ping to %s successful.\n", ip_address);
                    tryRB = 0;
                } else {
                    // Failure
                    printf("Ping to %s failed. Retry count: %d\n", ip_address, tryRB + 1);
                    tryRB++;
                    
                    if(tryRB >= 6) {
                        tryRB = 0;
                        printf("CRITICAL: Network down for too long. Rebooting...\n");
                        system("sudo poff rnet");
                        sleep(15);
                        connection_LTE();
                    }
                }
            } 
        }
        
        // Slight delay to prevent 100% CPU usage if GPSRDY is polling based
        sleep(1); 
    }    

    return 0;
}