#include <stdlib.h>
#include <stdint.h>
#include <stdio.h>
#include <unistd.h>
#include <string.h>

#include "Drivers/bacn_gpio.h"
#include "Drivers/bacn_LTE.h"
#include "Drivers/bacn_GPS.h"

st_uart LTE;
gp_uart GPS;

GPSCommand GPSInfo;

bool LTE_open = false;
bool GPS_open = false;

int main(void)
{
    select_ANTENNA(1);

    // Check if module LTE is ON
	if(status_LTE()) {               //#----------Descomentar desde aqui-------------#
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

    while (1)
    {
        /* code */
        printf ("Latitude = %s, Longitude = %s, Altitude = %s\n",GPSInfo.Latitude, GPSInfo.Longitude, GPSInfo.Altitude);
        sleep(3);
    }    

    return 0;
}

