/**
 * @file bacn_GPS.c
 * @brief Implementación del manejador de datos GPS e hilo de recepción.
 */

#include "bacn_GPS.h"

/**
 * @addtogroup bacn_gps_module
 * @{
 */

/** @brief Buffer global donde el hilo deposita los datos crudos del GPS. */
char RESPONSE_BUFFER_GPS[UART_BUFFER_SIZE];
/** @brief Delimitadores estándar para tramas NMEA (Coma y símbolo de inicio). */
const char NMEA_DELIMITERS[3] = "$,";

/** @cond DOXYGEN_SHOULD_SKIP_THIS */
extern GPSCommand GPSInfo;
extern bool GPSRDY;
extern bool GPS_open;
/** @endcond */

bool GPS_run = false;

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
    // Usamos el nuevo nombre de la variable de delimitación
    char *token = strtok(GPSData, NMEA_DELIMITERS);

    if (token != NULL) GPSInfo.Header = token;
    token = strtok(NULL, NMEA_DELIMITERS);
    if (token != NULL) GPSInfo.UTC_Time = token;
    token = strtok(NULL, NMEA_DELIMITERS);
    if (token != NULL) GPSInfo.Latitude = token;
    token = strtok(NULL, NMEA_DELIMITERS);
    if (token != NULL) GPSInfo.LatDir = token;
    token = strtok(NULL, NMEA_DELIMITERS);
    if (token != NULL) GPSInfo.Longitude = token;
    token = strtok(NULL, NMEA_DELIMITERS);
    if (token != NULL) GPSInfo.LonDir = token;
    token = strtok(NULL, NMEA_DELIMITERS);
    if (token != NULL) GPSInfo.Quality = token;
    token = strtok(NULL, NMEA_DELIMITERS);
    if (token != NULL) GPSInfo.Satelites = token;
    token = strtok(NULL, NMEA_DELIMITERS);
    if (token != NULL) GPSInfo.HDOP = token;
    token = strtok(NULL, NMEA_DELIMITERS);
    if (token != NULL) GPSInfo.Altitude = token;
    token = strtok(NULL, NMEA_DELIMITERS);
    if (token != NULL) GPSInfo.Units_al = token;
    token = strtok(NULL, NMEA_DELIMITERS);
    if (token != NULL) GPSInfo.Undulation = token;
    token = strtok(NULL, NMEA_DELIMITERS);
    if (token != NULL) GPSInfo.Units_un = token;
    token = strtok(NULL, NMEA_DELIMITERS);
    if (token != NULL) GPSInfo.Age = token;
    token = strtok(NULL, NMEA_DELIMITERS);
    if (token != NULL) GPSInfo.Cheksum = token;
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

/** @} */