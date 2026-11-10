"""Integralo: servidor principal (cuentas, dashboard, API y webhooks)."""
import logging
import re
import shutil
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import channels, config, db, excel, llm, secretary, security, service, tiendanube, webhooks, whatsapp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

app = FastAPI(title="Integralo", docs_url=None, redoc_url=None)
STATIC = config.BASE_DIR / "static"
COOKIE = "integralo_session"
app.include_router(webhooks.router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.on_event("startup")
def _startup():
    db.init_db()
    whatsapp.start_token_refresher()
    if not llm.available():
        log.warning("ANTHROPIC_API_KEY no configurada: la IA no responderá.")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


# =========================================================================== sesión

@dataclass
class Ctx:
    user: dict
    tenant: dict

    @property
    def tid(self) -> int:
        return self.tenant["id"]


def current(request: Request) -> Ctx:
    user = db.get_session_user(request.cookies.get(COOKIE, ""))
    if not user:
        raise HTTPException(401, "Inicia sesión")
    return Ctx(user, db.get_tenant(user["tenant_id"]))


def owner_only(ctx: Ctx = Depends(current)) -> Ctx:
    if ctx.user["role"] != "dueno":
        raise HTTPException(403, "Solo la dueña o el dueño de la cuenta puede cambiar esto")
    return ctx


_attempts: dict[str, list[float]] = defaultdict(list)


def _rate_limit(key: str, limit: int = 10, window: int = 600) -> None:
    now = time.time()
    _attempts[key] = [t for t in _attempts[key] if now - t < window]
    if len(_attempts[key]) >= limit:
        raise HTTPException(429, "Demasiados intentos. Espera unos minutos.")
    _attempts[key].append(now)


def _login_response(user_id: int) -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.set_cookie(COOKIE, db.create_session(user_id), max_age=config.SESSION_DAYS * 86400, httponly=True,
                    samesite="lax", secure=config.COOKIE_SECURE)
    return resp


# =========================================================================== páginas

@app.get("/")
def index(request: Request):
    if not db.get_session_user(request.cookies.get(COOKIE, "")):
        return RedirectResponse("/login")
    return FileResponse(STATIC / "index.html")


@app.get("/salud")
def health():
    return {"ok": True}


@app.get("/login")
@app.get("/registro")
def login_page():
    return FileResponse(STATIC / "login.html")


@app.get("/logo/{tid}")
def logo(tid: int):
    t = db.get_tenant(tid)
    if not t or not t["logo_file"]:
        raise HTTPException(404)
    path = config.TENANTS_DIR / str(tid) / t["logo_file"]
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=300"})


# =========================================================================== autenticación

class LoginIn(BaseModel):
    email: str
    password: str


class RegisterIn(BaseModel):
    business_name: str = Field(min_length=2, max_length=80)
    name: str = Field(min_length=1, max_length=80)
    email: str
    password: str = Field(min_length=8)


@app.post("/auth/login")
def login(body: LoginIn, request: Request):
    _rate_limit("login:" + (request.client.host if request.client else "?"))
    user = db.get_user_by_email(body.email)
    if not user or not security.verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Correo o contraseña incorrectos")
    return _login_response(user["id"])


@app.post("/auth/register")
def register(body: RegisterIn, request: Request):
    if not config.ALLOW_SIGNUP:
        raise HTTPException(403, "El registro está cerrado. Pide acceso a Integralo.")
    _rate_limit("register:" + (request.client.host if request.client else "?"), limit=5)
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", body.email):
        raise HTTPException(400, "Correo inválido")
    if db.get_user_by_email(body.email):
        raise HTTPException(400, "Ya existe una cuenta con ese correo")
    tenant = db.create_tenant(body.business_name.strip())
    user = db.create_user(tenant["id"], body.email, body.name, body.password, role="dueno")
    return _login_response(user["id"])


@app.post("/auth/logout")
def logout(request: Request):
    db.delete_session(request.cookies.get(COOKIE, ""))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


class PasswordIn(BaseModel):
    current: str
    new: str = Field(min_length=8)


@app.post("/api/me/password")
def change_password(body: PasswordIn, ctx: Ctx = Depends(current)):
    user = db.get_user_by_email(ctx.user["email"])
    if not security.verify_password(body.current, user["password_hash"]):
        raise HTTPException(400, "La contraseña actual no es correcta")
    db.set_password(user["id"], body.new)
    return _login_response(user["id"])


# =========================================================================== cuenta y configuración

def public_tenant(t: dict) -> dict:
    s = db.get_secrets(t)
    base = f"{config.PUBLIC_URL}/w/{t['webhook_token']}"
    return {
        "id": t["id"], "slug": t["slug"], "name": t["name"], "assistant_name": t["assistant_name"],
        "owner_title": t["owner_title"], "brand_primary": t["brand_primary"], "brand_secondary": t["brand_secondary"],
        "logo_url": f"/logo/{t['id']}?v={t['logo_file']}" if t["logo_file"] else "",
        "currency": t["currency"], "business_info": t["business_info"], "ai_enabled_default": bool(t["ai_enabled_default"]),
        "webhooks": {k: f"{base}/{k}" for k in ("whatsapp", "meta", "tiktok", "shopify", "woocommerce", "web")},
        "meta_verify_token": s.get("meta_verify_token") or t["webhook_token"],
        "secrets_set": {k: bool(s.get(k)) for k in db.SECRET_KEYS},
        "human_pause_minutes": t["human_pause_minutes"], "ai_reply_delay_seconds": t["ai_reply_delay_seconds"],
        "whatsapp": {
            "mode": t["whatsapp_mode"] or ("manual" if s.get("whatsapp_token") else ""),
            "display_phone": t["whatsapp_display_phone"], "connected_at": t["whatsapp_connected_at"],
            "sync": whatsapp.sync_state(t), "sync_window_open": whatsapp.sync_window_open(t),
        },
        "channels": {
            "whatsapp": bool(s.get("whatsapp_token") and s.get("whatsapp_phone_number_id")),
            "facebook_instagram": bool(s.get("meta_page_token")),
            "tiktok": bool(s.get("tiktok_access_token")),
            "tiendanube": bool(t["tiendanube_store_id"] and s.get("tiendanube_token")),
        },
        "tiendanube_store_id": t["tiendanube_store_id"],
    }


@app.get("/api/me")
def me(ctx: Ctx = Depends(current)):
    return {"user": ctx.user, "tenant": public_tenant(ctx.tenant), "ia_disponible": llm.available(),
            "tiendanube_disponible": tiendanube.enabled(), "whatsapp_signup": whatsapp.signup_settings(),
            "estados": db.STATUS_LABELS}


class SettingsIn(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=80)
    assistant_name: str | None = Field(None, min_length=1, max_length=40)
    owner_title: str | None = Field(None, max_length=40)
    brand_primary: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    brand_secondary: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    business_info: str | None = Field(None, max_length=20000)
    ai_enabled_default: bool | None = None
    human_pause_minutes: int | None = Field(None, ge=0, le=1440)
    ai_reply_delay_seconds: int | None = Field(None, ge=0, le=600)


@app.put("/api/settings")
def save_settings(body: SettingsIn, ctx: Ctx = Depends(owner_only)):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "ai_enabled_default" in fields:
        fields["ai_enabled_default"] = int(fields["ai_enabled_default"])
    return public_tenant(db.update_tenant(ctx.tid, fields))


@app.post("/api/settings/secrets")
def save_secrets(body: dict, ctx: Ctx = Depends(owner_only)):
    db.set_secrets(ctx.tid, {k: v for k, v in body.items() if k in db.SECRET_KEYS and k != "tiendanube_token"})
    return public_tenant(db.get_tenant(ctx.tid))


@app.post("/api/settings/logo")
def upload_logo(file: UploadFile, ctx: Ctx = Depends(owner_only)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".svg"):
        raise HTTPException(400, "Sube una imagen PNG, JPG, WEBP o SVG")
    data = file.file.read(2_000_001)
    if len(data) > 2_000_000:
        raise HTTPException(400, "La imagen debe pesar menos de 2 MB")
    d = config.TENANTS_DIR / str(ctx.tid)
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("logo*"):
        old.unlink()
    name = f"logo-{int(time.time())}{ext}"
    (d / name).write_bytes(data)
    return public_tenant(db.update_tenant(ctx.tid, {"logo_file": name}))


class UserIn(BaseModel):
    name: str
    email: str
    password: str = Field(min_length=8)


@app.get("/api/users")
def users(ctx: Ctx = Depends(current)):
    return db.list_users(ctx.tid)


@app.post("/api/users")
def add_user(body: UserIn, ctx: Ctx = Depends(owner_only)):
    if db.get_user_by_email(body.email):
        raise HTTPException(400, "Ya existe una cuenta con ese correo")
    return db.create_user(ctx.tid, body.email, body.name, body.password, role="equipo")


@app.delete("/api/users/{uid}")
def remove_user(uid: int, ctx: Ctx = Depends(owner_only)):
    db.delete_user(ctx.tid, uid)
    return {"ok": True}


# =========================================================================== WhatsApp con un clic

class SignupIn(BaseModel):
    code: str = Field(min_length=4, max_length=2000)
    phone_number_id: str | None = None
    waba_id: str | None = None
    coexistence: bool = False


@app.post("/api/whatsapp/embedded-signup")
def whatsapp_signup(body: SignupIn, ctx: Ctx = Depends(owner_only)):
    if not whatsapp.platform_enabled():
        raise HTTPException(400, "La conexión de WhatsApp con un clic no está configurada en el servidor.")
    _rate_limit(f"wa-signup:{ctx.tid}", limit=10)
    try:
        result = whatsapp.connect_embedded(ctx.tenant, body.code, body.phone_number_id, body.waba_id, body.coexistence)
    except whatsapp.MetaError as e:
        raise HTTPException(400, f"Meta: {e}")
    return {**result, "tenant": public_tenant(db.get_tenant(ctx.tid))}


@app.post("/api/whatsapp/sincronizar")
def whatsapp_resync(ctx: Ctx = Depends(owner_only)):
    if ctx.tenant["whatsapp_mode"] != "coexistencia":
        raise HTTPException(400, "Solo aplica a números en coexistencia.")
    if not whatsapp.sync_window_open(ctx.tenant):
        raise HTTPException(400, "Pasaron las 24 horas: para sincronizar de nuevo hay que desconectar y volver a conectar el número.")
    return {"sync": whatsapp.run_coexistence_sync(ctx.tid)}


@app.post("/api/whatsapp/desconectar")
def whatsapp_disconnect(ctx: Ctx = Depends(owner_only)):
    whatsapp.disconnect(ctx.tid)
    return public_tenant(db.get_tenant(ctx.tid))


# =========================================================================== Tiendanube

@app.get("/tiendanube/conectar")
def tiendanube_connect(ctx: Ctx = Depends(owner_only)):
    if not tiendanube.enabled():
        raise HTTPException(400, "La integración con Tiendanube no está configurada en el servidor.")
    return RedirectResponse(tiendanube.authorize_url(security.sign(str(ctx.tid))))


@app.get("/tiendanube/callback")
def tiendanube_callback(code: str, request: Request, state: str = ""):
    user = db.get_session_user(request.cookies.get(COOKIE, ""))
    if not user:
        return RedirectResponse("/login?next=tiendanube")
    if state and security.unsign(state) != str(user["tenant_id"]):
        raise HTTPException(400, "La autorización no corresponde a esta cuenta. Vuelve a intentarlo.")
    if user["role"] != "dueno":
        raise HTTPException(403, "Solo la dueña o el dueño puede conectar Tiendanube")
    token, store_id = tiendanube.exchange_code(code)
    other = db.get_tenant_by("tiendanube_store_id", store_id)
    if other and other["id"] != user["tenant_id"]:
        raise HTTPException(400, "Esa tienda de Tiendanube ya está conectada a otra cuenta de Integralo.")
    tid = user["tenant_id"]
    db.set_secrets(tid, {"tiendanube_token": token})
    tenant = db.update_tenant(tid, {"tiendanube_store_id": store_id})

    def setup():
        try:
            tiendanube.register_webhooks(tenant)
            n = tiendanube.sync_products(tenant)
            log.info("Tiendanube conectado (tienda %s): %s productos", tid, n)
        except tiendanube.TiendanubeError:
            log.exception("Error configurando Tiendanube para la tienda %s", tid)

    service.in_background(setup)
    return RedirectResponse("/#configuracion")


@app.post("/api/tiendanube/sincronizar-productos")
def tn_sync_products(ctx: Ctx = Depends(owner_only)):
    try:
        return {"productos": tiendanube.sync_products(ctx.tenant)}
    except tiendanube.TiendanubeError as e:
        raise HTTPException(400, str(e))


@app.post("/api/tiendanube/importar-pedidos")
def tn_import_orders(ctx: Ctx = Depends(owner_only)):
    try:
        return {"pedidos": tiendanube.import_recent_orders(ctx.tenant)}
    except tiendanube.TiendanubeError as e:
        raise HTTPException(400, str(e))


@app.post("/api/tiendanube/desconectar")
def tn_disconnect(ctx: Ctx = Depends(owner_only)):
    db.set_secrets(ctx.tid, {"tiendanube_token": ""})
    db.update_tenant(ctx.tid, {"tiendanube_store_id": None})
    return {"ok": True}


# =========================================================================== resumen y pedidos

@app.get("/api/stats")
def get_stats(ctx: Ctx = Depends(current)):
    return db.stats(ctx.tid)


@app.get("/api/orders")
def get_orders(status: str | None = None, q: str | None = None, ctx: Ctx = Depends(current)):
    return db.list_orders(ctx.tid, status, q)


@app.get("/api/orders/{order_id}")
def get_order(order_id: int, ctx: Ctx = Depends(current)):
    o = db.get_order(ctx.tid, order_id)
    if not o:
        raise HTTPException(404, "Pedido no encontrado")
    return o


class OrderIn(BaseModel):
    customer_name: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    city: str = ""
    notes: str = ""
    shipping: float = 0
    source: str = "manual"
    payment_status: str = "pendiente"
    payment_method: str = ""
    items: list[dict] = []


@app.post("/api/orders")
def create_order(body: OrderIn, ctx: Ctx = Depends(current)):
    data = body.model_dump()
    if data["source"] not in db.CHANNELS:
        data["source"] = "manual"
    order, _ = db.create_order(ctx.tid, data)
    excel.safe_sync(ctx.tid)
    return order


class StatusIn(BaseModel):
    status: str
    note: str = ""
    notify: bool = True


@app.post("/api/orders/{order_id}/status")
def set_status(order_id: int, body: StatusIn, ctx: Ctx = Depends(current)):
    try:
        return service.change_status(ctx.tenant, order_id, body.status, body.note, body.notify)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))


