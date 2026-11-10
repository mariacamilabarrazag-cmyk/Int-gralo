"""WhatsApp: conexión con un clic (Embedded Signup), coexistencia con la app de WhatsApp Business
y procesamiento de los webhooks.

Coexistencia = el MISMO número funciona a la vez en la app de WhatsApp Business del celular y en
Integralo (Cloud API). Meta envía, además de los mensajes de los clientes (`messages`):

  smb_message_echoes   lo que la tienda responde DESDE EL CELULAR → se guarda en la conversación y
                       la IA hace una pausa en ese chat para no hablar encima de la persona.
  history              el historial de chats (hasta ~6 meses), por partes, una vez tras conectar.
  smb_app_state_sync   la libreta de contactos del celular → nombres de los clientes.
  account_update       p. ej. PARTNER_REMOVED: la tienda desconectó Integralo desde el celular.

Tras conectar en modo coexistencia hay que pedir UNA vez cada sincronización (contactos e
historial) dentro de las 24 horas siguientes (POST /{phone_number_id}/smb_app_data).
En coexistencia no se llama a /register (el número ya vive en la app).
"""
import json
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timedelta

import httpx

from . import config, db

log = logging.getLogger("whatsapp")
GRAPH = f"https://graph.facebook.com/{config.GRAPH_VERSION}"
SYNC_TYPES = ("smb_app_state_sync", "history")
HISTORY_DECLINED = 2593109        # la tienda decidió no compartir el historial
REENGAGEMENT_ERROR = 131047       # pasaron más de 24 h desde el último mensaje del cliente


class MetaError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


def platform_enabled() -> bool:
    """¿Está configurada la app de Integralo en Meta para conectar WhatsApp con un clic?"""
    return bool(config.META_APP_ID and config.META_APP_SECRET and config.META_CONFIG_ID)


def signup_settings() -> dict:
    try:
        extras = json.loads(config.META_SIGNUP_EXTRAS or "{}")
    except ValueError:
        extras = {}
    return {"enabled": platform_enabled(), "app_id": config.META_APP_ID, "config_id": config.META_CONFIG_ID,
            "graph_version": config.GRAPH_VERSION, "extras": extras}


def digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _ts(value) -> str:
    try:
        return datetime.fromtimestamp(int(value)).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return db.now()


# ======================================================================= Graph API

def _graph(method: str, path: str, token: str | None = None, **kw) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = httpx.request(method, f"{GRAPH}/{path.lstrip('/')}", headers=headers, timeout=30, **kw)
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400 or "error" in data:
        err = data.get("error") or {}
        raise MetaError(err.get("error_user_msg") or err.get("message") or f"Meta respondió {r.status_code}", err.get("code"))
    return data


def _expires(data: dict) -> str | None:
    secs = int(data.get("expires_in") or 0)
    return (datetime.now() + timedelta(seconds=secs)).isoformat(timespec="seconds") if secs else None


def exchange_code(code: str) -> tuple[str, str | None]:
    data = _graph("GET", "oauth/access_token", params={
        "client_id": config.META_APP_ID, "client_secret": config.META_APP_SECRET, "code": code})
    if not data.get("access_token"):
        raise MetaError("Meta no devolvió un token. El código es de un solo uso: vuelve a abrir la ventana de conexión.")
    return data["access_token"], _expires(data)


def refresh_token(token: str) -> tuple[str, str | None]:
    data = _graph("GET", "oauth/access_token", params={
        "grant_type": "fb_exchange_token", "client_id": config.META_APP_ID,
        "client_secret": config.META_APP_SECRET, "fb_exchange_token": token})
    return data["access_token"], _expires(data)


def list_phone_numbers(waba_id: str, token: str) -> list[dict]:
    data = _graph("GET", f"{waba_id}/phone_numbers", token,
                  params={"fields": "id,display_phone_number,verified_name,is_on_biz_app,platform_type"})
    return data.get("data") or []


