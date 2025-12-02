#ifndef BACN_GPS_H
#define BACN_GPS_H

#include <pthread.h>
#include <stdint.h>
#include <stdbool.h>

#define UART_BUFFER_SIZE    120

#define SERIAL_DEV_GPS "/dev/ttyUSB1"

typedef struct
{
    uint32_t serial_fd;
    pthread_t th_recv;

    int32_t recv_buff_cnt;

}gp_uart;

typedef struct GPSCommand
{
    char* Header;
    char* UTC_Time;  
    char* Latitude;    
    char* LatDir;
    char* Longitude;
    char* LonDir;
    char* Quality;
    char* Satelites;
    char* HDOP;
    char* Altitude;
    char* Units_al;
    char* Undulation;
    char* Units_un;
    char* Age;
    char* Cheksum;

} GPSCommand;

/* Commands Functions*/
int8_t init_usart1(gp_uart *s_uart);
void close_usart1(gp_uart *s_uart);
void GPS_Track(char* GPSData);
void* GPSIntHandler(void *arg);

#endif // BACN_GPS_H