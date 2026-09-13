# WSGI-Konfiguration fuer PythonAnywhere
# Kopiere diesen Inhalt in deine WSGI-Datei auf PythonAnywhere:
# /var/www/<dein-username>_pythonanywhere_com_wsgi.py

import sys
import os

# Pfad zum Projektordner auf PythonAnywhere (passe <dein-username> an!)
path = '/home/<dein-username>/BookSharing'
if path not in sys.path:
    sys.path.insert(0, path)

# WICHTIG: SECRET_KEY NICHT hier im Code hinterlegen (landet sonst im Git-Repo)!
# Setze ihn stattdessen als Umgebungsvariable auf PythonAnywhere:
# Web-Tab -> "Environment variables" -> SECRET_KEY = <zufaelliger, langer String>
# Einen zufaelligen Wert kannst du z. B. lokal erzeugen mit:
#   python -c "import secrets; print(secrets.token_hex(32))"
# Ohne gesetzte Variable startet die App zwar (siehe app.py), aber alle
# Sessions gehen bei jedem Neustart/Reload verloren.

# Fuer den "Passwort vergessen"-Versand zusaetzlich noetig (ebenfalls als
# Umgebungsvariablen setzen, nicht hier im Code):
#   SMTP_HOST, SMTP_PORT (Standard 587), SMTP_USERNAME, SMTP_PASSWORD,
#   MAIL_FROM (z.B. "BookSharing <noreply@deine-domain.de>")
# Ohne SMTP_HOST wird der Mail-Versand uebersprungen und nur im Log vermerkt -
# die App startet trotzdem, Reset-Links koennen dann aber nicht zugestellt werden.

# Flask-App importieren. PythonAnywhere erwartet ein Objekt namens "application"
from app import app as application
