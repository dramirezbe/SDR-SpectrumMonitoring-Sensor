# docs/conf.py
import os
import sys

sys.path.insert(0, os.path.abspath('..'))

# Absolute path to the xml directory we just told Doxygen to use
current_dir = os.path.abspath(os.path.dirname(__file__))
xml_path = os.path.join(current_dir, 'xml')

# DIAGNOSTIC: This will tell you if the path actually exists during the build
if not os.path.exists(os.path.join(xml_path, 'index.xml')):
    print(f"!!! ERROR: index.xml not found at {xml_path}")

extensions = [
    'breathe',
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx_rtd_theme',
]

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

html_theme = "furo"
html_logo = "_static/Logo_GCPDS_spanish.png"

project = 'Sensor con tecnologías SDR para el monitoreo del espectro'
copyright = '2026, ANE'
author = 'BACN, dramirezbe'
