#!/usr/bin/env bash
# =====================================================================
# Instala Integralo en un servidor Ubuntu (pensado para Oracle Cloud
# "Always Free", funciona en cualquier VPS Ubuntu 22.04/24.04).
#
# Uso, desde la carpeta del proyecto en el servidor:
#     bash deploy/instalar.sh
#
# Hace: memoria swap, Docker, abre los puertos 80/443 del firewall del
# servidor, crea el archivo .env (preguntando lo necesario), opcionalmente
# configura un subdominio gratis de DuckDNS, levanta la app con HTTPS
# automático y programa una copia de seguridad diaria.
# Se puede volver a ejecutar sin problema.
# =====================================================================
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
COMPOSE=(sudo docker compose --env-file "$ROOT/.env" -f "$ROOT/deploy/docker-compose.yml")

say()  { printf '\n\033[1;34m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }

if ! grep -qi ubuntu /etc/os-release; then
  warn "Este script está probado en Ubuntu. Continúo de todas formas."
fi

# ---------------------------------------------------------------- swap
say "Memoria swap"
if ! swapon --show | grep -q .; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  ok "Swap de 2 GB creada (los servidores gratis tienen poca RAM)."
else
  ok "Ya hay swap."
fi

# ---------------------------------------------------------------- docker
say "Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER" || true
fi
sudo systemctl enable --now docker >/dev/null
ok "Docker $(sudo docker --version | cut -d' ' -f3 | tr -d ,)"

# ---------------------------------------------------------------- firewall
say "Firewall del servidor (puertos 80 y 443)"
# Las imágenes Ubuntu de Oracle traen reglas iptables que bloquean todo menos SSH.
for port in 80 443; do
  if ! sudo iptables -C INPUT -p tcp --dport "$port" -m conntrack --ctstate NEW -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT -p tcp --dport "$port" -m conntrack --ctstate NEW -j ACCEPT
  fi
done
if ! sudo iptables -C INPUT -p udp --dport 443 -j ACCEPT 2>/dev/null; then
  sudo iptables -I INPUT -p udp --dport 443 -j ACCEPT
fi
if command -v netfilter-persistent >/dev/null; then
  sudo netfilter-persistent save >/dev/null 2>&1 || true
else
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq iptables-persistent >/dev/null 2>&1 || true
  sudo netfilter-persistent save >/dev/null 2>&1 || true
fi
if command -v ufw >/dev/null && sudo ufw status | grep -q "Status: active"; then
  sudo ufw allow 80/tcp >/dev/null; sudo ufw allow 443 >/dev/null
fi
ok "Puertos abiertos en el servidor."
warn "Recuerda abrirlos también en Oracle: Red virtual → Lista de seguridad → Reglas de entrada (ver DESPLIEGUE.md)."

# ---------------------------------------------------------------- .env
setvar() {  # setvar NOMBRE valor  → crea o reemplaza la línea en .env
  local name="$1" value="$2"
  if grep -q "^${name}=" .env; then
    python3 - "$name" "$value" <<'PY'
import sys, re, pathlib
name, value = sys.argv[1], sys.argv[2]
p = pathlib.Path(".env")
lines = [f"{name}={value}" if l.startswith(f"{name}=") else l for l in p.read_text().splitlines()]
p.write_text("\n".join(lines) + "\n")
PY
  else
    echo "${name}=${value}" >> .env
  fi
}
getvar() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }

say "Configuración (.env)"
NEW_ENV=0
if [ ! -f .env ]; then cp .env.example .env; NEW_ENV=1; fi
chmod 600 .env

