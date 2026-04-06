from utils import ZmqPairController
import time
import asyncio
import cfg


async def main():
    print("Starting Kalibrate Payload Test... Esperando conexión del motor RF")
    controller = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=True)
    async with controller as zmq_ctrl:
        print("Enviando comando de calibración...")
        await zmq_ctrl.send_command({"calibrate": True})
        
        print("Esperando respuesta del motor (esto puede tomar 30-60 segundos)...")
        try:
            response = await zmq_ctrl.wait_for_data()
            print(f"✓ Respuesta recibida: {response}")
        except asyncio.TimeoutError:
            print("✗ Timeout: No se recibió respuesta del motor en 15 segundos")

if __name__ == "__main__":
    asyncio.run(main())

