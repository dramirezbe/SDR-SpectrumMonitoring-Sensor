#ifndef BACN_LTE_H
#define BACN_LTE_H

#include <pthread.h>
#include <stdint.h>
#include <stdbool.h>

#define UART_BUFFER_SIZE    120
#define DEFAULT_TIMEOUT     4000 //corresponde a 10seg tiempo/4= valor deseado
#define DEFAULT_CRLF_COUNT  2

#define SERIAL_DEV "/dev/ttyAMA0"

enum LTE_RESPONSE_STATUS{
    LTE_RESPONSE_WAITING,
    LTE_RESPONSE_FINISHED,
    LTE_RESPONSE_TIMEOUT,
    LTE_RESPONSE_BUFFER_FULL,
    LTE_RESPONSE_STARTING,
    LTE_RESPONSE_ERROR
};

typedef struct
{
    uint32_t serial_fd;
    pthread_t th_recv;

    int32_t recv_buff_cnt;

}st_uart;

/* Commands Functions*/
void Read_Response(void);
void Start_Read_Response(void);
bool WaitForExpectedResponse(const char* ExpectedResponse);
bool SendATandExpectResponse(st_uart *s_uart, const char* ATCommand, const char* ExpectedResponse);

bool LTE_Start(st_uart *s_uart);
void LTE_SendString(st_uart *s_uart, const char *data);
int8_t init_usart(st_uart *s_uart);
void close_usart(st_uart *s_uart);
void* LTEIntHandler(void *arg);

#endif // BACN_LTE_H