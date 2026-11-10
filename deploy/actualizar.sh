#!/usr/bin/env bash
# Trae la última versión de Integralo desde GitHub y reinicia sin perder datos.
#     bash deploy/actualizar.sh
set -euo pipefail
cd "$(dirname "$0")/.."
sudo /usr/local/bin/integralo-respaldo 2>/dev/null || true   # copia antes de actualizar
git pull --ff-only
sudo docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
sudo docker image prune -f >/dev/null
echo "✓ Integralo actualizado."
