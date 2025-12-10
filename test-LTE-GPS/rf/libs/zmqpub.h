/**
 * @file Drivers/zmqpub.h
 * @brief ZeroMQ Publisher to send JSON data to Python
 */

#ifndef ZMQPUB_H
#define ZMQPUB_H

#include <zmq.h>

// We use a DIFFERENT address for results to avoid locking conflicts 
// with the command channel.
// Python ZmqSub must connect to THIS address to get results.
#define PUB_IPC_ADDR "ipc:///tmp/zmq_data" 

typedef struct {
    void *context;
    void *socket;
} zpub_t;

/**
 * @brief Initialize the Publisher
 * @return Pointer to zpub_t struct
 */
zpub_t* zpub_init(void);

/**
 * @brief Sends a message in the format "TOPIC JSON_STRING"
 * Matches Python: full_msg.split(" ", 1)
 * * @param pub Pointer to zpub struct
 * @param topic Topic string (e.g. "psd_data")
 * @param json_payload The JSON data string
 * @return Number of bytes sent, or -1 on error
 */
int zpub_publish(zpub_t *pub, const char *topic, const char *json_payload);

/**
 * @brief Closes socket and context
 */
void zpub_close(zpub_t *pub);

#endif