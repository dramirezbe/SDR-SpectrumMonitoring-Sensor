import pyvisa
import numpy as np
import matplotlib.pyplot as plt
import time
import os
import threading
import queue
from flask import Flask, request
from flask_socketio import SocketIO, emit

# --- CONFIGURACIÓN ---
N9000B_IP = '10.42.0.41'
VISA_TIMEOUT = 10000
OUTPUT_DIR = 'comparative_data'

CENTER_FREQUENCIES = [100.0e6, 105.7e6, 110.0e6]
SPAN = 20.0e6

# Flask / SocketIO
app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*", logger=False, engineio_logger=False)

sensor_queue = queue.Queue()
client_connected = threading.Event()

os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- SERVIDOR ---
@socketio.on('connect')
def handle_connect():
    print(f"✅ Sensor Conectado: {request.sid}")
    client_connected.set()

@socketio.on('sensor_reading')
def handle_sensor_reading(data):
    sensor_queue.put(data)
    emit('server_ack', {'status': 'received'})

def run_server():
    socketio.run(app, host='10.182.143.246', port=5000, allow_unsafe_werkzeug=True)

# --- PRINCIPAL ---
def main():
    # Iniciar servidor en hilo aparte
    threading.Thread(target=run_server, daemon=True).start()
    print("Esperando conexión del sensor...")
    
    if not client_connected.wait(timeout=60):
        print("❌ Timeout: Sensor no conectado.")
        return

    try:
        rm = pyvisa.ResourceManager('@py')
        inst = rm.open_resource(f'TCPIP::{N9000B_IP}::INSTR')
        inst.timeout = VISA_TIMEOUT
        inst.write('*CLS')
        inst.write(':FORM ASC')
        print(f"✅ Conectado N9000B: {inst.query('*IDN?').strip()}")
    except Exception as e:
        print(f"❌ Error VISA: {e}")
        return

    for i, center_freq in enumerate(CENTER_FREQUENCIES):
        print(f"\n--- Captura {i+1}/{len(CENTER_FREQUENCIES)}: {center_freq/1e6} MHz ---")
        
        start_f = center_freq - (SPAN / 2)
        end_f = center_freq + (SPAN / 2)

        # 1. Solicitar datos al SENSOR
        with sensor_queue.mutex: sensor_queue.queue.clear()

        post_dict = {
            "start_freq_hz": start_f, 
            "end_freq_hz": end_f,
            "rbw_hz": 10000, 
            "sample_rate_hz": 20000000, 
            "span_hz": SPAN,
            "antenna_port": 1, 
            "window": "hamming", 
            "overlap": 0.5, "scale": "dBm",
            "lna_gain": 0, 
            "vga_gain": 0, 
            "antenna_amp": True
        }

        socketio.emit('configure_sensor', post_dict)
        
        # 2. Solicitar datos al N9000B
        inst.write(f':SENSe:FREQuency:CENTer {center_freq}')
        inst.write(f':SENSe:FREQuency:SPAN {SPAN}')
        time.sleep(0.5) # Pequeña espera para estabilizar el barrido
        
        n9000_y = np.array(inst.query_ascii_values(':TRACe:DATA? TRACE1'))
        
        # 3. Recibir datos del SENSOR
        try:
            packet = sensor_queue.get(timeout=10)
            sensor_y_raw = np.array(packet.get('Pxx', []))
        except queue.Empty:
            print("❌ Timeout esperando datos del sensor.")
            continue

        # 4. Procesamiento
        n9000_x = np.linspace(start_f, end_f, len(n9000_y))
        sensor_x_raw = np.linspace(start_f, end_f, len(sensor_y_raw))

        # Interpolar Sensor para que coincida con el eje X del N9000B
        sensor_y_interp = np.interp(n9000_x, sensor_x_raw, sensor_y_raw)

        # 5. Guardar CSV (Todo alineado)
        csv_name = os.path.join(OUTPUT_DIR, f'data_{int(center_freq)}.csv')
        data_stack = np.column_stack((n9000_x, n9000_y, sensor_y_interp))
        np.savetxt(csv_name, data_stack, delimiter=',', header='Freq_Hz,N9000B_dBm,Sensor_dB', comments='')
        print(f"💾 CSV guardado: {csv_name}")

        # 6. Guardar PNGs separados (N9000B y Sensor)
        # N9000B only
        png_n9000 = os.path.join(OUTPUT_DIR, f'plot_n9000b_{int(center_freq)}.png')
        plt.figure(figsize=(10, 6))
        plt.plot(n9000_x, n9000_y, label='N9000B', linewidth=1)
        plt.title(f"N9000B - Espectro - Central: {center_freq/1e6} MHz")
        plt.xlabel("Frecuencia (Hz)")
        plt.ylabel("Amplitud (dBm)")
        plt.legend()
        plt.grid(True, which='both', linestyle='--', alpha=0.7)
        plt.tight_layout()
        plt.savefig(png_n9000)
        plt.close()
        print(f"🖼️ PNG guardado: {png_n9000}")

        # Sensor only (si hay datos)
        png_sensor = os.path.join(OUTPUT_DIR, f'plot_sensor_{int(center_freq)}.png')
        if sensor_y_raw.size == 0:
            print(f"⚠️ Sensor no devolvió datos para {center_freq/1e6} MHz — no se guarda el plot del sensor.")
        else:
            plt.figure(figsize=(10, 6))
            plt.plot(n9000_x, sensor_y_interp, label='Sensor (Interpolado)', linewidth=1)
            plt.title(f"Sensor - Espectro - Central: {center_freq/1e6} MHz")
            plt.xlabel("Frecuencia (Hz)")
            plt.ylabel("Amplitud (dB or dBm)")
            plt.legend()
            plt.grid(True, which='both', linestyle='--', alpha=0.7)
            plt.tight_layout()
            plt.savefig(png_sensor)
            plt.close()
            print(f"🖼️ PNG guardado: {png_sensor}")

    inst.close()
    print("\nProceso finalizado exitosamente.")

if __name__ == '__main__':
    main()