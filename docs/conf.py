# docs/conf.py
import os
import sys

sys.path.insert(0, os.path.abspath('..'))

# Absolute path to the xml directory we just told Doxygen to use
current_dir = os.path.abspath(os.path.dirname(__file__))
xml_path = os.path.abspath(os.path.join(current_dir, 'xml'))

# DIAGNOSTIC: This will tell you if the path actually exists during the build
if not os.path.exists(os.path.join(xml_path, 'index.xml')):
    print(f"!!! ERROR: index.xml not found at {xml_path}")

extensions = [
    'breathe',
    'sphinx.ext.mathjax',
    'sphinx.ext.imgmath',
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx_rtd_theme',
]

# Configuración específica para el PDF (LaTeX de Sphinx)
latex_elements = {
    'preamble': r'''
        \usepackage{amsmath}
        \usepackage{amsfonts}
        \usepackage{amssymb}
    ''',
}

mathjax3_config = {
    'tex': {
        'inlineMath': [['\\(', '\\)'], ['$', '$']],
        'displayMath': [['\\[', '\\]'], ['$$', '$$']],
    }
}

# Add this line to tell Sphinx "ignore these libraries, just read the docstrings"
autodoc_mock_imports = [
    "dotenv", 
    "zmq", 
    "crontab", 
    "numpy", 
    "websockets", 
    "gi", 
    "gi.repository"
]

breathe_projects = {
    "spectrum_sensor": xml_path
}
breathe_default_project = "spectrum_sensor"
breathe_default_domain = 'c'
breathe_default_members = ('members', 'undoc-members', 'detaileddescription')
# Añade esta línea para asegurar que no se oculte nada
breathe_show_define_initializer = True
breathe_show_enumvalue_initializer = True

# 3. Soporte para tipos complejos de C99 y evitar errores de "Invalid C++ declaration"
c_id_attributes = ['complex', '_Complex', 'atomic_int', 'atomic_double', '_Atomic']
cpp_id_attributes = ['_Atomic']
c_extra_keywords = ['complex', '_Complex', 'double complex']

c_macro_replacement_table = {
    "_Atomic": "",
}

language = 'es'

html_theme = "furo"
html_logo = "_static/Logo_GCPDS_spanish.png"

project = 'Sensor con tecnologías SDR para el monitoreo del espectro'
copyright = '2026, ANE'
author = 'BACN, GCPDS'
