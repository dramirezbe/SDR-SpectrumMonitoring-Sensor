from pathlib import Path
from utils import StatusDevice

def test_real_logs():
    # 1. Configurar la ruta a tu carpeta de logs
    # Si el script está en la raíz, suele ser Path.cwd() / "Logs"
    logs_path = Path.cwd() / "Logs"
    
    if not logs_path.exists():
        print(f"❌ Error: No se encontró la carpeta en {logs_path}")
        return

    # 2. Instanciar el dispositivo
    # Pasamos la ruta detectada para asegurarnos de que apunte al lugar correcto
    sd = StatusDevice(logs_dir=logs_path)

    print(f"--- Iniciando prueba de logs en: {logs_path} ---")
    
    # 3. Ejecutar el método
    # get_logs devuelve (None, None, logs_text)
    _, _, texto_resultado = sd.get_logs()

    # 4. Mostrar resultados
    print("\n--- CONTENIDO EXTRAÍDO ---")
    if texto_resultado == "Sistema operando normalmente":
        print("⚠️  Aviso: El método devolvió el mensaje por defecto.")
        print("Esto puede pasar si todos los logs tienen [[OK]] o si no hay archivos válidos.")
    else:
        print(texto_resultado)
    print("---------------------------\n")

    # 5. Verificación de conteo
    lineas = [l for l in texto_resultado.split('\n') if l.strip()]
    print(f"Total de líneas recuperadas: {len(lineas)} (Máximo esperado: 10)")

if __name__ == "__main__":
    test_real_logs()