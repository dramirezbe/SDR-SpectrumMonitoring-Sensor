from cfg import ZmqClients
import cfg
log = cfg.set_logger()

import sys
import subprocess
import json
import zmq


def main()->int:
    # ZMQ setup
    context = zmq.Context()
    # Create a REP (Reply) socket
    socket = context.socket(zmq.PUB)
    socket.bind("tcp://*:5555")

    while True:
        dummy_data = {
            "dummy": "data"
        }
        json_msg = json.dumps(dummy_data)
        #sending to just scheduler
        socket.send_string(ZmqClients.scheduler + json_msg)



    return 0

if __name__ == "__main__":
    rc = cfg.run_and_capture(main, cfg.LOG_FILES_NUM)
    sys.exit(rc)