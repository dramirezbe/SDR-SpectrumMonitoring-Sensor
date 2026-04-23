from utils import ZmqPairController
import asyncio
import cfg
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

async def main():
    print("Starting Kalibrate Payload Test... Esperando conexión del motor RF")
    controller = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=True)
    async with controller as zmq_ctrl:
        print("Enviando comando de calibración...")
        await zmq_ctrl.send_command({"calibrate": True})
        
        print("Esperando respuesta del motor (esto puede tomar 30-60 segundos)...")
        response = await zmq_ctrl.wait_for_data()
        if response is None:
            print("✗ Timeout: No se recibió respuesta del motor en 15 segundos")
        else:
            print(f"✓ Respuesta recibida: {response}")

if __name__ == "__main__":
    asyncio.run(main())
