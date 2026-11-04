#!/usr/bin/env bash
# HV Director installer / upgrader for Debian 13 (trixie), single host, no Docker.
#
#   sudo ./deploy/install.sh [options]
#
# Installs PostgreSQL, nginx (TLS reverse proxy), Python venv with the application,
# guacd (built from source with FreeRDP 3, RDP/vmconnect enabled) and systemd units.
# Re-running the script upgrades the application in place (config and data are kept).
#
# Options:
#   --hostname NAME     name for the self-signed TLS certificate (default: hostname -f)
#   --admin-user NAME   first provider administrator (default: admin)
#   --skip-guacd        do not build/install guacd
#   --rebuild-guacd     rebuild guacd even if it is already installed
#   --build-ui          rebuild the web UI with Node.js (prebuilt frontend/dist is used otherwise)
#   --demo              use the built-in Hyper-V simulator instead of real hosts (demo/testing)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR=/opt/hvdirector
CONF_DIR=/etc/hvdirector
DATA_DIR=/var/lib/hvdirector
ENV_FILE="$CONF_DIR/hvdirector.env"
DB_NAME=hvdirector
DB_USER=hvdirector
SVC_USER=hvdirector

HOSTNAME_TLS="$(hostname -f 2>/dev/null || hostname)"
ADMIN_USER=admin
SKIP_GUACD=0
REBUILD_GUACD=0
BUILD_UI=0
DEMO=0

while [ $# -gt 0 ]; do
    case "$1" in
        --hostname) HOSTNAME_TLS="$2"; shift 2 ;;
        --admin-user) ADMIN_USER="$2"; shift 2 ;;
        --skip-guacd) SKIP_GUACD=1; shift ;;
        --rebuild-guacd) REBUILD_GUACD=1; shift ;;
        --build-ui) BUILD_UI=1; shift ;;
        --demo) DEMO=1; shift ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "Unknown option $1" >&2; exit 2 ;;
    esac
done

log() { echo -e "\e[1;32m==>\e[0m $*"; }
warn() { echo -e "\e[1;33mWARNING:\e[0m $*" >&2; }

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)" >&2; exit 1; }
if [ -r /etc/os-release ]; then
    . /etc/os-release
    if [ "${ID:-}" != "debian" ] || [ "${VERSION_ID:-}" != "13" ]; then
        warn "Tested on Debian 13; detected ${PRETTY_NAME:-unknown}. Continuing."
    fi
fi

export DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------- packages
log "Installing system packages"
apt-get update -q
apt-get install -y --no-install-recommends \
    postgresql nginx python3 python3-venv openssl curl ca-certificates rsync

# ---------------------------------------------------------------- user, dirs
id "$SVC_USER" >/dev/null 2>&1 || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SVC_USER"
install -d -o "$SVC_USER" -g "$SVC_USER" -m 0750 "$DATA_DIR" "$DATA_DIR/iso"
install -d -o root -g "$SVC_USER" -m 0750 "$CONF_DIR" "$CONF_DIR/tls"
install -d -m 0755 "$APP_DIR" "$APP_DIR/app" "$APP_DIR/app/frontend"

# ---------------------------------------------------------------- UI build (optional)
if [ "$BUILD_UI" = 1 ] || [ ! -f "$REPO_DIR/frontend/dist/index.html" ]; then
    log "Building the web UI (Node.js)"
    apt-get install -y --no-install-recommends nodejs npm
    (cd "$REPO_DIR/frontend" && npm ci --no-audit --no-fund && npm run build)
fi

# ---------------------------------------------------------------- application
log "Copying application to $APP_DIR/app"
rsync -a --delete --exclude '__pycache__' --exclude '*.egg-info' --exclude 'tests' \
    "$REPO_DIR/backend/" "$APP_DIR/app/backend/"
rsync -a --delete "$REPO_DIR/frontend/dist/" "$APP_DIR/app/frontend/dist/"
rsync -a --delete "$REPO_DIR/deploy/" "$APP_DIR/app/deploy/"

log "Creating Python virtual environment"
[ -x "$APP_DIR/venv/bin/python" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/app/backend/requirements.txt"
"$APP_DIR/venv/bin/pip" install -q --no-deps --force-reinstall "$APP_DIR/app/backend"

# ---------------------------------------------------------------- configuration
if [ ! -f "$ENV_FILE" ]; then
    log "Generating $ENV_FILE"
    DB_PASS="$(openssl rand -hex 24)"
    SECRET="$("$APP_DIR/venv/bin/python" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
    DRIVER=psrp
    [ "$DEMO" = 1 ] && DRIVER=fake
    umask 027
    cat > "$ENV_FILE" <<EOF
# HV Director bootstrap configuration (everything else is stored in PostgreSQL)
HVD_DATABASE_URL=postgresql+psycopg://${DB_USER}:${DB_PASS}@127.0.0.1:5432/${DB_NAME}
# Master key for credentials stored in the database - BACK IT UP
HVD_SECRET_KEY=${SECRET}
HVD_HYPERV_DRIVER=${DRIVER}
HVD_FAKE_STATE=${DATA_DIR}/fake-hyperv.json
HVD_COOKIE_SECURE=true
HVD_LOG_LEVEL=INFO
HVD_WORKER_THREADS=8
HVD_FRONTEND_DIST=${APP_DIR}/app/frontend/dist
EOF
    umask 022
fi
chown root:"$SVC_USER" "$ENV_FILE"
chmod 0640 "$ENV_FILE"
DB_PASS="$(sed -n 's#^HVD_DATABASE_URL=postgresql[^:]*://[^:]*:\([^@]*\)@.*#\1#p' "$ENV_FILE")"

# ---------------------------------------------------------------- PostgreSQL
log "Configuring PostgreSQL"
systemctl enable --now postgresql >/dev/null
for _ in $(seq 1 30); do runuser -u postgres -- pg_isready -q && break; sleep 1; done
if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1; then
    runuser -u postgres -- psql -q -c "CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}'"
else
    runuser -u postgres -- psql -q -c "ALTER ROLE ${DB_USER} PASSWORD '${DB_PASS}'"
fi
if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
    runuser -u postgres -- psql -q -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER} ENCODING 'UTF8' TEMPLATE template0"