def phone_info(phone_number_id: str, token: str) -> dict:
    return _graph("GET", phone_number_id, token, params={"fields": "display_phone_number,verified_name"})


# ======================================================================= conexión con un clic

def connect_embedded(tenant: dict, code: str, phone_number_id: str | None, waba_id: str | None,
                     coexistence: bool) -> dict:
    """Completa la conexión después de que la dueña termina la ventana de Meta."""
    tid = tenant["id"]
    if not waba_id:
        raise MetaError("Meta no informó la cuenta de WhatsApp Business. Vuelve a intentarlo.")
    token, expires_at = exchange_code(code)

    if not phone_number_id:  # algunas versiones del flujo de coexistencia solo envían la cuenta
        numbers = list_phone_numbers(waba_id, token)
        pick = next((n for n in numbers if n.get("is_on_biz_app")), None) or (numbers[0] if numbers else None)
        if not pick:
            raise MetaError("La cuenta de WhatsApp Business no tiene números.")
        phone_number_id = pick["id"]
        coexistence = coexistence or bool(pick.get("is_on_biz_app"))

    other = db.get_tenant_by("whatsapp_phone_number_id", phone_number_id)
    if other and other["id"] != tid:
        raise MetaError("Ese número ya está conectado a otra cuenta de Integralo.")

    _graph("POST", f"{waba_id}/subscribed_apps", token)  # que los webhooks del número lleguen a Integralo

    if not coexistence:
        # Número nuevo solo para la API: hay que registrarlo con un PIN de verificación en dos pasos.
        pin = db.get_secrets(tenant).get("whatsapp_pin") or f"{secrets.randbelow(10**6):06d}"
        try:
            _graph("POST", f"{phone_number_id}/register", token, json={"messaging_product": "whatsapp", "pin": pin})
        except MetaError as e:
            if "already" not in str(e).lower():
                raise
        db.set_secrets(tid, {"whatsapp_pin": pin})

    info = {}
    try:
        info = phone_info(phone_number_id, token)
    except MetaError:
        pass
    db.set_secrets(tid, {"whatsapp_token": token, "whatsapp_phone_number_id": phone_number_id})
    db.update_tenant(tid, {
        "whatsapp_phone_number_id": phone_number_id, "whatsapp_waba_id": waba_id,
        "whatsapp_display_phone": info.get("display_phone_number", ""),
        "whatsapp_mode": "coexistencia" if coexistence else "api",
        "whatsapp_connected_at": db.now(), "whatsapp_token_expires_at": expires_at,
        "smb_sync": "{}",
    })
    if coexistence:
        threading.Thread(target=run_coexistence_sync, args=(tid,), daemon=True).start()
    return {"modo": "coexistencia" if coexistence else "api", "numero": info.get("display_phone_number", "")}


def sync_state(tenant: dict) -> dict:
    try:
        return json.loads(tenant.get("smb_sync") or "{}")
    except ValueError:
        return {}


def sync_window_open(tenant: dict) -> bool:
    at = tenant.get("whatsapp_connected_at")
    return bool(at) and datetime.fromisoformat(at) > datetime.now() - timedelta(hours=24)


def run_coexistence_sync(tid: int) -> dict:
    """Pide a Meta los contactos y el historial del celular. Meta acepta cada tipo UNA sola vez, dentro
    de las 24 h siguientes a la conexión; los datos llegan después por webhook."""
    tenant = db.get_tenant(tid)
    if tenant.get("whatsapp_mode") != "coexistencia":
        return {}
    state = sync_state(tenant)
    s = db.get_secrets(tenant)
    for sync_type in SYNC_TYPES:
        if state.get(sync_type, {}).get("ok"):
            continue  # ya se pidió: no repetir
        if not sync_window_open(tenant):
            state[sync_type] = {"ok": False, "error": "Pasaron las 24 horas para sincronizar."}
            continue
        try:
            data = _graph("POST", f"{s['whatsapp_phone_number_id']}/smb_app_data", s["whatsapp_token"],
                          json={"messaging_product": "whatsapp", "sync_type": sync_type})
            state[sync_type] = {"ok": True, "at": db.now(), "request_id": data.get("request_id")}
        except (MetaError, httpx.HTTPError, KeyError) as e:
            log.warning("Sincronización %s falló (tienda %s): %s", sync_type, tid, e)
            state[sync_type] = {"ok": False, "error": str(e), "at": db.now()}
        db.update_tenant(tid, {"smb_sync": json.dumps(state)})
    db.update_tenant(tid, {"smb_sync": json.dumps(state)})
    return state


