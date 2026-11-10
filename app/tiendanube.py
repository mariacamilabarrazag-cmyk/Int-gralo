"""Integración con Tiendanube.

Integralo se registra UNA vez como app en el Portal de Socios de Tiendanube (TIENDANUBE_APP_ID y
TIENDANUBE_CLIENT_SECRET). Cada tienda la instala con un clic desde Configuración → Canales:

1. La dueña va a la URL de autorización de Tiendanube y acepta.
2. Tiendanube la devuelve a {PUBLIC_URL}/tiendanube/callback?code=...  (URL que se configura en el Portal de Socios).
3. Cambiamos el `code` por un `access_token` y el `user_id` (= id de la tienda en Tiendanube).
4. Registramos los webhooks de pedidos y productos e importamos el catálogo.

Detalles de la API: el encabezado de autenticación es `Authentication: bearer <token>` (no `Authorization`),
se exige un User-Agent con contacto, y los webhooks llegan firmados con HMAC-SHA256 (hex) del cuerpo
usando el client secret, en el encabezado `x-linkedstore-hmac-sha256`. El webhook solo trae
{store_id, event, id}: el pedido completo se consulta a la API.
"""
import hashlib
import hmac
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import httpx

from . import config, db, excel

log = logging.getLogger("tiendanube")

ORDER_EVENTS = ["order/created", "order/paid", "order/cancelled", "order/fulfilled", "order/updated"]
PRODUCT_EVENTS = ["product/created", "product/updated", "product/deleted"]
APP_EVENTS = ["app/uninstalled"]
PRIVACY_EVENTS = ["store/redact", "customers/redact", "customers/data_request"]  # se configuran en el Portal de Socios


def enabled() -> bool:
    return bool(config.TIENDANUBE_APP_ID and config.TIENDANUBE_CLIENT_SECRET)


def authorize_url(state: str) -> str:
    return config.TIENDANUBE_AUTH_URL.format(app_id=config.TIENDANUBE_APP_ID) + f"?state={state}"


def webhook_url() -> str:
    return f"{config.PUBLIC_URL}/webhooks/tiendanube"


def exchange_code(code: str) -> tuple[str, str]:
    r = httpx.post("https://www.tiendanube.com/apps/authorize/token", timeout=20, json={
        "client_id": config.TIENDANUBE_APP_ID, "client_secret": config.TIENDANUBE_CLIENT_SECRET,
        "grant_type": "authorization_code", "code": code,
    })
    data = r.json()
    if r.status_code >= 400 or "access_token" not in data:
        raise RuntimeError(f"Tiendanube rechazó la autorización: {data}")
    return data["access_token"], str(data["user_id"])


def verify_webhook(body: bytes, header: str | None) -> bool:
    if not config.TIENDANUBE_CLIENT_SECRET or not header:
        return False
    expected = hmac.new(config.TIENDANUBE_CLIENT_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.strip().lower())


# ------------------------------------------------------------------ cliente de la API

class TiendanubeError(RuntimeError):
    pass


