"""Mensajería por canal (WhatsApp, Messenger, Instagram, TikTok) usando las credenciales de cada tienda."""
import base64
import hashlib
import hmac
import logging
from dataclasses import dataclass

import httpx

from . import config, db

log = logging.getLogger("channels")
GRAPH = f"https://graph.facebook.com/{config.GRAPH_VERSION}"


# ------------------------------------------------------------------ envío

@dataclass
class SendResult:
    ok: bool
    external_id: str | None = None   # id del mensaje en la red social (wamid en WhatsApp)
    error: str = ""                   # explicación para mostrar en el panel

    def __bool__(self):
        return self.ok


NOT_CONNECTED = "El canal no está conectado: el mensaje no le llegó al cliente (revisa Configuración → Canales)."


def send_message(tenant: dict, channel: str, external_user_id: str, text: str) -> SendResult:
    """Envía `text` al cliente por su canal."""
    s = db.get_secrets(tenant)
    try:
        if channel == "whatsapp":
            return _send_whatsapp(tenant, s, external_user_id, text)
        if channel in ("facebook", "instagram"):
            return _send_meta(s, external_user_id, text)
        if channel == "tiktok":
            return _send_tiktok(s, external_user_id, text)
        return SendResult(channel == "simulador", error="" if channel == "simulador" else NOT_CONNECTED)
    except httpx.HTTPError:
        log.exception("Error de red enviando por %s (tienda %s)", channel, tenant["id"])
        return SendResult(False, error="No hubo conexión con la red social; intenta de nuevo.")


def _send_whatsapp(tenant: dict, s: dict, to: str, text: str) -> SendResult:
    if not (s.get("whatsapp_token") and s.get("whatsapp_phone_number_id")):
        return SendResult(False, error=NOT_CONNECTED)
    r = httpx.post(f"{GRAPH}/{s['whatsapp_phone_number_id']}/messages", timeout=20,
                   headers={"Authorization": f"Bearer {s['whatsapp_token']}"},
                   json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text[:4096]}})
    data = r.json() if r.content else {}
    if r.status_code < 400 and data.get("messages"):
        return SendResult(True, data["messages"][0].get("id"))
    err = data.get("error") or {}
    log.error("WhatsApp respondió %s: %s", r.status_code, str(err)[:400])
    if err.get("code") == 131047:
        hint = ("Respóndele desde la app de WhatsApp Business del celular." if tenant.get("whatsapp_mode") == "coexistencia"
                else "Solo se le puede escribir con una plantilla aprobada por Meta.")
        return SendResult(False, error=f"Pasaron más de 24 horas desde el último mensaje del cliente. {hint}")
    return SendResult(False, error=f"WhatsApp rechazó el mensaje: {err.get('message', r.status_code)}")


def _send_meta(s: dict, recipient_id: str, text: str) -> SendResult:
    if not s.get("meta_page_token"):
        return SendResult(False, error=NOT_CONNECTED)
    r = httpx.post(f"{GRAPH}/me/messages", timeout=20, params={"access_token": s["meta_page_token"]},
                   json={"recipient": {"id": recipient_id}, "messaging_type": "RESPONSE", "message": {"text": text[:1000]}})
    data = r.json() if r.content else {}
    if r.status_code < 400:
        return SendResult(True, data.get("message_id"))
    log.error("Meta respondió %s: %s", r.status_code, r.text[:400])
    if (data.get("error") or {}).get("code") == 10:
        return SendResult(False, error="Pasaron más de 24 horas desde el último mensaje del cliente; respóndele desde la app.")
    return SendResult(False, error=f"Meta rechazó el mensaje: {(data.get('error') or {}).get('message', r.status_code)}")


def _send_tiktok(s: dict, conversation_id: str, text: str) -> SendResult:
    """TikTok Business Messaging solo está disponible para cuentas aprobadas. Completar con la
    documentación que entregue TikTok al aprobar el acceso; mientras tanto se responde desde la app de TikTok."""
    if s.get("tiktok_access_token"):
        log.warning("Envío a TikTok pendiente de implementar con el acceso aprobado de TikTok.")
    return SendResult(False, error="Responder por TikTok desde Integralo estará disponible cuando TikTok apruebe el acceso. Respóndele desde la app de TikTok.")


def fetch_whatsapp_media(tenant: dict, media_id: str) -> tuple[bytes, str] | None:
    """Descarga un archivo (imagen, PDF, audio) que envió un cliente por WhatsApp."""
    s = db.get_secrets(tenant)
    if not s.get("whatsapp_token"):
        return None
    h = {"Authorization": f"Bearer {s['whatsapp_token']}"}
    meta = httpx.get(f"{GRAPH}/{media_id}", headers=h, timeout=20)
    if meta.status_code >= 400:
        return None
    f = httpx.get(meta.json()["url"], headers=h, timeout=60)
    return (f.content, f.headers.get("content-type", "application/octet-stream")) if f.status_code < 400 else None


# ------------------------------------------------------------------ firmas

def verify_meta_signature(app_secret: str | None, body: bytes, header: str | None) -> bool:
    if not app_secret:
        return True  # sin secreto configurado: la URL secreta del webhook es la única protección
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


def verify_hmac_base64(secret: str | None, body: bytes, header: str | None) -> bool:
    """Shopify y WooCommerce: HMAC-SHA256 en base64."""
    if not secret:
        return True
    if not header:
        return False
    expected = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expected, header)


# ------------------------------------------------------------------ lectura de webhooks
# Cada mensaje entrante se normaliza a: {channel, user_id, name, phone, text, media_kind, media_ref}

def parse_meta(payload: dict) -> list[dict]:
    """Messenger (object=page) e Instagram (object=instagram)."""
    channel = "instagram" if payload.get("object") == "instagram" else "facebook"
    out = []
    for entry in payload.get("entry", []):
        for ev in entry.get("messaging", []):
            msg = ev.get("message") or {}
            if msg.get("is_echo"):
                continue
            item = {"channel": channel, "user_id": ev["sender"]["id"], "name": "", "phone": "",
                    "text": msg.get("text", ""), "media_kind": "", "media_ref": ""}
            if msg.get("attachments"):
                a = msg["attachments"][0]
                kind = a.get("type", "archivo")
                item.update(media_kind=kind, media_ref=(a.get("payload") or {}).get("url", ""))
                item["text"] = (item["text"] + f"\n[El cliente envió un adjunto: {kind}]").strip()
            if not item["text"] and ev.get("postback"):
                item["text"] = ev["postback"].get("title", "")
            if item["text"]:
                out.append(item)
    return out


def parse_tiktok(payload: dict) -> list[dict]:
    """Formato genérico: ajustar a los campos reales que entregue TikTok."""
    out = []
    for ev in payload.get("events", [payload]):
        text = ev.get("text") or (ev.get("message") or {}).get("text")
        user = ev.get("user_id") or ev.get("conversation_id") or (ev.get("sender") or {}).get("id")
        if text and user:
            out.append({"channel": "tiktok", "user_id": str(user), "name": ev.get("display_name", ""), "phone": "",
                        "text": text, "media_kind": "", "media_ref": ""})
    return out
