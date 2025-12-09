import asyncio
from utils import ZmqSub
import cfg 

log = cfg.set_logger()

async def listen_routine():
    sub = ZmqSub(topic=cfg.ZmqClients.antenna_mux, verbose=True, log=log)
    print("Listening for 'antennas'...")
    
    while True:
        # This line yields control if no message is ready
        data = await sub.wait_msg() 
        if data:
            log.info(f"Data fetched: {data}")

async def health_check_routine():
    """Simulates a task that must run frequently"""
    while True:
        log.info("   [Background] Checking System Health...")
        await asyncio.sleep(2)

async def main():
    # Run both functions concurrently
    await asyncio.gather(
        listen_routine(),
        health_check_routine()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass