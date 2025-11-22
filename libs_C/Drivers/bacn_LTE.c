//Drivers/bacn_LTE.c
#include <stdio.h>
#include <unistd.h>
#include <fcntl.h>
#include <termios.h>
#include <stdint.h>
#include <sys/select.h>
#include <string.h>
#include <stdlib.h>
#include <time.h>

#include "bacn_LTE.h"

uint32_t TimeOut = 0;
int8_t Response_Status, CRLF_COUNT = 0, Data_Count;
volatile uint8_t OBDCount = 0, GPSCount = 0;

char RESPONSE_BUFFER[UART_BUFFER_SIZE];

bool LTE_run = false;
bool LTERDY = false;
extern bool LTE_open;

void Read_Response(void)
{
    char CRLF_BUF[2];
    char CRLF_FOUND;
    uint32_t TimeCount = 0, ResponseBufferLength;

    while(1)
    {
        if(TimeCount >= (DEFAULT_TIMEOUT+TimeOut))
        {
            CRLF_COUNT = 0; TimeOut = 0;
            Response_Status = LTE_RESPONSE_TIMEOUT;
            return;
        }

        if(Response_Status == LTE_RESPONSE_STARTING)
        {
            CRLF_FOUND = 0;
            memset(CRLF_BUF, 0, 2);
            Response_Status = LTE_RESPONSE_WAITING;
        }
        ResponseBufferLength = strlen(RESPONSE_BUFFER);
        if (ResponseBufferLength)
        {
            usleep(1000);

            TimeCount++;
            if (ResponseBufferLength==strlen(RESPONSE_BUFFER))
            {
                uint16_t i;
                for (i=0; i<ResponseBufferLength; i++)
                {
                    memmove(CRLF_BUF, CRLF_BUF + 1, 1);
                    CRLF_BUF[1] = RESPONSE_BUFFER[i];
                    if(!strncmp(CRLF_BUF, "\r\n", 2))
                    {
                        if(++CRLF_FOUND == (DEFAULT_CRLF_COUNT+CRLF_COUNT))
                        {
                            CRLF_COUNT = 0; TimeOut = 0;
                            Response_Status = LTE_RESPONSE_FINISHED;
                            return;
                        }
                    }
                }
                CRLF_FOUND = 0;
            }
        }
        usleep(1000);
        TimeCount++;
    }
}

void Start_Read_Response(void)
{
    Response_Status = LTE_RESPONSE_STARTING;
    do {
        Read_Response();
    } while(Response_Status == LTE_RESPONSE_WAITING);

}

bool WaitForExpectedResponse(const char* ExpectedResponse)
{   
    while(!LTERDY);
    LTERDY = false;
    Start_Read_Response();                      /* First read response */

    if((Response_Status != LTE_RESPONSE_TIMEOUT) && (strstr(RESPONSE_BUFFER, ExpectedResponse) != NULL))
        return true;                            /* Return true for success */

    return false;                               /* Else return false */
}

bool SendATandExpectResponse(st_uart *s_uart, const char* ATCommand, const char* ExpectedResponse)
{
    LTE_SendString(s_uart, ATCommand);            /* Send AT command to LTE */
    return WaitForExpectedResponse(ExpectedResponse);
}

void LTE_SendString(st_uart *s_uart, const char *data)
{
    char dataLTE[20];
    
    memset(dataLTE, 0, sizeof(dataLTE));
    sprintf(dataLTE, "<%s>", data);
    write(s_uart->serial_fd, dataLTE, strlen(dataLTE));   
}

bool LTE_Start(st_uart *s_uart)
{
    uint8_t i;

    for (i=0; i<5; i++)
    {
        if(SendATandExpectResponse(s_uart, "ATE0\r","OK"))
            return true;
    }

    return false;
}

int8_t init_usart(st_uart *s_uart)
{    
    struct termios tty;

    s_uart->serial_fd = -1;
    s_uart->serial_fd = open(SERIAL_DEV, O_RDWR | O_NOCTTY | O_NDELAY);
    if (s_uart->serial_fd == -1)
    {
        printf ("Error : open serial device: %s\r\n",SERIAL_DEV);
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

    LTE_run = true;

    if (pthread_create(&s_uart->th_recv, NULL, &LTEIntHandler, (void *)(s_uart)) != 0)
    {
        printf("ERROR : initial thread receive serial failed\r\n");
        return -1;
    }

    return 0;
}

void close_usart(st_uart *s_uart)
{   
    LTE_run = false;
    close(s_uart->serial_fd);
}

void* LTEIntHandler(void *arg)
{
    st_uart *s_uart = (st_uart *)arg;
    fd_set rset;
    struct timeval tv;
    int32_t count = 0;
    uint8_t i = 0;


    while(LTE_run)
    {
        FD_ZERO(&rset);
        FD_SET(s_uart->serial_fd, &rset);
        tv.tv_sec = 30;
        tv.tv_usec = 0;

        count = select(s_uart->serial_fd + 1, &rset, NULL, NULL, &tv);

        if(count > 0)
        {            
            memset(RESPONSE_BUFFER, 0, UART_BUFFER_SIZE); 
            usleep(800000);           
            s_uart->recv_buff_cnt = read(s_uart->serial_fd, &RESPONSE_BUFFER, UART_BUFFER_SIZE); 
            LTERDY = true;           
        }
        else
        {
            if(s_uart->serial_fd < 0)
            {
                //close serial
                LTE_run = false;
                close(s_uart->serial_fd);                
                printf("UART close 2\r\n");                 
            }
        }
    }
    LTE_open = true;
    printf("UART close\r\n"); 
    pthread_exit(NULL);       
}
