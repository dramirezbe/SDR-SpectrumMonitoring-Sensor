from flask import Flask, request
from pathlib import Path

app = Flask(__name__)

@app.route('/upload_csv', methods=['POST'])
def upload_csv():
    folder_path = request.form.get('folder_path', '')
    file = request.files['file']

    # Directorio base donde corre este servidor
    base_dir = Path(__file__).resolve().parent

    # Tomamos solo el nombre de la carpeta final del path de la RPi
    folder_name = Path(folder_path).name if folder_path else "uploads"

    # Creamos la carpeta destino
    target_dir = base_dir / folder_name
    target_dir.mkdir(parents=True, exist_ok=True)

    # Nombre base del archivo
    filename = Path(file.filename).stem  # sin extensión
    extension = Path(file.filename).suffix  # extensión (por ej. .csv)

    save_path = target_dir / file.filename

    # 🔁 Si ya existe, genera nombre incremental
    counter = 1
    while save_path.exists():
        save_path = target_dir / f"{filename}_{counter}{extension}"
        counter += 1

    # Guardamos el archivo final
    file.save(save_path)

    print(f"[OK] Archivo recibido y guardado en {save_path}")
    return f"Archivo guardado en: {save_path}", 200


if __name__ == '__main__':
    app.run(host='10.182.143.246', port=5000)