def _api(tenant: dict, method: str, path: str, **kw):
    token = db.get_secrets(tenant).get("tiendanube_token")
    store_id = tenant.get("tiendanube_store_id")
    if not (token and store_id):
        raise TiendanubeError("La tienda no tiene Tiendanube conectado.")
    url = f"{config.TIENDANUBE_API_URL}/{store_id}{path}"
    headers = {"Authentication": f"bearer {token}", "User-Agent": config.TIENDANUBE_USER_AGENT,
               "Content-Type": "application/json; charset=utf-8"}
    for attempt in range(4):
        r = httpx.request(method, url, headers=headers, timeout=30, **kw)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise TiendanubeError(f"Tiendanube {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}
    raise TiendanubeError("Tiendanube no respondió (demasiados reintentos).")


def _paginate(tenant: dict, path: str, params: dict | None = None, per_page: int = 50, max_pages: int = 100):
    for page in range(1, max_pages + 1):
        data = _api(tenant, "GET", path, params={**(params or {}), "page": page, "per_page": per_page})
        if not data:
            return
        yield from data
        if len(data) < per_page:
            return


def register_webhooks(tenant: dict) -> list[str]:
    existing = {(w.get("event"), w.get("url")) for w in (_api(tenant, "GET", "/webhooks") or [])}
    created = []
    for event in ORDER_EVENTS + PRODUCT_EVENTS + APP_EVENTS:
        if (event, webhook_url()) not in existing:
            try:
                _api(tenant, "POST", "/webhooks", json={"event": event, "url": webhook_url()})
                created.append(event)
            except TiendanubeError as e:
                log.warning("No se pudo registrar el webhook %s: %s", event, e)
    return created


# ------------------------------------------------------------------ conversión de datos

def _lang(value, default="") -> str:
    """Los textos de Tiendanube vienen por idioma: {"es": "...", "pt": "..."}."""
    if isinstance(value, dict):
        return value.get("es") or value.get("pt") or value.get("en") or next(iter(value.values()), default) or default
    return value or default


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def map_product(p: dict) -> dict:
    variants = p.get("variants") or []
    prices = [_f(v.get("promotional_price") or v.get("price")) for v in variants if v.get("price") is not None]
    stocks = [v.get("stock") for v in variants]
    options = []
    for v in variants:
        vals = " / ".join(_lang(x) for x in (v.get("values") or []))
        if vals:
            options.append(vals + (f" (agotado)" if v.get("stock") == 0 else ""))
    tags = (p.get("tags") or "").lower()
    name = _lang(p.get("name"), "Producto")
    return {
        "external_id": f"tn-{p['id']}",
        "name": name,
        "price": min(prices) if prices else 0,
        "stock": None if not stocks or any(s is None for s in stocks) else sum(stocks),
        "sku": None,
        "description": _strip_html(_lang(p.get("description")))[:600],
        "variants": "; ".join(options)[:800],
        "image_url": ((p.get("images") or [{}])[0] or {}).get("src", ""),
        "url": p.get("canonical_url") or "",
        "personalizable": "personaliz" in tags or "personaliz" in name.lower(),
        "active": bool(p.get("published", True)),
    }


def map_order(o: dict) -> dict:
    ship = o.get("shipping_address") or {}
    cust = o.get("customer") or {}
    total, subtotal, discount = _f(o.get("total")), _f(o.get("subtotal")), _f(o.get("discount"))
    shipping = _f(o.get("shipping_cost_customer")) if o.get("shipping_cost_customer") not in (None, "") \
        else max(total - subtotal + discount, 0)
    address = " ".join(str(x) for x in [ship.get("address"), ship.get("number"), ship.get("floor")] if x)
    if ship.get("locality"):
        address += f", {ship['locality']}"
    method = (o.get("payment_details") or {}).get("method") or o.get("gateway_name") or o.get("gateway") or ""
    notes = f"Tiendanube #{o.get('number', o.get('id'))}"
    if o.get("note"):
        notes += f" · Nota del cliente: {o['note']}"
    return {
        "source": "web",
        "external_id": f"tn-{o['id']}",
        "customer_name": o.get("contact_name") or ship.get("name") or cust.get("name") or "",
        "phone": o.get("contact_phone") or ship.get("phone") or cust.get("phone") or "",
        "email": o.get("contact_email") or cust.get("email") or "",
        "address": address,
        "city": ", ".join(x for x in [ship.get("city"), ship.get("province")] if x),
        "notes": notes,
        "shipping": shipping,
        "total": total,
        "payment_status": "pagado" if o.get("payment_status") == "paid" else "pendiente",
        "payment_method": str(method),
        "status": "cancelado" if o.get("status") == "cancelled" else "recibido",
        "items": [{
            "product_name": li.get("name_without_variants") or li.get("name") or "Producto",
            "sku": li.get("sku") or "",
            "details": ", ".join(_lang(v) for v in (li.get("variant_values") or [])) if li.get("name_without_variants") else "",
            "quantity": int(li.get("quantity") or 1),
            "unit_price": _f(li.get("price")),
        } for li in o.get("products", [])],
    }


# ------------------------------------------------------------------ sincronización

def sync_products(tenant: dict) -> int:
    n = 0
    for p in _paginate(tenant, "/products"):
        db.upsert_product(tenant["id"], map_product(p))
        n += 1
    return n


def save_order(tenant: dict, tn_order: dict) -> dict:
    """Crea el pedido si no existe y sincroniza pago/cancelación si ya existía."""
    data = map_order(tn_order)
    tid = tenant["id"]
    order, created = db.create_order(tid, data)
    if not created:
        if data["payment_status"] == "pagado" and order["payment_status"] != "pagado":
            order = db.update_payment(tid, order["id"], "pagado", data["payment_method"])
        if data["status"] == "cancelado" and order["status"] != "cancelado":
            order = db.update_status(tid, order["id"], "cancelado", "Cancelado en Tiendanube")
        if tn_order.get("shipping_status") == "shipped" and order["status"] in ("recibido", "preparacion"):
            order = db.update_status(tid, order["id"], "reparto", "Despachado en Tiendanube")
    return order


def import_recent_orders(tenant: dict, days: int = 30) -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S%z")
    n = 0
    for o in _paginate(tenant, "/orders", {"created_at_min": since}):
        save_order(tenant, o)
        n += 1
    excel.safe_sync(tenant["id"])
    return n


def handle_webhook(payload: dict) -> None:
    """Procesa un webhook ya verificado. Se ejecuta en segundo plano (Tiendanube corta a los pocos segundos)."""
    event, store_id, obj_id = payload.get("event", ""), payload.get("store_id"), payload.get("id")
    tenant = db.get_tenant_by("tiendanube_store_id", store_id)
    if not tenant:
        log.warning("Webhook de Tiendanube de una tienda desconocida: %s", store_id)
        return
    tid = tenant["id"]
    try:
        if event.startswith("order/") and obj_id:
            o = _api(tenant, "GET", f"/orders/{obj_id}")
            if o:
                order = save_order(tenant, o)
                log.info("Tiendanube %s → %s (tienda %s)", event, order["code"], tid)
                excel.safe_sync(tid)
        elif event in ("product/created", "product/updated") and obj_id:
            p = _api(tenant, "GET", f"/products/{obj_id}")
            if p:
                db.upsert_product(tid, map_product(p))
        elif event == "product/deleted" and obj_id:
            for p in db.list_products(tid):
                if p["external_id"] == f"tn-{obj_id}":
                    db.delete_product(tid, p["id"])
        elif event in ("app/uninstalled", "store/redact"):
            db.set_secrets(tid, {"tiendanube_token": ""})
            db.update_tenant(tid, {"tiendanube_store_id": None})
        elif event in ("customers/redact", "customers/data_request"):
            log.info("Solicitud de privacidad %s recibida para la tienda %s", event, tid)
    except TiendanubeError:
        log.exception("Error procesando webhook %s", event)