DOMAIN="$(getvar DOMAIN)"
if [ -z "$DOMAIN" ]; then
  echo "¿Con qué dominio se abrirá Integralo? (ej. heymap.duckdns.org o app.tudominio.com)"
  read -r -p "Dominio: " DOMAIN
  DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN%%/*}"
fi
setvar DOMAIN "$DOMAIN"
setvar PUBLIC_URL "https://$DOMAIN"

if [ -z "$(getvar SECRET_KEY)" ]; then
  setvar SECRET_KEY "$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
  ok "SECRET_KEY generada (guárdala en un lugar seguro: está en .env)."
fi

if [ -z "$(getvar ANTHROPIC_API_KEY)" ]; then
  read -r -s -p "Clave de Claude (ANTHROPIC_API_KEY, Enter para dejarla para después): " KEY; echo
  if [ -n "$KEY" ]; then setvar ANTHROPIC_API_KEY "$KEY"; fi
fi

if [ "$NEW_ENV" = 1 ] || [ -z "$(getvar ALLOW_SIGNUP)" ]; then
  read -r -p "¿Permitir que cualquier tienda cree su cuenta en /registro? [s/N]: " R
  if [[ "$R" =~ ^[sS] ]]; then setvar ALLOW_SIGNUP true; else setvar ALLOW_SIGNUP false; fi
fi
ok ".env listo."

# ---------------------------------------------------------------- DuckDNS (opcional)
if [[ "$DOMAIN" == *.duckdns.org ]]; then
  say "DuckDNS (subdominio gratis)"
  DUCK_TOKEN="$(getvar DUCKDNS_TOKEN)"
  if [ -z "$DUCK_TOKEN" ]; then
    read -r -s -p "Token de DuckDNS (lo ves en duckdns.org al iniciar sesión): " DUCK_TOKEN; echo
    setvar DUCKDNS_TOKEN "$DUCK_TOKEN"
  fi
  SUB="${DOMAIN%.duckdns.org}"
  sudo tee /usr/local/bin/integralo-duckdns >/dev/null <<EOF
#!/bin/sh
curl -fsS "https://www.duckdns.org/update?domains=${SUB}&token=${DUCK_TOKEN}&ip=" >/dev/null
EOF
  sudo chmod 700 /usr/local/bin/integralo-duckdns
  sudo /usr/local/bin/integralo-duckdns && ok "DuckDNS apunta a este servidor."
  ( sudo crontab -l 2>/dev/null | grep -v integralo-duckdns; echo "*/10 * * * * /usr/local/bin/integralo-duckdns" ) | sudo crontab -
fi

# ---------------------------------------------------------------- comprobar DNS
say "Comprobando que $DOMAIN apunte a este servidor"
MYIP="$(curl -fsS https://api.ipify.org || true)"
DNSIP="$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}' || true)"
if [ -n "$MYIP" ] && [ "$MYIP" = "$DNSIP" ]; then
  ok "$DOMAIN → $MYIP"
else
  warn "$DOMAIN apunta a '${DNSIP:-nada}' y este servidor es '${MYIP:-?}'."
  warn "El certificado HTTPS solo se emite cuando el dominio apunta aquí. Caddy lo reintentará solo."
fi

# ---------------------------------------------------------------- arrancar
say "Construyendo y arrancando Integralo (la primera vez tarda unos minutos)"
"${COMPOSE[@]}" up -d --build
ok "Contenedores en marcha."

# ---------------------------------------------------------------- copias de seguridad
say "Copia de seguridad diaria"
sudo tee /usr/local/bin/integralo-respaldo >/dev/null <<EOF
#!/bin/sh
cd "$ROOT" && docker compose --env-file .env -f deploy/docker-compose.yml exec -T app python -m scripts.respaldo
EOF
sudo chmod 755 /usr/local/bin/integralo-respaldo
( sudo crontab -l 2>/dev/null | grep -v integralo-respaldo; echo "30 3 * * * /usr/local/bin/integralo-respaldo >/var/log/integralo-respaldo.log 2>&1" ) | sudo crontab -
ok "Todos los días a las 3:30 a. m. (se guardan 14 días dentro del volumen de datos)."

# ---------------------------------------------------------------- verificación
say "Verificando HTTPS"
for i in $(seq 1 20); do
  if curl -fsS "https://$DOMAIN/salud" >/dev/null 2>&1; then
    ok "¡Listo! Integralo está en https://$DOMAIN"
    if [ "$(getvar ALLOW_SIGNUP)" = "false" ]; then
      echo "   Crea la primera tienda con:"
      echo "   ${COMPOSE[*]} exec app python -m scripts.crear_tienda --nombre \"Hey Map\" --email correo@ejemplo.com --asistente Mapi --preset personalizacion"
    else
      echo "   Crea la primera tienda en https://$DOMAIN/registro"
    fi
    exit 0
  fi
  sleep 6
done
warn "Aún no responde por HTTPS. Revisa: puertos 80/443 en la Lista de seguridad de Oracle y que el dominio apunte a ${MYIP:-este servidor}."
warn "Ver registros: ${COMPOSE[*]} logs --tail 50 caddy"
