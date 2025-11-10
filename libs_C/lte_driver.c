/**
 * @file lte_driver.c
 * @brief LTE driver, function to choose antenna (0,1,2,3), and function to get gps lat,lng and alt.
*/

//gcc -Wall -O2 -shared -fPIC lte_driver.c -o lte_driver.so

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

//------------------------------------------------------------------------------
// Simulated internal state
//------------------------------------------------------------------------------
static int lte_power = 0;
static int current_antenna = 0;

//------------------------------------------------------------------------------
// Dummy functions
//------------------------------------------------------------------------------

void LTE_on(void)
{
    if (!lte_power) {
        lte_power = 1;
        printf("[LTE_so] Power ON simulated.\n");
    } else {
        printf("[LTE_so] Already ON.\n");
    }
}

void LTE_off(void)
{
    if (lte_power) {
        lte_power = 0;
        printf("[LTE_so] Power OFF simulated.\n");
    } else {
        printf("[LTE_so] Already OFF.\n");
    }
}

int switch_antenna(int ant_num)
{
    if (ant_num < 0 || ant_num > 3) {
        printf("[LTE_so] Invalid antenna number: %d\n", ant_num);
        return -1;
    }
    current_antenna = ant_num;
    printf("[LTE_so] Switched to antenna #%d\n", ant_num);
    return 0;
}

/**
 * @brief Dummy GPS output in NMEA format with random coordinates.
 * @return Pointer to static string (no malloc needed).
 */
const char* get_gps(void)
{
    static char buffer[128];
    time_t t = time(NULL);
    struct tm *utc = gmtime(&t);

    // Initialize random seed once per program run
    static int seeded = 0;
    if (!seeded) {
        srand((unsigned int)time(NULL));
        seeded = 1;
    }

    // Bogotá approx: 4.6N, -74.07W, altitude ~2600m
    double lat_deg = 4.60 + ((rand() % 1001) - 500) / 10000.0;   // ±0.05°
    double lon_deg = -74.07 + ((rand() % 1001) - 500) / 10000.0; // ±0.05°
    double alt_m   = 2600.0 + ((rand() % 101) - 50);             // ±50 m

    // Convert decimal degrees to NMEA "ddmm.mmmm" format
    int lat_d = (int)lat_deg;
    double lat_m_frac = (lat_deg - lat_d) * 60.0;
    int lon_d = (int)(-lon_deg); // west longitude
    double lon_m_frac = (-lon_deg - lon_d) * 60.0;

    // Build NMEA GPGGA sentence
    snprintf(buffer, sizeof(buffer),
             "$GPGGA,%02d%02d%02d.00,%02d%07.4f,N,%03d%07.4f,W,1,08,1.0,%.1f,M,0.0,M,,*47",
             utc->tm_hour, utc->tm_min, utc->tm_sec,
             lat_d, lat_m_frac, lon_d, lon_m_frac, alt_m);

    return buffer;
}
