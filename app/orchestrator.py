import cfg
log = cfg.set_logger()
from utils import ZmqPub, RequestClient, ElapsedTimer

import sys
from dataclasses import dataclass
from typing import Optional, List
import time


state = cfg.SysState.IDLE

@dataclass
class Timeframe:
    start: int
    end: int

@dataclass
class FilterConfig:
    type: str
    filter_bw_hz: int
    order_filter: int

@dataclass
class Campaign:
    campaign_id: int
    status: str
    center_freq_hz: int
    rbw_hz: int
    sample_rate_hz: int
    antenna_port: int
    acquisition_period_s: int
    span: int
    scale: str
    window: str
    overlap: float
    lna_gain: int
    vga_gain: int
    antenna_amp: bool
    timeframe: Timeframe
    # Optional field (defaults to None if missing or null)
    filter: Optional[FilterConfig] = None 

    def __post_init__(self):
        # Auto-convert nested dicts to classes
        if isinstance(self.timeframe, dict):
            self.timeframe = Timeframe(**self.timeframe)
        if isinstance(self.filter, dict):
            self.filter = FilterConfig(**self.filter)

@dataclass
class JobResponse:
    campaigns: List[Campaign]

    def __post_init__(self):
        # Auto-convert list of dicts to list of Campaign objects
        if self.campaigns:
            self.campaigns = [Campaign(**c) if isinstance(c, dict) else c for c in self.campaigns]

def send_select_antenna(pub_obj:ZmqPub, num_antenna:int) -> None:
    pub_obj.public_client(cfg.ZmqClients.antenna_mux, {"select_antenna": num_antenna})

def resend_realtime(pub_obj:ZmqPub, resp):
    dict_resp = resp.json()
    pub_obj.public_client(cfg.ZmqClients.realtime, dict_resp)


def main() -> int:
    pub = None
    client = None
    state = cfg.SysState.IDLE
    try:
        pub = ZmqPub(verbose=cfg.VERBOSE, log=log)
        client = RequestClient(cfg.API_URL, timeout=(5, 15), verbose=cfg.VERBOSE, logger=log, api_key=cfg.get_mac())

        tim_jobs = ElapsedTimer()
        tim_jobs.init_count(cfg.CAMPAIGNS_INTERVAL_S)

        tim_realtime = ElapsedTimer()
        tim_realtime.init_count(cfg.REALTIME_INTERVAL_S)

        while True:
            try:
                if tim_realtime.time_elapsed():
                    tim_realtime.init_count(cfg.REALTIME_INTERVAL_S) # Reset timer immediately
                    
                    err, resp = client.get(cfg.REALTIME_URL)
                    
                    # 1. Error handling
                    if err != 0:
                        log.error(f"Failed to fetch realtime data. rc={err}")
                        continue

                    # 2. Priority Guard: Do not interrupt if we are busy with a Campaign
                    if state == cfg.SysState.CAMPAIGN:
                        if resp:
                            log.info("Received realtime response while in campaign state, ignoring...")
                        continue 

                    # 3. Handle Realtime Logic
                    if resp:
                        # Server sent data: Switch to Realtime and forward data
                        state = cfg.SysState.REALTIME
                        resend_realtime(pub, resp)
                    else:
                        # Server sent nothing: Stop Realtime if active, then idle
                        if state == cfg.SysState.REALTIME:
                            pub.public_client(cfg.ZmqClients.realtime, {"stop_realtime": True})
                        state = cfg.SysState.IDLE

                # Job fetching logic remains the same
                if tim_jobs.time_elapsed():
                    tim_jobs.init_count(cfg.CAMPAIGNS_INTERVAL_S)

                    err, resp = client.get(cfg.JOBS_URL)
                    
                    # 1. Error handling
                    if err != 0:
                        log.error(f"Failed to fetch jobs data. rc={err}")
                        continue
                    if resp:
                        jobs_dict = resp.json()
                        jobs_resp = JobResponse(**jobs_dict)
                        #Do logic of crontab
                    
                time.sleep(0.01)

            except Exception as e:
                log.error(f"Iteration error: {e}")
                time.sleep(1) # Prevent CPU spin if constant error
            except KeyboardInterrupt:
                log.info("Received KeyboardInterrupt, exiting...")

    except Exception as e:
        log.error("Failed to start Zmq Pub or RequestClient: %s", e)
    
    finally:
        if pub:
            pub.close()
        return 0

if __name__ == "__main__":
    rc = cfg.run_and_capture(main, cfg.LOG_FILES_NUM)
    sys.exit(rc)