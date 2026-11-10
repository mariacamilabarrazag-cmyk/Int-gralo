"""Contraseñas, sesiones y cifrado de credenciales."""
import base64
import hashlib
import hmac
import json
import secrets

from cryptography.fernet import Fernet, InvalidToken

from . import config

_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(("enc:" + config.SECRET_KEY).encode()).digest()))


# ------------------------------------------------------------------ contraseñas (scrypt)

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, dk_b64 = stored.split("$")
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


# ------------------------------------------------------------------ tokens

def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def sign(value: str) -> str:
    mac = hmac.new(config.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{value}.{mac}"


def unsign(signed: str) -> str | None:
    value, _, mac = (signed or "").rpartition(".")
    if value and hmac.compare_digest(sign(value), signed):
        return value
    return None


# ------------------------------------------------------------------ cifrado de credenciales

def encrypt_json(data: dict) -> str:
    return _fernet.encrypt(json.dumps(data).encode()).decode()


def decrypt_json(blob: str | None) -> dict:
    if not blob:
        return {}
    try:
        return json.loads(_fernet.decrypt(blob.encode()))
    except (InvalidToken, ValueError):
        return {}
