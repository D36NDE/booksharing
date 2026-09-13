#!/usr/bin/env bash
#
# Deploy-Skript für BookSharing auf dem Produktionsserver.
#
# Holt den aktuellen main-Branch, installiert Python-Abhängigkeiten,
# prüft/migriert die Datenbank und startet den systemd-Service neu.
# Bricht bei jedem Fehler sofort ab (set -e), damit ein kaputtes Deploy
# nicht unbemerkt "durchrutscht".
#
# Verwendung (auf dem Server):
#   ./scripts/deploy.sh
#
# Einmalig ausführbar machen:
#   chmod +x scripts/deploy.sh

set -euo pipefail

# --- Konfiguration (bei Bedarf anpassen) ---
APP_DIR="/var/www/booksharing"
VENV_DIR="$APP_DIR/venv"
SERVICE_NAME="booksharing.service"
BRANCH="main"
DB_FILE="$APP_DIR/booksharing.db"
BACKUP_DIR="$APP_DIR/backups"

log() { echo "==> $*"; }
fail() { echo "❌ $*" >&2; exit 1; }

cd "$APP_DIR" || fail "App-Verzeichnis $APP_DIR nicht gefunden."

# --- 0. Sicherstellen, dass keine lokalen Änderungen im Weg sind ---
if [ -n "$(git status --porcelain)" ]; then
    fail "Es gibt uncommittete lokale Änderungen in $APP_DIR. Bitte erst klären (git status), dann erneut deployen."
fi

# --- 1. Datenbank sichern (falls vorhanden) ---
if [ -f "$DB_FILE" ]; then
    mkdir -p "$BACKUP_DIR"
    backup_file="$BACKUP_DIR/booksharing_$(date +%Y%m%d_%H%M%S).db"
    log "Sichere Datenbank nach $backup_file"
    cp "$DB_FILE" "$backup_file"
    # Nur die letzten 10 Backups behalten, um Speicher zu sparen
    ls -1t "$BACKUP_DIR"/booksharing_*.db 2>/dev/null | tail -n +11 | xargs -r rm --
fi

# --- 2. Code aktualisieren ---
log "Hole aktuellen Stand von origin/$BRANCH"
git fetch origin
git checkout "$BRANCH"
git pull origin "$BRANCH"

# --- 3. Python-Abhängigkeiten installieren ---
log "Installiere/aktualisiere Python-Abhängigkeiten"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q
pip install -r requirements.txt

# --- 4. Datenbank-Migration vorab prüfen (läuft auch beim App-Start,
#         hier aber isoliert testbar, bevor der Service neu startet) ---
log "Prüfe/migriere Datenbankschema"
python database.py
deactivate

# --- 5. Service neu starten ---
log "Starte $SERVICE_NAME neu"
sudo systemctl restart "$SERVICE_NAME"

# --- 6. Health-Check ---
log "Warte kurz und prüfe Service-Status..."
sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "✅ Deploy erfolgreich – $SERVICE_NAME läuft."
else
    echo "❌ $SERVICE_NAME ist NACH DEM DEPLOY NICHT aktiv!"
    echo "   Logs ansehen mit: sudo journalctl -u $SERVICE_NAME -n 50 --no-pager"
    exit 1
fi