def disconnect(tid: int) -> None:
    db.set_secrets(tid, {"whatsapp_token": "", "whatsapp_phone_number_id": ""})
    db.update_tenant(tid, {"whatsapp_phone_number_id": None, "whatsapp_waba_id": None, "whatsapp_mode": "",
                           "whatsapp_display_phone": "", "whatsapp_token_expires_at": None})


def refresh_expiring_tokens() -> None:
    """Renueva los tokens de negocio antes de que venzan (se ejecuta en segundo plano cada 12 h)."""
    soon = (datetime.now() + timedelta(days=15)).isoformat(timespec="seconds")
    for t in db.tenants_with_whatsapp_token_expiring(soon):
        try:
            token, expires_at = refresh_token(db.get_secrets(t)["whatsapp_token"])
            db.set_secrets(t["id"], {"whatsapp_token": token})
            db.update_tenant(t["id"], {"whatsapp_token_expires_at": expires_at})
            log.info("Token de WhatsApp renovado (tienda %s)", t["id"])
        except Exception:
            log.exception("No se pudo renovar el token de WhatsApp de la tienda %s", t["id"])


def start_token_refresher() -> None:
    def loop():
        while True:
            if platform_enabled():
                refresh_expiring_tokens()
            time.sleep(12 * 3600)
    threading.Thread(target=loop, daemon=True).start()


# ======================================================================= webhooks

def content_text(m: dict) -> tuple[str, str, str]:
    """(texto, tipo_de_adjunto, referencia_del_adjunto) de un mensaje de WhatsApp."""
    t = m.get("type") or ""
    if t == "text":
        return (m.get("text") or {}).get("body", ""), "", ""
    if t in ("image", "document", "video", "sticker", "audio"):
        media = m.get(t) or {}
        label = {"image": "una imagen", "document": "un documento", "video": "un video",
                 "sticker": "un sticker", "audio": "una nota de voz"}[t]
        caption = media.get("caption") or media.get("filename") or ""
        return f"[Envió {label}] {caption}".strip(), t, f"wa:{media['id']}" if media.get("id") else ""
    if t == "button":
        return (m.get("button") or {}).get("text", ""), "", ""
    if t == "interactive":
        i = m.get("interactive") or {}
        return (i.get("button_reply") or i.get("list_reply") or {}).get("title", ""), "", ""
    if t == "location":
        loc = m.get("location") or {}
        return f"[Ubicación] {loc.get('address') or ''} ({loc.get('latitude')}, {loc.get('longitude')})", "", ""
    if t == "media_placeholder":
        return "[Archivo]", "", ""
    return f"[{t or 'mensaje'}]", "", ""


def handle_change(tenant: dict, field: str, value: dict) -> None:
    """Procesa un cambio del webhook de WhatsApp de una tienda. Nunca lanza excepciones."""
    try:
        if field == "messages":
            _on_messages(tenant, value)
        elif field == "smb_message_echoes":
            _on_echoes(tenant, value)
        elif field == "history":
            _on_history(tenant, value)
        elif field == "smb_app_state_sync":
            _on_contacts(tenant, value)
        elif field == "account_update":
            if (value.get("event") or "").upper() == "PARTNER_REMOVED":
                log.warning("La tienda %s desconectó Integralo desde WhatsApp", tenant["id"])
                disconnect(tenant["id"])
    except Exception:
        log.exception("Error procesando webhook de WhatsApp '%s' (tienda %s)", field, tenant["id"])


