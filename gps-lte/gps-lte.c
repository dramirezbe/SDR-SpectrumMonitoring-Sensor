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

#include "utils.h"       
#include "bacn_LTE.h"
#include "bacn_GPS.h"
#include "bacn_gpio.h"

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

/** * @name Estado y Datos Globales
 * @{ 
 */
GPSCommand GPSInfo;  /**< Estructura que almacena la última trama de datos GPS procesada (Lat, Lon, Alt). */

bool LTE_open = false; /**< Indica si el puerto serie LTE está abierto. */
bool GPS_open = false; /**< Indica si el puerto serie GPS está abierto. */
bool GPSRDY  = false; /**< Bandera de sincronización; se activa cuando hay una nueva trama GPS lista. */
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
                if(GPSInfo.Latitude != NULL) {
                    status = post_gps_data(api_url, GPSInfo.Altitude, GPSInfo.Latitude, GPSInfo.Longitude);
                }

                if (status == 0) {
                        printf("Success: Data posted to %s\n", api_url);
                } else {
                	fprintf(stderr, "Failed with error code: %d\n", status);
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