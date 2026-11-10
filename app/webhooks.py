"""Webhooks entrantes.

Cada tienda tiene una URL secreta propia: /w/<token-de-la-tienda>/<canal>. El token identifica la tienda y,
además, cuando la tienda configura los secretos de cada plataforma, se valida la firma de cada mensaje.
Tiendanube usa una única URL para toda la plataforma (la tienda se identifica por su store_id).
"""
import logging
import secrets

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import PlainTextResponse

from . import channels, config, db, excel, service, tiendanube, web_orders, whatsapp

log = logging.getLogger("webhooks")
router = APIRouter()


def _tenant(token: str) -> tuple[dict, dict]:
    t = db.get_tenant_by("webhook_token", token)
    if not t:
        raise HTTPException(404, "No encontrado")
    return t, db.get_secrets(t)


# ------------------------------------------------------------------ Meta: WhatsApp, Messenger, Instagram

@router.get("/w/{token}/whatsapp")
@router.get("/w/{token}/meta")
def meta_verify(token: str, request: Request):
    t, s = _tenant(token)
    p = request.query_params
    expected = s.get("meta_verify_token") or token
    if p.get("hub.mode") == "subscribe" and secrets.compare_digest(p.get("hub.verify_token", ""), expected):
        return PlainTextResponse(p.get("hub.challenge", ""))
    raise HTTPException(403, "Token de verificación incorrecto")


@router.post("/w/{token}/whatsapp")
@router.post("/w/{token}/meta")
async def meta_webhook(token: str, request: Request, background: BackgroundTasks):
    """Webhook de una tienda que conectó su propia app de Meta (conexión manual)."""
    t, s = _tenant(token)
    body = await request.body()
    if not channels.verify_meta_signature(s.get("meta_app_secret"), body, request.headers.get("x-hub-signature-256")):
        raise HTTPException(401, "Firma inválida")
    payload = await request.json()
    if payload.get("object") == "whatsapp_business_account":
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                background.add_task(whatsapp.handle_change, t, change.get("field", ""), change.get("value") or {})
    else:
        for m in channels.parse_meta(payload):
            service.handle_incoming(t, m)
    return {"ok": True}


# ------------------------------------------------------------------ WhatsApp de la plataforma (conexión con un clic)

@router.get("/webhooks/meta")
def platform_meta_verify(request: Request):
    p = request.query_params
    if (config.META_VERIFY_TOKEN and p.get("hub.mode") == "subscribe"
            and secrets.compare_digest(p.get("hub.verify_token", ""), config.META_VERIFY_TOKEN)):
        return PlainTextResponse(p.get("hub.challenge", ""))
    raise HTTPException(403, "Token de verificación incorrecto")


@router.post("/webhooks/meta")
async def platform_meta_webhook(request: Request, background: BackgroundTasks):
    """Una sola URL para todas las tiendas conectadas con la app de Integralo: cada cambio se
    enruta a la tienda dueña del número."""
    if not config.META_APP_SECRET:
        raise HTTPException(404, "No encontrado")
    body = await request.body()
    if not channels.verify_meta_signature(config.META_APP_SECRET, body, request.headers.get("x-hub-signature-256")):
        raise HTTPException(401, "Firma inválida")
    payload = await request.json()
    if payload.get("object") != "whatsapp_business_account":
        return {"ok": True}
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            tenant = whatsapp.route_platform_change(entry, change)
            if tenant:
                background.add_task(whatsapp.handle_change, tenant, change.get("field", ""), change.get("value") or {})
            else:
                log.warning("Webhook de WhatsApp sin tienda asociada (campo %s)", change.get("field"))
    return {"ok": True}


@router.post("/w/{token}/tiktok")
async def tiktok_webhook(token: str, request: Request):
    t, s = _tenant(token)
    body = await request.body()
    if s.get("tiktok_webhook_secret") and not channels.verify_hmac_base64(
            s["tiktok_webhook_secret"], body, request.headers.get("x-tiktok-signature")):
        raise HTTPException(401, "Firma inválida")
    for m in channels.parse_tiktok(await request.json()):
        service.handle_incoming(t, m)
    return {"ok": True}


# ------------------------------------------------------------------ páginas web

def _save(t: dict, data: dict) -> dict:
    order, created = db.create_order(t["id"], data)
    excel.safe_sync(t["id"])
    if created:
        log.info("Pedido web %s (tienda %s)", order["code"], t["id"])
    return {"ok": True, "codigo": order["code"]}


@router.post("/w/{token}/shopify")
async def shopify_webhook(token: str, request: Request):
    t, s = _tenant(token)
    body = await request.body()
    if not channels.verify_hmac_base64(s.get("shopify_secret"), body, request.headers.get("x-shopify-hmac-sha256")):
        raise HTTPException(401, "Firma inválida")
    return _save(t, web_orders.from_shopify(await request.json()))


@router.post("/w/{token}/woocommerce")
async def woocommerce_webhook(token: str, request: Request):
    t, s = _tenant(token)
    body = await request.body()
    if not body or body.startswith(b"webhook_id="):  # ping de prueba de WooCommerce
        return {"ok": True}
    if not channels.verify_hmac_base64(s.get("woocommerce_secret"), body, request.headers.get("x-wc-webhook-signature")):
        raise HTTPException(401, "Firma inválida")
    return _save(t, web_orders.from_woocommerce(await request.json()))


@router.post("/w/{token}/web")
async def generic_web_webhook(token: str, request: Request):
    t, s = _tenant(token)
    if s.get("web_token") and not secrets.compare_digest(request.headers.get("x-token", ""), s["web_token"]):
        raise HTTPException(401, "Token inválido")
    return _save(t, web_orders.from_generic(await request.json()))


# ------------------------------------------------------------------ Tiendanube (una URL para toda la plataforma)

@router.post("/webhooks/tiendanube")
async def tiendanube_webhook(request: Request, background: BackgroundTasks):
    body = await request.body()
    if not tiendanube.verify_webhook(body, request.headers.get("x-linkedstore-hmac-sha256")):
        raise HTTPException(401, "Firma inválida")
    background.add_task(tiendanube.handle_webhook, await request.json())
    return {"ok": True}
