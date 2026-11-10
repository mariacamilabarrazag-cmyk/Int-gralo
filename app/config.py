"""Configuración de la plataforma Integralo (variables de entorno / archivo .env).

La configuración de cada tienda (marca, asistente, credenciales de canales) se guarda en la base
de datos y se edita desde el dashboard; aquí solo va lo que es común a toda la plataforma.
"""
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


APP_NAME = "Integralo"

# Dirección pública donde corre la app (se usa para mostrar las URLs de webhooks y para Tiendanube)
PUBLIC_URL = env("PUBLIC_URL", "http://localhost:8000").rstrip("/")

# --- Archivos ---
DATA_DIR = Path(env("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "integralo.db"
TENANTS_DIR = DATA_DIR / "tiendas"
TENANTS_DIR.mkdir(parents=True, exist_ok=True)


def _secret_key() -> str:
    """Clave para cifrar credenciales. Si no está en el entorno se genera una y se guarda en data/."""
    key = env("SECRET_KEY")
    if key:
        return key
    f = DATA_DIR / "secret.key"
    if not f.exists():
        f.write_text(secrets.token_urlsafe(48))
        try:
            f.chmod(0o600)
        except OSError:
            pass
    return f.read_text().strip()


SECRET_KEY = _secret_key()
COOKIE_SECURE = PUBLIC_URL.startswith("https://")
SESSION_DAYS = 30
ALLOW_SIGNUP = env("ALLOW_SIGNUP", "true").lower() == "true"  # false = solo el administrador crea tiendas

# --- IA (Claude) ---
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY")
CLAUDE_MODEL = env("CLAUDE_MODEL", "claude-opus-5-5")

# --- Meta (WhatsApp / Messenger / Instagram) ---
GRAPH_VERSION = env("GRAPH_VERSION", "v21.0")

# --- Tiendanube (app de Integralo registrada en el Portal de Socios de Tiendanube) ---
TIENDANUBE_APP_ID = env("TIENDANUBE_APP_ID")
TIENDANUBE_CLIENT_SECRET = env("TIENDANUBE_CLIENT_SECRET")
TIENDANUBE_API_URL = env("TIENDANUBE_API_URL", "https://api.tiendanube.com/2025-03").rstrip("/")
TIENDANUBE_AUTH_URL = env("TIENDANUBE_AUTH_URL", "https://www.tiendanube.com/apps/{app_id}/authorize")
TIENDANUBE_USER_AGENT = env("TIENDANUBE_USER_AGENT", "Integralo (soporte@integralo.app)")
