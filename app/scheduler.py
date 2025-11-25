import cfg
log = cfg.set_logger()

import sys

topic = cfg.ZmqClients.scheduler

def main()->int:
    return 0

if __name__ == "__main__":
    rc = cfg.run_and_capture(main, cfg.LOG_FILES_NUM)
    sys.exit(rc)