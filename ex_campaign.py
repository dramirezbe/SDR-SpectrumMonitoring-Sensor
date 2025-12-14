import cfg
log = cfg.set_logger()
from utils import (
    RequestClient,
    CronHandler,
)
from .functions import return_campaign_object
import sys

dummy_cmd = "echo 'dummy'"

def main()->int:
    client = RequestClient(cfg.API_URL, mac_wifi=cfg.get_mac(), timeout=(5, 15), verbose=cfg.VERBOSE, logger=log)
    
    response_obj = return_campaign_object(client, log)
    if not response_obj:
        log.error("Failed to fetch campaigns.")
        return 1

    # ---------------------------------------------------------
    # 4. Use the Data
    # ---------------------------------------------------------
    log.info(f"Successfully loaded {len(response_obj.campaigns)} campaigns.")

    cron_handler = CronHandler(
        get_time_ms=cfg.get_time_ms(),
        logger=log,
        verbose=cfg.VERBOSE
    )

    log.info("Syncing crontab with active campaigns...")
    try:
        cron_handler.process_campaigns(response_obj, dummy_cmd)
    except Exception as e:
        log.error(f"Critical error updating crontab: {e}")
        return 1

    return 0

if __name__ == "__main__":
    rc = cfg.run_and_capture(main)
    sys.exit(rc)