fi

manage() {
    runuser -u "$SVC_USER" -- env HVD_CONFIG_FILE="$ENV_FILE" bash -c "set -a; . '$ENV_FILE'; set +a; '$APP_DIR/venv/bin/hvdirector-manage' $*"
}

log "Applying database migrations"
manage migrate
ADMIN_OUT="$(manage bootstrap-admin --username "$ADMIN_USER")"

# ---------------------------------------------------------------- guacd
if [ "$SKIP_GUACD" = 0 ]; then
    if [ "$REBUILD_GUACD" = 1 ] || [ ! -x /usr/local/sbin/guacd ]; then
        log "Building guacd (Apache Guacamole server) from source"
        bash "$REPO_DIR/deploy/build-guacd.sh"
    fi
    install -d -o guacd -g guacd -m 0750 /var/lib/guacd
    install -m 0644 "$REPO_DIR/deploy/systemd/hvdirector-guacd.service" /etc/systemd/system/
fi

# ---------------------------------------------------------------- TLS + nginx
if [ ! -f "$CONF_DIR/tls/server.crt" ]; then
    log "Generating self-signed TLS certificate for $HOSTNAME_TLS (replace with your own)"
    SAN="DNS:${HOSTNAME_TLS}"
    for ip in $(hostname -I 2>/dev/null); do SAN="${SAN},IP:${ip}"; done
    openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -subj "/CN=${HOSTNAME_TLS}" \
        -addext "subjectAltName=${SAN}" -keyout "$CONF_DIR/tls/server.key" -out "$CONF_DIR/tls/server.crt" 2>/dev/null
    chmod 0640 "$CONF_DIR/tls/server.key"
    chgrp www-data "$CONF_DIR/tls/server.key" 2>/dev/null || true
fi

log "Configuring nginx"
install -m 0644 "$REPO_DIR/deploy/nginx/hvdirector-headers.conf" /etc/nginx/snippets/hvdirector-headers.conf
install -m 0644 "$REPO_DIR/deploy/nginx/hvdirector.conf" /etc/nginx/sites-available/hvdirector.conf
ln -sf /etc/nginx/sites-available/hvdirector.conf /etc/nginx/sites-enabled/hvdirector.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t -q

# ---------------------------------------------------------------- services
log "Installing systemd units"
install -m 0644 "$REPO_DIR/deploy/systemd/hvdirector-api.service" /etc/systemd/system/
install -m 0644 "$REPO_DIR/deploy/systemd/hvdirector-worker.service" /etc/systemd/system/
systemctl daemon-reload
UNITS="hvdirector-api hvdirector-worker"
[ "$SKIP_GUACD" = 0 ] && UNITS="hvdirector-guacd $UNITS"
for u in $UNITS; do
    systemctl enable "$u" >/dev/null 2>&1
    systemctl restart "$u"
done
systemctl enable nginx >/dev/null 2>&1
systemctl reload-or-restart nginx

log "Waiting for the API"
for _ in $(seq 1 30); do
    if curl -fsk https://127.0.0.1/api/health >/dev/null 2>&1; then break; fi
    sleep 1
done
curl -fsk https://127.0.0.1/api/health >/dev/null && log "API is up" || warn "API health check failed: journalctl -u hvdirector-api"
manage check || true

echo
echo "=================================================================="
echo " HV Director is installed:  https://${HOSTNAME_TLS}/"
if [[ "$ADMIN_OUT" == CREATED* ]]; then
    set -- $ADMIN_OUT
    echo " Provider administrator:    $2"
    echo " Initial password:          $3   (change it after the first login)"
fi
echo " Organization sign-in:      https://${HOSTNAME_TLS}/tenant/<code>/login"
echo " Config / master key:       $ENV_FILE  (back it up together with the database)"
[ "$DEMO" = 1 ] && echo " Mode:                      DEMO (Hyper-V simulator, HVD_HYPERV_DRIVER=fake)"
echo " Next steps: Settings -> ISO share, Storage classes, Clusters -> Add (wizard), Organizations"
echo "=================================================================="
