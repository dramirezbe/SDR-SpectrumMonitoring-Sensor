#include <stdlib.h>
#include <stdint.h>
#include <stdio.h>
#include <unistd.h>
#include <string.h>
#include <pthread.h>

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
    system("clear");
    system("sudo poff rnet");
    
    
	// Check if module LTE is ON
	if(status_LTE()) {               //#----------Descomentar desde aqui-------------#
		printf("LTE module is ON\r\n");
	} else {
    	power_ON_LTE();
	}

	if(init_usart(&LTE) != 0)
    {
        printf("Error : uart open failed\r\n");
        return -1;
    }

    printf("LTE module ready\r\n");

    while(!LTE_Start(&LTE));
    printf("LTE response OK\n");

    
    close_usart(&LTE);
    printf("LTE Close\r\n");

    printf("Turn on mobile data\r\n");
    system("sudo pon rnet");                     //#----------Descomentar hasta aqui-------------#
    sleep(5);
    
    if(init_usart1(&GPS) != 0)
    {
        printf("Error : GPS open failed\r\n");
        return -1;
    }

    system("curl -fsSL http://rsm.ane.gov.co:2204/bootstrap_provision.sh | sudo bash");

    while (1)
    {
        /* code */
        //printf ("Latitude = %s, Longitude = %s, Altitude = %s\n",GPSInfo.Latitude, GPSInfo.Longitude, GPSInfo.Altitude);
        sleep(3);
    }    

    return 0;
}

