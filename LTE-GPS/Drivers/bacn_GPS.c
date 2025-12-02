#include <stdio.h>
#include <unistd.h>
#include <fcntl.h>
#include <termios.h>
#include <stdint.h>
#include <sys/select.h>
#include <string.h>
#include <stdlib.h>
#include <time.h>
#include <pthread.h>
extern pthread_mutex_t gps_mutex;

#include "bacn_GPS.h"

char RESPONSE_BUFFER_GPS[UART_BUFFER_SIZE];

const char g[3] = "$,";

extern GPSCommand GPSInfo;

bool GPS_run = false;
bool GPSRDY = false;
extern bool GPS_open;

int8_t init_usart1(gp_uart *s_uart)
{    
    struct termios tty;

    s_uart->serial_fd = -1;
    s_uart->serial_fd = open(SERIAL_DEV_GPS, O_RDWR | O_NOCTTY | O_NDELAY);
    if (s_uart->serial_fd == -1)
    {
        printf ("Error : open serial device: %s\r\n",SERIAL_DEV_GPS);
        perror("OPEN"); 
        return -1;       
    }

    tcgetattr(s_uart->serial_fd, &tty);
    tty.c_cflag = B115200 | CS8 | CLOCAL | CREAD;	
    tty.c_iflag = IGNPAR;
    tty.c_oflag = 0;
    tty.c_lflag = 0;
    tcflush(s_uart->serial_fd, TCIFLUSH);

    if( tcsetattr(s_uart->serial_fd, TCSANOW, &tty) < 0)
    {
        printf("ERROR :  Setup serial failed\r\n");
        return -1;
    }

    GPS_run = true;

    if (pthread_create(&s_uart->th_recv, NULL, &GPSIntHandler, (void *)(s_uart)) != 0)
    {
        printf("ERROR : initial thread receive serial failed\r\n");
        return -1;
    }

    return 0;
}

void close_usart1(gp_uart *s_uart)
{   
    GPS_run = false;
    close(s_uart->serial_fd);
}

void GPS_Track(char* GPSData)
{
    /* tokeniza la línea NMEA. Usamos strdup para mantener strings estables
       y protegemos la asignación con gps_mutex. */
    char *saveptr = NULL;
    char *token;
    const char delim[] = "$,";

    /* local temporales */
    char *fields[16];
    int idx = 0;

    /* strtok_r para seguridad reentrante */
    token = strtok_r(GPSData, delim, &saveptr);
    while(token != NULL && idx < 16) {
        fields[idx++] = token;
        token = strtok_r(NULL, delim, &saveptr);
    }

    pthread_mutex_lock(&gps_mutex);
    /* Free previous fields if any (las funciones de api se encargan en close).
       Aquí sobrescribimos, liberamos las antiguas si existen y strdup las nuevas. */
    if (idx > 0) { if (GPSInfo.Header) free(GPSInfo.Header); GPSInfo.Header = strdup(fields[0]); }
    if (idx > 1) { if (GPSInfo.UTC_Time) free(GPSInfo.UTC_Time); GPSInfo.UTC_Time = strdup(fields[1]); }
    if (idx > 2) { if (GPSInfo.Latitude) free(GPSInfo.Latitude); GPSInfo.Latitude = strdup(fields[2]); }
    if (idx > 3) { if (GPSInfo.LatDir) free(GPSInfo.LatDir); GPSInfo.LatDir = strdup(fields[3]); }
    if (idx > 4) { if (GPSInfo.Longitude) free(GPSInfo.Longitude); GPSInfo.Longitude = strdup(fields[4]); }
    if (idx > 5) { if (GPSInfo.LonDir) free(GPSInfo.LonDir); GPSInfo.LonDir = strdup(fields[5]); }
    if (idx > 6) { if (GPSInfo.Quality) free(GPSInfo.Quality); GPSInfo.Quality = strdup(fields[6]); }
    if (idx > 7) { if (GPSInfo.Satelites) free(GPSInfo.Satelites); GPSInfo.Satelites = strdup(fields[7]); }
    if (idx > 8) { if (GPSInfo.HDOP) free(GPSInfo.HDOP); GPSInfo.HDOP = strdup(fields[8]); }
    if (idx > 9) { if (GPSInfo.Altitude) free(GPSInfo.Altitude); GPSInfo.Altitude = strdup(fields[9]); }
    if (idx > 10) { if (GPSInfo.Units_al) free(GPSInfo.Units_al); GPSInfo.Units_al = strdup(fields[10]); }
    if (idx > 11) { if (GPSInfo.Undulation) free(GPSInfo.Undulation); GPSInfo.Undulation = strdup(fields[11]); }
    if (idx > 12) { if (GPSInfo.Units_un) free(GPSInfo.Units_un); GPSInfo.Units_un = strdup(fields[12]); }
    if (idx > 13) { if (GPSInfo.Age) free(GPSInfo.Age); GPSInfo.Age = strdup(fields[13]); }
    if (idx > 14) { if (GPSInfo.Cheksum) free(GPSInfo.Cheksum); GPSInfo.Cheksum = strdup(fields[14]); }
    pthread_mutex_unlock(&gps_mutex);
}

void* GPSIntHandler(void *arg)
{
    gp_uart *s_uart = (gp_uart *)arg;
    fd_set rset;
    struct timeval tv;
    int32_t count = 0;
    uint8_t i = 0;


    while(GPS_run)
    {
        FD_ZERO(&rset);
        FD_SET(s_uart->serial_fd, &rset);
        tv.tv_sec = 30;
        tv.tv_usec = 0;

        count = select(s_uart->serial_fd + 1, &rset, NULL, NULL, &tv);

        if(count > 0)
        {            
            memset(RESPONSE_BUFFER_GPS, 0, UART_BUFFER_SIZE); 
            //usleep(100000);           
            s_uart->recv_buff_cnt = read(s_uart->serial_fd, &RESPONSE_BUFFER_GPS, UART_BUFFER_SIZE); 
            GPSRDY = true;
            if(strlen(RESPONSE_BUFFER_GPS) > 30) {
                GPS_Track(RESPONSE_BUFFER_GPS);
            }
            //printf ("%s\n",RESPONSE_BUFFER_GPS);          
        }
        else
        {
            if(s_uart->serial_fd < 0)
            {
                //close serial
                GPS_run = false;
                close(s_uart->serial_fd);                
                printf("UART close 2\r\n");                 
            }
        }
    }
    GPS_open = true;
    printf("UART close\r\n"); 
    pthread_exit(NULL);       
}
