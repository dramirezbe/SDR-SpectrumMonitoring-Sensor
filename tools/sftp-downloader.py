#!/usr/bin/env python3

import os
import sys
import argparse
import paramiko
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

class SimpleSFTP:
    def __init__(self):
        self.host = "PLACEHOLDER"  # Cambia esto por la IP o hostname real del servidor SFTP
        self.port = 1222
        self.user = "root"
        self.password = 'PLACEHOLDER' # Cambia esto por la contraseña real del usuario root en el servidor SFTP

        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.sftp = None

    def connect(self):
        print(f"Conectando a {self.host}:{self.port}...")
        self.ssh.connect(
            hostname=self.host,
            port=self.port,
            username=self.user,
            password=self.password,
            look_for_keys=False,
            allow_agent=False
        )
        self.sftp = self.ssh.open_sftp()
        print("Conexión SFTP establecida.\n")

    def close(self):
        if self.sftp:
            self.sftp.close()
        self.ssh.close()
        print("Conexión cerrada.")

    def _progress_bar(self, transferred, total):
        if total == 0: return
        bar_len = 40
        filled_len = int(bar_len * transferred // total)
        bar = '█' * filled_len + '-' * (bar_len - filled_len)
        percent = 100.0 * transferred / total
        
        # \r vuelve al inicio de la línea, \033[K borra el resto para evitar basura visual
        sys.stdout.write(f'\r    [{bar}] {percent:.1f}%\033[K')
        sys.stdout.flush()

    def sync_files(self, remote_dir, local_dir):
        try:
            archivos = self.sftp.listdir(remote_dir)
        except IOError:
            print(f"El directorio remoto '{remote_dir}' no existe.")
            return

        if not archivos:
            print("El directorio remoto está vacío.")
            return

        print(f"Se encontraron {len(archivos)} archivos. Revisando cuáles faltan...\n")
        
        descargados = 0
        for archivo in archivos:
            remote_path = f"{remote_dir}/{archivo}"
            local_path = os.path.join(local_dir, archivo)
            
            if os.path.exists(local_path):
                continue
            
            print(f"📥 {archivo}")
            self.sftp.get(remote_path, local_path, callback=self._progress_bar)
            print("\n    ✅ Completado\n")
            descargados += 1

        print(f"¡Proceso terminado! Se descargaron {descargados} archivos nuevos.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Descarga archivos nuevos del servidor remoto con barra de progreso.")
    parser.add_argument("-r", type=str, required=True, help="Ruta de la carpeta local de destino")
    args = parser.parse_args()

    local_path = Path(args.r).resolve()
    local_path.mkdir(parents=True, exist_ok=True)

    remote_dir = "/root/files-database-postprocesamiento"
    
    sftp_client = SimpleSFTP()
    try:
        sftp_client.connect()
        sftp_client.sync_files(remote_dir, str(local_path))
    except Exception as e:
        print(f"Ocurrió un error: {e}")
    finally:
        sftp_client.close()