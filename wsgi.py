# WSGI-Konfiguration fuer PythonAnywhere
# Kopiere diesen Inhalt in deine WSGI-Datei auf PythonAnywhere:
# /var/www/<dein-username>_pythonanywhere_com_wsgi.py

import sys
import os

# Pfad zum Projektordner auf PythonAnywhere (passe <dein-username> an!)
path = '/home/<dein-username>/BookSharing'
if path not in sys.path:
    sys.path.insert(0, path)

# Optional: Umgebungsvariablen setzen (z. B. fuer den Flask Secret Key)
os.environ['SECRET_KEY'] = 'dein_sicherer_zufaelliger_schluessel_hier'

# Flask-App importieren. PythonAnywhere erwartet ein Objekt namens "application"
from app import app as application
