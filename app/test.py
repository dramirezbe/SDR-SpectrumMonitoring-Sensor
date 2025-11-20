import requests
import json  # Necesario para el formateo
from utils import get_persist_var
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
print(f"Data: {response.json()}")