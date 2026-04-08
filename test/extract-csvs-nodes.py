import argparse
import paramiko
import os
import stat

TUNNEL_HOST = "PLACEHOLDER"
TUNNEL_PORT = 1222
TUNNEL_USER = "root"
TUNNEL_PASS = 'PLACEHOLDER'

NODE_PORT = 21104
NODE_USER = "anepi"
NODE_PASS = "PLACEHOLDER"
REMOTE_DIR = "/home/anepi/SDR-SpectrumMonitoring-Sensor/results"

parser = argparse.ArgumentParser()
parser.add_argument('-n', required=True, type=str, help="Número de nodo")
parser.add_argument('-r', required=True, type=str, help="Ruta local base")
args = parser.parse_args()

node_ip = f"10.10.1.{args.n}"
local_dir = os.path.join(args.r, f"Node{args.n}")

jumpbox = paramiko.SSHClient()
jumpbox.set_missing_host_key_policy(paramiko.AutoAddPolicy())
jumpbox.connect(TUNNEL_HOST, port=TUNNEL_PORT, username=TUNNEL_USER, password=TUNNEL_PASS)

channel = jumpbox.get_transport().open_channel("direct-tcpip", (node_ip, NODE_PORT), ('127.0.0.1', 0))

target = paramiko.SSHClient()
target.set_missing_host_key_policy(paramiko.AutoAddPolicy())
target.connect(node_ip, port=NODE_PORT, username=NODE_USER, password=NODE_PASS, sock=channel)

sftp = target.open_sftp()

def sync(remote, local):
    os.makedirs(local, exist_ok=True)
    for item in sftp.listdir_attr(remote):
        r_path = f"{remote}/{item.filename}"
        l_path = os.path.join(local, item.filename)
        
        if stat.S_ISDIR(item.st_mode):
            sync(r_path, l_path)
        elif not os.path.exists(l_path):
            print(f"Descargando: {item.filename}")
            sftp.get(r_path, l_path)

sync(REMOTE_DIR, local_dir)
print("Sincronización completada.")

sftp.close()
target.close()
jumpbox.close()