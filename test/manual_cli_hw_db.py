import os
import time
from datetime import datetime
import csv
import sys
from pathlib import Path

# Configuración de rutas y logger
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path: sys.path.insert(0, str(PROJECT_ROOT))
if str(TEST_ROOT) not in sys.path: sys.path.insert(0, str(TEST_ROOT))

import cfg
log = cfg.set_logger()

os.makedirs('./results', exist_ok=True)

# Generar timestamp humano y concatenarlo al nombre del archivo
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
archivo = f'./results/{ts}_HW_manual.csv'

with open(archivo, mode='a', newline='') as f:
    writer = csv.writer(f)
    
    if os.stat(archivo).st_size == 0:
        writer.writerow(['Time_Human', 'Time_Unix_ms', 'Current_A', 'Voltage_V', 'Temp_HW_C'])
    
    print("\n--- Registro de Hardware ---")
    print(f"Guardando en: {archivo}")
    print("Presiona Ctrl+C en cualquier momento para salir.\n")
    
    try:
        while True:
            # Pedir cada dato individualmente
            current = input("Current (A): ")
            voltage = input("Voltage (V): ")
            temp    = input("Temp (°C):   ")
            
            t_unix_ms = int(time.time() * 1000)
            t_human = datetime.now().strftime('%H:%M:%S.%f')[:-3]
            
            writer.writerow([t_human, t_unix_ms, current, voltage, temp])
            f.flush() 
            
            print("✓ Fila guardada\n") 
            
    except KeyboardInterrupt:
        print("\n\nGuardado exitoso. Programa terminado.")