"""import requests
import json  # Necesario para el formateo
import cfg

# Definir URL y Headers
url = f"{cfg.API_URL}{cfg.JOBS_URL}"
headers = {
    "Authorization": f"ApiKey {cfg.API_KEY}",
    "Accept": "application/json"
}

print(cfg.API_URL)
print(cfg.API_KEY)

print(f"Consultando: {url} ...")

try:
    # Ejecutar petición
    response = requests.get(url, headers=headers)

    # Ver resultados
    print(f"Status: {response.status_code}")

    # --- BLOQUE DE FORMATEO ---
    try:
        data = response.json()
        # json.dumps convierte el objeto a texto con sangría (indent=4)
        formatted_json = json.dumps(data, indent=4, ensure_ascii=False)
        print("Data:")
        print(formatted_json)
    except json.JSONDecodeError:
        print(f"Respuesta no es JSON: {response.text}")
    # --------------------------

except requests.exceptions.ConnectionError:
    print(f"[ERROR] No se pudo conectar a {BASE}")
    print("Verifica que el servidor esté corriendo y el puerto en cfg.py sea el correcto.")
# Ejecutar petición
response = requests.get(url, headers=headers)

# Ver resultados
print(f"Status: {response.status_code}")
print(f"Data: {response.json()}")"""



import cfg
from utils import RequestClient

log = cfg.set_logger()

client = RequestClient(cfg.API_URL, timeout=(5, 15), verbose=cfg.VERBOSE, logger=log, api_key=cfg.API_KEY)


rc, resp = client.get(cfg.JOBS_URL)

if rc == 0 and resp is not None:
    log.info(resp.json())
else:
    log.error(f"GET request: Failed to fetch jobs: rc={rc}")