"""Mensajería por canal (WhatsApp, Messenger, Instagram, TikTok) usando las credenciales de cada tienda."""
import base64
import hashlib
import hmac
import logging

import httpx

from . import config, db

log = logging.getLogger("channels")
GRAPH = f"https://graph.facebook.com/{config.GRAPH_VERSION}"


# ------------------------------------------------------------------ envío

def send_message(tenant: dict, channel: str, external_user_id: str, text: str) -> bool:
    """Envía `text` al cliente. Devuelve True si se entregó a la red social (o si es el simulador)."""
    s = db.get_secrets(tenant)
    try:
        if channel == "whatsapp":
            return _send_whatsapp(s, external_user_id, text)
        if channel in ("facebook", "instagram"):
            return _send_meta(s, external_user_id, text)
        if channel == "tiktok":
            return _send_tiktok(s, external_user_id, text)
        return channel == "simulador"
    except Exception:
        log.exception("Error enviando mensaje por %s (tienda %s)", channel, tenant["id"])
        return False


def _send_whatsapp(s: dict, to: str, text: str) -> bool:
    if not (s.get("whatsapp_token") and s.get("whatsapp_phone_number_id")):
        return False
    r = httpx.post(f"{GRAPH}/{s['whatsapp_phone_number_id']}/messages", timeout=20,
                   headers={"Authorization": f"Bearer {s['whatsapp_token']}"},
                   json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text[:4096]}})
    if r.status_code >= 400:
        log.error("WhatsApp respondió %s: %s", r.status_code, r.text[:500])
    return r.status_code < 400


def _send_meta(s: dict, recipient_id: str, text: str) -> bool:
    if not s.get("meta_page_token"):
        return False
    r = httpx.post(f"{GRAPH}/me/messages", timeout=20, params={"access_token": s["meta_page_token"]},
                   json={"recipient": {"id": recipient_id}, "messaging_type": "RESPONSE", "message": {"text": text[:1000]}})
    if r.status_code >= 400:
        log.error("Meta respondió %s: %s", r.status_code, r.text[:500])
    return r.status_code < 400


def _send_tiktok(s: dict, conversation_id: str, text: str) -> bool:
    """TikTok Business Messaging solo está disponible para cuentas aprobadas. Completar con la
    documentación que entregue TikTok al aprobar el acceso; mientras tanto se responde desde la app de TikTok."""
    if s.get("tiktok_access_token"):
        log.warning("Envío a TikTok pendiente de implementar con el acceso aprobado de TikTok.")
    return False


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

def parse_whatsapp(payload: dict) -> list[dict]:
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): c.get("profile", {}).get("name", "") for c in value.get("contacts", [])}
            for m in value.get("messages", []):
                msg = {"channel": "whatsapp", "user_id": m["from"], "name": names.get(m["from"], ""),
                       "phone": m["from"], "text": "", "media_kind": "", "media_ref": ""}
                t = m.get("type")
                if t == "text":
                    msg["text"] = m["text"]["body"]
                elif t == "button":
                    msg["text"] = m["button"].get("text", "")
                elif t == "interactive":
                    i = m["interactive"]
                    msg["text"] = (i.get("button_reply") or i.get("list_reply") or {}).get("title", "")
                elif t in ("image", "document", "video", "sticker"):
                    media = m.get(t, {})
                    msg.update(media_kind=t, media_ref=f"wa:{media.get('id', '')}")
                    caption = media.get("caption") or media.get("filename") or ""
                    label = {"image": "una imagen", "document": "un documento", "video": "un video", "sticker": "un sticker"}[t]
                    msg["text"] = f"[El cliente envió {label}] {caption}".strip()
                elif t == "audio":
                    msg.update(media_kind="audio", media_ref=f"wa:{m['audio'].get('id', '')}",
                               text="[El cliente envió una nota de voz]")
                elif t == "location":
                    loc = m["location"]
                    msg["text"] = f"[Ubicación] {loc.get('address') or ''} ({loc.get('latitude')}, {loc.get('longitude')})"
                if msg["text"]:
                    out.append(msg)
    return out


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