class PaymentIn(BaseModel):
    payment_status: str
    method: str = ""
    reference: str = ""
    notify: bool = True


@app.post("/api/orders/{order_id}/payment")
def set_payment(order_id: int, body: PaymentIn, ctx: Ctx = Depends(current)):
    try:
        return service.change_payment(ctx.tenant, order_id, body.payment_status, body.method, body.reference, body.notify)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))


@app.patch("/api/orders/{order_id}")
def patch_order(order_id: int, fields: dict, ctx: Ctx = Depends(current)):
    order = db.update_order_fields(ctx.tid, order_id, fields)
    if not order:
        raise HTTPException(404, "Pedido no encontrado")
    excel.safe_sync(ctx.tid)
    return order


@app.get("/api/excel")
def download_excel(ctx: Ctx = Depends(current)):
    path = excel.sync_orders_to_excel(ctx.tid)
    return FileResponse(path, filename=f"pedidos-{ctx.tenant['slug']}.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# =========================================================================== productos

@app.get("/api/products")
def get_products(ctx: Ctx = Depends(current)):
    return db.list_products(ctx.tid)


@app.post("/api/products")
def save_product(p: dict, ctx: Ctx = Depends(current)):
    if not str(p.get("name", "")).strip():
        raise HTTPException(400, "El producto necesita nombre")
    return {"id": db.upsert_product(ctx.tid, p)}


@app.delete("/api/products/{pid}")
def remove_product(pid: int, ctx: Ctx = Depends(current)):
    db.delete_product(ctx.tid, pid)
    return {"ok": True}


@app.post("/api/products/import")
def import_products(file: UploadFile, ctx: Ctx = Depends(current)):
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
    try:
        return {"importados": excel.import_products_from_excel(ctx.tid, Path(tmp.name))}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(400, "No se pudo leer el archivo. ¿Es un Excel (.xlsx)?")
    finally:
        Path(tmp.name).unlink(missing_ok=True)


# =========================================================================== conversaciones

@app.get("/api/conversations")
def get_conversations(ctx: Ctx = Depends(current)):
    return db.list_conversations(ctx.tid)


@app.get("/api/conversations/{cid}")
def get_conversation(cid: int, ctx: Ctx = Depends(current)):
    conv = db.get_conversation(ctx.tid, cid)
    if not conv:
        raise HTTPException(404, "Conversación no encontrada")
    if conv["unread"]:
        db.update_conversation(ctx.tid, cid, unread=0)
    return {"conversation": conv, "messages": db.get_messages(ctx.tid, cid),
            "orders": db.orders_for_conversation(ctx.tid, cid)}


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@app.post("/api/conversations/{cid}/reply")
def manual_reply(cid: int, body: TextIn, ctx: Ctx = Depends(current)):
    try:
        result = service.send_to_customer(ctx.tenant, cid, body.text)
    except LookupError as e:
        raise HTTPException(404, str(e))
    return {"enviado": result.ok, "error": result.error}


class ConvSettingsIn(BaseModel):
    ai_enabled: bool | None = None
    needs_human: bool | None = None
    resume_ai: bool | None = None     # quitar la pausa de la IA


@app.post("/api/conversations/{cid}/settings")
def conversation_settings(cid: int, body: ConvSettingsIn, ctx: Ctx = Depends(current)):
    data = body.model_dump()
    fields = {k: int(v) for k, v in data.items() if v is not None and k != "resume_ai"}
    if data.get("resume_ai") or data.get("needs_human") is False:
        fields["ai_paused_until"] = None
    conv = db.update_conversation(ctx.tid, cid, **fields)
    if not conv:
        raise HTTPException(404, "Conversación no encontrada")
    if conv["ai_enabled"] and not conv["needs_human"] and llm.available():
        service.in_background(service.reply_with_ai, ctx.tid, cid)  # responde si quedó algo pendiente
    return conv


@app.get("/api/media/{mid}")
def media(mid: int, ctx: Ctx = Depends(current)):
    m = db.get_message(ctx.tid, mid)
    if not m or not m["media_ref"]:
        raise HTTPException(404)
    if m["media_ref"].startswith("wa:"):
        got = channels.fetch_whatsapp_media(ctx.tenant, m["media_ref"][3:])
        if not got:
            raise HTTPException(404, "No se pudo descargar el archivo de WhatsApp")
        return Response(got[0], media_type=got[1], headers={"Cache-Control": "private, max-age=3600"})
    if m["media_ref"].startswith("https://"):
        return RedirectResponse(m["media_ref"])
    raise HTTPException(404)


class SimulateIn(BaseModel):
    user: str = "cliente-prueba"
    name: str = "Cliente de prueba"
    text: str = Field(min_length=1, max_length=4000)


@app.post("/api/simulate")
def simulate(body: SimulateIn, ctx: Ctx = Depends(current)):
    """Simula un cliente escribiendo, para probar la IA sin conectar redes sociales."""
    conv = service.handle_incoming(ctx.tenant, {"channel": "simulador", "user_id": body.user, "name": body.name,
                                                "phone": "", "text": body.text})
    return {"conversation_id": conv["id"]}


# =========================================================================== asistente interna

@app.get("/api/questions")
def questions(status: str = "pendiente", ctx: Ctx = Depends(current)):
    return db.list_questions(ctx.tid, status or None)


@app.post("/api/questions/{qid}/answer")
def answer_question(qid: int, body: TextIn, ctx: Ctx = Depends(current)):
    q = service.answer_owner_question(ctx.tenant, qid, body.text)
    if not q:
        raise HTTPException(404, "Pregunta no encontrada")
    return q


@app.get("/api/assistant/messages")
def assistant_messages(ctx: Ctx = Depends(current)):
    return db.get_assistant_messages(ctx.tid)


@app.post("/api/assistant/chat")
def assistant_chat(body: TextIn, ctx: Ctx = Depends(current)):
    return {"reply": secretary.chat(ctx.tenant, ctx.user, body.text)}
