"""
from utils import ZmqPairController
import time
import asyncio
import cfg


async def main():
    print("Starting Kalibrate Payload Test... Wait 5 secs")
    controller = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False)
    time.sleep(5)
    async with controller as zmq_ctrl:
        print("Sending calibrate command...")
        await zmq_ctrl.send_command({"calibrate": True})

if __name__ == "__main__":
    asyncio.run(main())
"""

