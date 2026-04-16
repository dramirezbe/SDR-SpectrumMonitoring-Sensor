/**
 * @file bacn_LTE.c
 * @brief Controlador para la comunicación con módulos LTE vía comandos AT.
 */

#include "bacn_LTE.h"

/**
 * @addtogroup bacn_lte_module
 * @{
 */

uint32_t TimeOut = 0;
int8_t Response_Status, CRLF_COUNT = 0, Data_Count;
volatile uint8_t OBDCount = 0, GPSCount = 0;

// Variables globales para el control de flujo
char RESPONSE_BUFFER[UART_BUFFER_SIZE]; /**< Buffer global donde el hilo deposita los datos recibidos. */
bool LTERDY = false;                     /**< Flag que indica que hay nuevos datos listos en el buffer. */

bool LTE_run = false;

static pthread_mutex_t lte_response_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t lte_response_cond = PTHREAD_COND_INITIALIZER;

/** @cond DOXYGEN_SHOULD_SKIP_THIS */
extern bool LTE_open;
/** @endcond */

static void deadline_from_now_ms(struct timespec *ts, uint32_t timeout_ms)
{
    clock_gettime(CLOCK_REALTIME, ts);
    ts->tv_sec += timeout_ms / 1000U;
    ts->tv_nsec += (long)(timeout_ms % 1000U) * 1000000L;
    if (ts->tv_nsec >= 1000000000L) {
        ts->tv_sec += 1;
        ts->tv_nsec -= 1000000000L;
    }
}

static int count_crlf_sequences(const char *buffer)
{
    int crlf_found = 0;
    char prev = '\0';

    if (!buffer) return 0;

    for (size_t i = 0; buffer[i] != '\0'; ++i) {
        if (prev == '\r' && buffer[i] == '\n') {
            crlf_found++;
        }
        prev = buffer[i];
    }

    return crlf_found;
}

void Read_Response(void)
{
    pthread_mutex_lock(&lte_response_mutex);

    if (Response_Status == LTE_RESPONSE_STARTING) {
        Response_Status = LTE_RESPONSE_WAITING;
    }

    const size_t response_len = strnlen(RESPONSE_BUFFER, UART_BUFFER_SIZE);
    if (response_len == 0) {
        Response_Status = LTE_RESPONSE_TIMEOUT;
    } else if (response_len >= (size_t)(UART_BUFFER_SIZE - 1)) {
        Response_Status = LTE_RESPONSE_BUFFER_FULL;
    } else if (count_crlf_sequences(RESPONSE_BUFFER) >= (DEFAULT_CRLF_COUNT + CRLF_COUNT)) {
        Response_Status = LTE_RESPONSE_FINISHED;
    } else {
        Response_Status = LTE_RESPONSE_FINISHED;
    }

    CRLF_COUNT = 0;
    TimeOut = 0;
    pthread_mutex_unlock(&lte_response_mutex);
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
    struct timespec deadline;
    deadline_from_now_ms(&deadline, DEFAULT_TIMEOUT + TimeOut);

    pthread_mutex_lock(&lte_response_mutex);
    while (!LTERDY) {
        if (!LTE_run) {
            Response_Status = LTE_RESPONSE_ERROR;
            pthread_mutex_unlock(&lte_response_mutex);
            return false;
        }

        int rc = pthread_cond_timedwait(&lte_response_cond, &lte_response_mutex, &deadline);
        if (rc == ETIMEDOUT) {
            Response_Status = LTE_RESPONSE_TIMEOUT;
            pthread_mutex_unlock(&lte_response_mutex);
            return false;
        }
    }

    LTERDY = false;
    pthread_mutex_unlock(&lte_response_mutex);
    Start_Read_Response();                      /* First read response */

    pthread_mutex_lock(&lte_response_mutex);
    bool matched = (Response_Status != LTE_RESPONSE_TIMEOUT) &&
                   (strstr(RESPONSE_BUFFER, ExpectedResponse) != NULL);
    pthread_mutex_unlock(&lte_response_mutex);

    if (matched)
        return true;                            /* Return true for success */

    return false;                               /* Else return false */
}

bool SendATandExpectResponse(st_uart *s_uart, const char* ATCommand, const char* ExpectedResponse)
{
    pthread_mutex_lock(&lte_response_mutex);
    memset(RESPONSE_BUFFER, 0, sizeof(RESPONSE_BUFFER));
    LTERDY = false;
    Response_Status = LTE_RESPONSE_STARTING;
    pthread_mutex_unlock(&lte_response_mutex);

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
    pthread_mutex_lock(&lte_response_mutex);
    LTE_run = false;
    pthread_cond_broadcast(&lte_response_cond);
    pthread_mutex_unlock(&lte_response_mutex);
    close(s_uart->serial_fd);
}

void* LTEIntHandler(void *arg)
{
    st_uart *s_uart = (st_uart *)arg;
    fd_set rset;
    struct timeval tv;
    int32_t count = 0;

    while(LTE_run)
    {
        FD_ZERO(&rset);
        FD_SET(s_uart->serial_fd, &rset);
        tv.tv_sec = 30;
        tv.tv_usec = 0;

        count = select(s_uart->serial_fd + 1, &rset, NULL, NULL, &tv);

        if(count > 0)
        {
            char local_buffer[UART_BUFFER_SIZE];
            size_t used = 0;
            memset(local_buffer, 0, sizeof(local_buffer));

            while (LTE_run && used < (size_t)(UART_BUFFER_SIZE - 1)) {
                ssize_t bytes = read(s_uart->serial_fd, local_buffer + used, (size_t)(UART_BUFFER_SIZE - 1) - used);
                if (bytes > 0) {
                    used += (size_t)bytes;
                    s_uart->recv_buff_cnt = (int32_t)used;
                } else {
                    break;
                }

                FD_ZERO(&rset);
                FD_SET(s_uart->serial_fd, &rset);
                tv.tv_sec = 0;
                tv.tv_usec = 200000;
                count = select(s_uart->serial_fd + 1, &rset, NULL, NULL, &tv);
                if (count <= 0) {
                    break;
                }
            }

            pthread_mutex_lock(&lte_response_mutex);
            memset(RESPONSE_BUFFER, 0, sizeof(RESPONSE_BUFFER));
            memcpy(RESPONSE_BUFFER, local_buffer, used);
            if (used >= (size_t)(UART_BUFFER_SIZE - 1)) {
                Response_Status = LTE_RESPONSE_BUFFER_FULL;
            }
            LTERDY = true;
            pthread_cond_signal(&lte_response_cond);
            pthread_mutex_unlock(&lte_response_mutex);
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

/** @} */
