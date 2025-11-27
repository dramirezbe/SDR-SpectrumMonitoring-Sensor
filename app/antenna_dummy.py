import asyncio
import sys

from utils import ZmqSub
import cfg

log = cfg.set_logger()

async def fetch_antenna():
    sub = ZmqSub(topic=cfg.ZmqClients.antenna_mux, verbose=True, log=log)
    print("Listening for 'antennas'...")
    
    while True:
        # This line yields control if no message is ready
        data = await sub.wait_msg() 
        if data:
            antenna_num = int(data.get("select_antenna"))
            log.info(f"Selected Antenna: {antenna_num}")
            
async def gps_sender():
    """Simulates a task that must run frequently"""
    while True:
        log.info("Sending GPS each 10 secs")
        await asyncio.sleep(10)

async def async_main():
    # Run both functions concurrently
    await asyncio.gather(
        fetch_antenna(),
        gps_sender()
    )
    return 0

def main():
    """Synchronous bridge to run the async loop"""
    try:
        return asyncio.run(async_main()) 
    except KeyboardInterrupt:
        return 0

if __name__ == "__main__":
    rc = cfg.run_and_capture(main, cfg.LOG_FILES_NUM)
    sys.exit(rc)