def _on_messages(tenant: dict, value: dict) -> None:
    from . import service  # import tardío: service importa este módulo
    names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in value.get("contacts", [])}
    for m in value.get("messages", []):
        text, kind, ref = content_text(m)
        if not text:
            continue
        service.handle_incoming(tenant, {"channel": "whatsapp", "user_id": m["from"], "name": names.get(m["from"], ""),
                                         "phone": m["from"], "text": text, "media_kind": kind, "media_ref": ref,
                                         "external_id": m.get("id")})


def _on_echoes(tenant: dict, value: dict) -> None:
    """Lo que la tienda escribió desde la app del celular."""
    from . import service
    tid = tenant["id"]
    for e in value.get("message_echoes", []):
        wamid, phone, kind = e.get("id"), digits(e.get("to")), e.get("type")
        if not wamid or len(phone) < 7:
            continue
        if kind in ("edit", "revoke"):
            original = (e.get(kind) or {}).get("original_message_id")
            if original:
                new_text = "🚫 Mensaje eliminado" if kind == "revoke" else \
                    content_text((e.get("edit") or {}).get("message") or {})[0] + " (editado)"
                db.update_message_text_by_external(tid, original, new_text)
            continue
        text, mkind, ref = content_text(e)
        conv = db.get_or_create_conversation(tid, "whatsapp", phone, phone=phone,
                                             ai_enabled=bool(tenant["ai_enabled_default"]))
        saved = db.add_message(tid, conv["id"], "out", "celular", text, mkind, ref, external_id=wamid)
        if saved:  # si ya existía, era un mensaje que envió el propio Integralo
            service.pause_ai(tenant, conv["id"])


def _on_history(tenant: dict, value: dict) -> None:
    tid = tenant["id"]
    business = digits((value.get("metadata") or {}).get("display_phone_number") or tenant.get("whatsapp_display_phone"))
    imported = 0
    for item in value.get("history", []):
        if item.get("errors"):
            code = (item["errors"][0] or {}).get("code")
            state = sync_state(db.get_tenant(tid))
            state["history_result"] = "rechazado" if code == HISTORY_DECLINED else f"error {code}"
            db.update_tenant(tid, {"smb_sync": json.dumps(state)})
            continue
        for thread in item.get("threads", []):
            phone = digits(thread.get("id"))
            if len(phone) < 7:
                continue
            conv = db.get_or_create_conversation(tid, "whatsapp", phone, phone=phone,
                                                 ai_enabled=bool(tenant["ai_enabled_default"]))
            for m in thread.get("messages", []):
                if not m.get("id"):
                    continue
                from_business = bool(m.get("to")) or (business and digits(m.get("from")) == business)
                text, kind, ref = content_text(m)
                if db.add_message(tid, conv["id"], "out" if from_business else "in",
                                  "celular" if from_business else "cliente", text, kind, ref,
                                  external_id=m["id"], created_at=_ts(m.get("timestamp")), history=True):
                    imported += 1
    if imported:
        log.info("Historial de WhatsApp: %s mensajes importados (tienda %s)", imported, tid)


def _on_contacts(tenant: dict, value: dict) -> None:
    for item in value.get("state_sync", []):
        contact = item.get("contact") or {}
        phone = digits(contact.get("phone_number"))
        if len(phone) < 7:
            continue
        if item.get("action") == "remove":
            db.delete_contact(tenant["id"], "whatsapp", phone)
        else:
            name = (contact.get("full_name") or contact.get("first_name") or "").strip()
            if name:
                db.save_contact(tenant["id"], "whatsapp", phone, name)


def route_platform_change(entry: dict, change: dict) -> dict | None:
    """Webhook de la app de la plataforma: encuentra la tienda por el número (o por la cuenta WABA)."""
    value = change.get("value") or {}
    pid = (value.get("metadata") or {}).get("phone_number_id")
    tenant = db.get_tenant_by("whatsapp_phone_number_id", pid) if pid else None
    return tenant or db.get_tenant_by("whatsapp_waba_id", entry.get("id"))
