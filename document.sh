#!/bin/bash
# =================================================================
# Script de Automatización de Documentación (C & Python)
# =================================================================
set -e  # Detener el script si algo falla

# 1. Obtener la ruta raíz del proyecto
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "🚀 Iniciando proceso de documentación..."

# 2. Crear entorno virtual si no existe
if [ ! -d "doc-venv" ]; then
    echo "📦 Creando entorno virtual 'doc-venv'..."
    python3 -m venv doc-venv
fi

# 3. Activar el entorno virtual
echo "🔧 Activando entorno virtual..."
source doc-venv/bin/activate

# 4. Instalar/Actualizar dependencias
if [ -f "docs/requirements.txt" ]; then
    echo "📥 Instalando dependencias desde docs/requirements.txt..."
    pip install --upgrade pip
    pip install -r docs/requirements.txt
else
    echo "⚠️ Error: No se encontró docs/requirements.txt"
    exit 1
fi

# 5. Limpieza y Preparación
echo "🧹 Limpiando compilaciones anteriores..."
rm -rf docs/_build/
rm -rf docs/xml/

# 6. Generar XML desde C (Doxygen)
if command -v doxygen &> /dev/null; then
    echo "🔨 Ejecutando Doxygen para código C..."
    doxygen Doxyfile
else
    echo "❌ Error: Doxygen no está instalado en el sistema."
    exit 1
fi

# 7. Compilar HTML con Sphinx
echo "📚 Compilando documentación con Sphinx..."
cd docs
python3 -m sphinx -M html "." "_build"

# 8. Finalizar
echo "✅ Proceso completado con éxito."
deactivate
echo "🌐 Puedes ver los resultados en: file://$ROOT_DIR/docs/_build/html/index.html"