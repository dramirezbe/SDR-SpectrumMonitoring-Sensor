import os
import time
import argparse
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

# Configurar argparse
parser = argparse.ArgumentParser()
parser.add_argument('-t', action='store_true')
args = parser.parse_args()

os.makedirs('./results', exist_ok=True)

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
archivo = f'./results/{ts}_HW_manual.csv'

with open(archivo, mode='a', newline='') as f:
    writer = csv.writer(f)
    
    if os.stat(archivo).st_size == 0:
        header = ['Time_Human', 'Time_Unix_ms', 'Current_A', 'Voltage_V']
        if args.t:
            header.append('Temp_HW_C')
        writer.writerow(header)
    
    print("\n--- Registro de Hardware ---")
    print(f"Guardando en: {archivo}")
    print("Presiona Ctrl+C en cualquier momento para salir.\n")
    
    try:
        while True:
            current = input("Current (A): ")
            voltage = input("Voltage (V): ")
            
            t_unix_ms = int(time.time() * 1000)
            t_human = datetime.now().strftime('%H:%M:%S.%f')[:-3]
            
            row = [t_human, t_unix_ms, current, voltage]
            
            if args.t:
                temp = input("Temp (°C):   ")
                row.append(temp)
            
            writer.writerow(row)
            f.flush() 
            
            print("✓ Fila guardada\n") 
            
    except KeyboardInterrupt:
        print("\n\nGuardado exitoso. Programa terminado.")