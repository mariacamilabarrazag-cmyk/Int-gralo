"""Base de datos SQLite multi-tienda.

Cada tabla de negocio lleva `tenant_id`, y TODAS las funciones de lectura/escritura reciben el id de la
tienda y filtran por él: una tienda nunca puede ver ni modificar datos de otra.
"""
import json
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta

from . import config, security

STATUSES = ["recibido", "preparacion", "reparto", "entregado", "cancelado"]
STATUS_LABELS = {
    "recibido": "Recibido",
    "preparacion": "En preparación",
    "reparto": "En reparto",
    "entregado": "Entregado",
    "cancelado": "Cancelado",
}
PAYMENT_STATUSES = ["pendiente", "por_verificar", "pagado", "reembolsado"]
CHANNELS = ["web", "whatsapp", "instagram", "facebook", "tiktok", "manual"]

# Credenciales por tienda (se guardan cifradas)
SECRET_KEYS = [
    "whatsapp_token", "whatsapp_phone_number_id",
    "meta_page_token", "meta_app_secret", "meta_verify_token",
    "tiktok_access_token", "tiktok_webhook_secret",
    "shopify_secret", "woocommerce_secret", "web_token",
    "tiendanube_token", "whatsapp_pin",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    assistant_name TEXT NOT NULL DEFAULT 'Asistente',
    owner_title TEXT NOT NULL DEFAULT 'la dueña',
    brand_primary TEXT NOT NULL DEFAULT '#2a78d6',
    brand_secondary TEXT NOT NULL DEFAULT '#0b0b0b',
    logo_file TEXT DEFAULT '',
    currency TEXT NOT NULL DEFAULT 'COP',
    business_info TEXT NOT NULL DEFAULT '',
    ai_enabled_default INTEGER NOT NULL DEFAULT 1,
    webhook_token TEXT UNIQUE NOT NULL,
    tiendanube_store_id TEXT UNIQUE,
    secrets_enc TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'equipo',      -- 'dueno' | 'equipo'
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    external_id TEXT,
    sku TEXT,
    name TEXT NOT NULL,
    price REAL NOT NULL DEFAULT 0,
    stock INTEGER,
    description TEXT DEFAULT '',
    variants TEXT DEFAULT '',
    image_url TEXT DEFAULT '',
    url TEXT DEFAULT '',
    personalizable INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(tenant_id, sku),
    UNIQUE(tenant_id, external_id)
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    external_user_id TEXT NOT NULL,
    customer_name TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    ai_enabled INTEGER NOT NULL DEFAULT 1,
    needs_human INTEGER NOT NULL DEFAULT 0,
    waiting_owner INTEGER NOT NULL DEFAULT 0,
    unread INTEGER NOT NULL DEFAULT 0,
    last_message_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, channel, external_user_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    direction TEXT NOT NULL,          -- 'in' (cliente) | 'out' (al cliente) | 'internal' (no lo ve el cliente)
    author TEXT NOT NULL,             -- 'cliente' | 'ia' | 'humano' | 'sistema' | 'duena'
    text TEXT NOT NULL,
    media_kind TEXT DEFAULT '',
    media_ref TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    code TEXT NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT,
    conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
    customer_name TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    email TEXT DEFAULT '',
    address TEXT DEFAULT '',
    city TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    shipping REAL NOT NULL DEFAULT 0,
    total REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'recibido',
    payment_status TEXT NOT NULL DEFAULT 'pendiente',
    payment_method TEXT DEFAULT '',
    payment_reference TEXT DEFAULT '',
    paid_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, seq),
    UNIQUE(tenant_id, source, external_id)
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_name TEXT NOT NULL,
    sku TEXT DEFAULT '',
    details TEXT DEFAULT '',          -- talla, color, diseño, texto a estampar...
    quantity INTEGER NOT NULL DEFAULT 1,
    unit_price REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,               -- 'estado' | 'pago'
    value TEXT NOT NULL,
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS owner_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    answer TEXT,
    status TEXT NOT NULL DEFAULT 'pendiente',   -- 'pendiente' | 'respondida'
    created_at TEXT NOT NULL,
    answered_at TEXT
);

CREATE TABLE IF NOT EXISTS assistant_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    role TEXT NOT NULL,               -- 'user' | 'assistant'
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_orders_tenant ON orders(tenant_id, status);
CREATE INDEX IF NOT EXISTS ix_msgs_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS ix_conv_tenant ON conversations(tenant_id, last_message_at);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def tx():
    """Transacción con bloqueo de escritura inmediato (evita choques al numerar pedidos)."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


# Columnas agregadas después de la primera versión: se crean solas en bases de datos existentes.
MIGRATIONS = [
    ("tenants", "whatsapp_phone_number_id", "TEXT"),        # para enrutar el webhook de la plataforma
    ("tenants", "whatsapp_waba_id", "TEXT"),
    ("tenants", "whatsapp_display_phone", "TEXT DEFAULT ''"),
    ("tenants", "whatsapp_mode", "TEXT DEFAULT ''"),         # '' | 'manual' | 'api' | 'coexistencia'
    ("tenants", "whatsapp_connected_at", "TEXT"),
    ("tenants", "whatsapp_token_expires_at", "TEXT"),
    ("tenants", "smb_sync", "TEXT DEFAULT '{}'"),            # estado de la sincronización de coexistencia
    ("tenants", "human_pause_minutes", "INTEGER NOT NULL DEFAULT 30"),
    ("tenants", "ai_reply_delay_seconds", "INTEGER NOT NULL DEFAULT 0"),
    ("conversations", "ai_paused_until", "TEXT"),
    ("messages", "external_id", "TEXT"),                      # wamid de WhatsApp (evita duplicados)
]
POST_MIGRATION_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_tenant_wa_phone ON tenants(whatsapp_phone_number_id) WHERE whatsapp_phone_number_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_msg_external ON messages(tenant_id, external_id) WHERE external_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS contacts (
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    external_user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (tenant_id, channel, external_user_id)
);
"""


def init_db() -> None:
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        for table, column, ddl in MIGRATIONS:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        conn.executescript(POST_MIGRATION_SQL)
    finally:
        conn.close()


def rows(cursor) -> list[dict]:
    return [dict(r) for r in cursor.fetchall()]


def one(cursor) -> dict | None:
    r = cursor.fetchone()
    return dict(r) if r else None


def to_json(o) -> str:
    return json.dumps(o, ensure_ascii=False, default=str)


# =========================================================================== tiendas

def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "tienda"


def create_tenant(name: str, **fields) -> dict:
    base = slugify(name)
    with tx() as c:
        slug, n = base, 1
        while c.execute("SELECT 1 FROM tenants WHERE slug=?", (slug,)).fetchone():
            n += 1
            slug = f"{base}-{n}"
        cur = c.execute(
            "INSERT INTO tenants (slug, name, webhook_token, created_at) VALUES (?,?,?,?)",
            (slug, name, security.new_token(18), now()),
        )
        tid = cur.lastrowid
    if fields:
        update_tenant(tid, fields)
    return get_tenant(tid)


TENANT_FIELDS = {"name", "assistant_name", "owner_title", "brand_primary", "brand_secondary", "logo_file",
                 "currency", "business_info", "ai_enabled_default", "tiendanube_store_id",
                 "whatsapp_phone_number_id", "whatsapp_waba_id", "whatsapp_display_phone", "whatsapp_mode",
                 "whatsapp_connected_at", "whatsapp_token_expires_at", "smb_sync",
                 "human_pause_minutes", "ai_reply_delay_seconds"}


def update_tenant(tid: int, fields: dict) -> dict:
    sets = {k: v for k, v in fields.items() if k in TENANT_FIELDS}
    if sets:
        with tx() as c:
            c.execute(f"UPDATE tenants SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), tid))
    return get_tenant(tid)


def get_tenant(tid: int) -> dict | None:
    c = connect()
    try:
        return one(c.execute("SELECT * FROM tenants WHERE id=?", (tid,)))
    finally:
        c.close()


def get_tenant_by(column: str, value) -> dict | None:
    assert column in ("webhook_token", "tiendanube_store_id", "slug", "whatsapp_phone_number_id", "whatsapp_waba_id")
    if not value:
        return None
    c = connect()
    try:
        return one(c.execute(f"SELECT * FROM tenants WHERE {column}=?", (str(value),)))
    finally:
        c.close()


def list_tenants() -> list[dict]:
    c = connect()
    try:
        return rows(c.execute("SELECT id, slug, name, created_at FROM tenants ORDER BY id"))
    finally:
        c.close()


def get_secrets(tenant: dict) -> dict:
    return security.decrypt_json(tenant.get("secrets_enc"))


def set_secrets(tid: int, updates: dict) -> None:
    """None = no cambiar; "" = borrar; otro valor = guardar."""
    t = get_tenant(tid)
    current = get_secrets(t)
    for k, v in updates.items():
        if k not in SECRET_KEYS or v is None:
            continue
        if v == "":
            current.pop(k, None)
        else:
            current[k] = str(v).strip()
    with tx() as c:
        c.execute("UPDATE tenants SET secrets_enc=? WHERE id=?", (security.encrypt_json(current), tid))


# =========================================================================== usuarios y sesiones

def create_user(tid: int, email: str, name: str, password: str, role: str = "equipo") -> dict:
    with tx() as c:
        cur = c.execute(
            "INSERT INTO users (tenant_id, email, name, password_hash, role, created_at) VALUES (?,?,?,?,?,?)",
            (tid, email.strip().lower(), name.strip(), security.hash_password(password), role, now()),
        )
        return one(c.execute("SELECT id, tenant_id, email, name, role FROM users WHERE id=?", (cur.lastrowid,)))


def get_user_by_email(email: str) -> dict | None:
    c = connect()
    try:
        return one(c.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)))
    finally:
        c.close()


def list_users(tid: int) -> list[dict]:
    c = connect()
    try:
        return rows(c.execute("SELECT id, email, name, role, created_at FROM users WHERE tenant_id=? ORDER BY id", (tid,)))
    finally:
        c.close()


def delete_user(tid: int, uid: int) -> None:
    with tx() as c:
        c.execute("DELETE FROM users WHERE id=? AND tenant_id=? AND role!='dueno'", (uid, tid))


def set_password(uid: int, password: str) -> None:
    with tx() as c:
        c.execute("UPDATE users SET password_hash=? WHERE id=?", (security.hash_password(password), uid))
        c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))


def create_session(uid: int) -> str:
    token = security.new_token()
    expires = (datetime.now() + timedelta(days=config.SESSION_DAYS)).isoformat(timespec="seconds")
    with tx() as c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (now(),))
        c.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?,?,?)",
                  (security.token_hash(token), uid, expires))
    return token


def get_session_user(token: str) -> dict | None:
    if not token:
        return None
    c = connect()
    try:
        return one(c.execute(
            """SELECT u.id, u.tenant_id, u.email, u.name, u.role FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash=? AND s.expires_at > ?""", (security.token_hash(token), now())))
    finally:
        c.close()


def delete_session(token: str) -> None:
    with tx() as c:
        c.execute("DELETE FROM sessions WHERE token_hash=?", (security.token_hash(token),))


# =========================================================================== productos

def list_products(tid: int, only_active: bool = False) -> list[dict]:
    c = connect()
    try:
        q = "SELECT * FROM products WHERE tenant_id=?" + (" AND active=1" if only_active else "") + " ORDER BY name"
        return rows(c.execute(q, (tid,)))
    finally:
        c.close()


def search_products(tid: int, query: str, limit: int = 15) -> list[dict]:
    words = [w for w in re.split(r"\s+", query.strip()) if len(w) > 1][:5]
    q = "SELECT * FROM products WHERE tenant_id=? AND active=1"
    args: list = [tid]
    for w in words:  # todas las palabras deben aparecer en algún campo
        q += " AND (name LIKE ? OR sku LIKE ? OR description LIKE ? OR variants LIKE ?)"
        args += [f"%{w}%"] * 4
    c = connect()
    try:
        return rows(c.execute(q + " ORDER BY personalizable DESC, name LIMIT ?", (*args, limit)))
    finally:
        c.close()


def get_product(tid: int, pid: int) -> dict | None:
    c = connect()
    try:
        return one(c.execute("SELECT * FROM products WHERE id=? AND tenant_id=?", (pid, tid)))
    finally:
        c.close()


def upsert_product(tid: int, p: dict) -> int:
    vals = {
        "external_id": p.get("external_id") or None,
        "sku": (p.get("sku") or None),
        "name": str(p["name"]).strip(),
        "price": float(p.get("price") or 0),
        "stock": p.get("stock") if p.get("stock") not in ("",) else None,
        "description": p.get("description", "") or "",
        "variants": p.get("variants", "") or "",
        "image_url": p.get("image_url", "") or "",
        "url": p.get("url", "") or "",
        "personalizable": int(bool(p.get("personalizable", 0))),
        "active": int(bool(p.get("active", 1))),
    }
    with tx() as c:
        existing = None
        if p.get("id"):
            existing = c.execute("SELECT id FROM products WHERE id=? AND tenant_id=?", (p["id"], tid)).fetchone()
        if not existing and vals["external_id"]:
            existing = c.execute("SELECT id FROM products WHERE external_id=? AND tenant_id=?", (vals["external_id"], tid)).fetchone()
        if not existing and vals["sku"]:
            existing = c.execute("SELECT id FROM products WHERE sku=? AND tenant_id=?", (vals["sku"], tid)).fetchone()
        if existing:
            c.execute(f"UPDATE products SET {', '.join(f'{k}=?' for k in vals)} WHERE id=? AND tenant_id=?",
                      (*vals.values(), existing["id"], tid))
            return existing["id"]
        cols = ", ".join(vals)
        cur = c.execute(f"INSERT INTO products (tenant_id, {cols}) VALUES (?, {', '.join('?' * len(vals))})",
                        (tid, *vals.values()))
        return cur.lastrowid


def delete_product(tid: int, pid: int) -> None:
    with tx() as c:
        c.execute("DELETE FROM products WHERE id=? AND tenant_id=?", (pid, tid))


# =========================================================================== pedidos

def _add_event(c, order_id: int, kind: str, value: str, note: str = "") -> None:
    c.execute("INSERT INTO order_events (order_id, kind, value, note, created_at) VALUES (?,?,?,?,?)",
              (order_id, kind, value, note, now()))


def _load_order(c, tid: int, order_id: int) -> dict | None:
    o = one(c.execute("SELECT * FROM orders WHERE id=? AND tenant_id=?", (order_id, tid)))
    if not o:
        return None
    o["items"] = rows(c.execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)))
    o["events"] = rows(c.execute("SELECT * FROM order_events WHERE order_id=? ORDER BY id", (order_id,)))
    return o


def create_order(tid: int, data: dict) -> tuple[dict, bool]:
    """Crea un pedido. Devuelve (pedido, creado). Si ya existía (mismo origen e id externo) devuelve el existente."""
    items = data.get("items") or []
    shipping = float(data.get("shipping") or 0)
    subtotal = sum(float(i.get("unit_price") or 0) * int(i.get("quantity") or 1) for i in items)
    total = float(data["total"]) if data.get("total") not in (None, "") else subtotal + shipping
    source = data.get("source") or "manual"
    ext = str(data["external_id"]) if data.get("external_id") else None
    ts = now()
    paid = data.get("payment_status") == "pagado"
    with tx() as c:
        if ext:
            r = c.execute("SELECT id FROM orders WHERE tenant_id=? AND source=? AND external_id=?", (tid, source, ext)).fetchone()
            if r:
                return _load_order(c, tid, r["id"]), False
        seq = c.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM orders WHERE tenant_id=?", (tid,)).fetchone()[0]
        cur = c.execute(
            """INSERT INTO orders (tenant_id, seq, code, source, external_id, conversation_id, customer_name, phone, email,
               address, city, notes, shipping, total, status, payment_status, payment_method, paid_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tid, seq, f"PED-{seq:05d}", source, ext, data.get("conversation_id"), data.get("customer_name", ""),
             data.get("phone", ""), data.get("email", ""), data.get("address", ""), data.get("city", ""),
             data.get("notes", ""), shipping, total, data.get("status", "recibido"),
             data.get("payment_status", "pendiente"), data.get("payment_method", ""), ts if paid else None, ts, ts),
        )
        oid = cur.lastrowid
        for i in items:
            c.execute(
                "INSERT INTO order_items (order_id, product_name, sku, details, quantity, unit_price) VALUES (?,?,?,?,?,?)",
                (oid, i.get("product_name") or "Producto", i.get("sku") or "", i.get("details") or "",
                 int(i.get("quantity") or 1), float(i.get("unit_price") or 0)),
            )
        _add_event(c, oid, "estado", data.get("status", "recibido"), f"Pedido creado desde {source}")
        if paid:
            _add_event(c, oid, "pago", "pagado", data.get("payment_method", ""))
        return _load_order(c, tid, oid), True


def get_order(tid: int, order_id: int) -> dict | None:
    c = connect()
    try:
        return _load_order(c, tid, order_id)
    finally:
        c.close()


def find_order_by_code(tid: int, code: str) -> dict | None:
    m = re.search(r"(\d+)", code or "")
    if not m:
        return None
    c = connect()
    try:
        r = c.execute("SELECT id FROM orders WHERE tenant_id=? AND seq=?", (tid, int(m.group(1)))).fetchone()
        return _load_order(c, tid, r["id"]) if r else None
    finally:
        c.close()


def find_order_by_external(tid: int, source: str, external_id: str) -> dict | None:
    c = connect()
    try:
        r = c.execute("SELECT id FROM orders WHERE tenant_id=? AND source=? AND external_id=?",
                      (tid, source, str(external_id))).fetchone()
        return _load_order(c, tid, r["id"]) if r else None
    finally:
        c.close()


def list_orders(tid: int, status: str | None = None, search: str | None = None,
                payment_status: str | None = None, limit: int = 500) -> list[dict]:
    q, args = "SELECT * FROM orders WHERE tenant_id=?", [tid]
    if status:
        q += " AND status=?"
        args.append(status)
    if payment_status:
        q += " AND payment_status=?"
        args.append(payment_status)
    if search:
        q += " AND (code LIKE ? OR customer_name LIKE ? OR phone LIKE ? OR notes LIKE ?)"
        args += [f"%{search}%"] * 4
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    c = connect()
    try:
        orders = rows(c.execute(q, args))
        for o in orders:
            o["items"] = rows(c.execute("SELECT * FROM order_items WHERE order_id=?", (o["id"],)))
        return orders
    finally:
        c.close()


def orders_for_conversation(tid: int, conversation_id: int) -> list[dict]:
    c = connect()
    try:
        ids = [r["id"] for r in c.execute(
            "SELECT id FROM orders WHERE tenant_id=? AND conversation_id=? ORDER BY id DESC LIMIT 10", (tid, conversation_id))]
        return [_load_order(c, tid, i) for i in ids]
    finally:
        c.close()


def update_status(tid: int, order_id: int, status: str, note: str = "") -> dict:
    if status not in STATUSES:
        raise ValueError(f"Estado inválido: {status}")
    with tx() as c:
        if not c.execute("SELECT 1 FROM orders WHERE id=? AND tenant_id=?", (order_id, tid)).fetchone():
            raise LookupError("Pedido no encontrado")
        c.execute("UPDATE orders SET status=?, updated_at=? WHERE id=? AND tenant_id=?", (status, now(), order_id, tid))
        _add_event(c, order_id, "estado", status, note)
        return _load_order(c, tid, order_id)


def update_payment(tid: int, order_id: int, payment_status: str, method: str = "", reference: str = "") -> dict:
    if payment_status not in PAYMENT_STATUSES:
        raise ValueError(f"Estado de pago inválido: {payment_status}")
    with tx() as c:
        if not c.execute("SELECT 1 FROM orders WHERE id=? AND tenant_id=?", (order_id, tid)).fetchone():
            raise LookupError("Pedido no encontrado")
        c.execute(
            """UPDATE orders SET payment_status=?, payment_method=COALESCE(NULLIF(?, ''), payment_method),
               payment_reference=COALESCE(NULLIF(?, ''), payment_reference), paid_at=?, updated_at=?
               WHERE id=? AND tenant_id=?""",
            (payment_status, method, reference, now() if payment_status == "pagado" else None, now(), order_id, tid),
        )
        _add_event(c, order_id, "pago", payment_status, " ".join(x for x in (method, reference) if x))
        return _load_order(c, tid, order_id)


def update_order_fields(tid: int, order_id: int, fields: dict) -> dict:
    allowed = {"customer_name", "phone", "email", "address", "city", "notes", "shipping", "total"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    with tx() as c:
        if sets:
            c.execute(f"UPDATE orders SET {', '.join(f'{k}=?' for k in sets)}, updated_at=? WHERE id=? AND tenant_id=?",
                      (*sets.values(), now(), order_id, tid))
        return _load_order(c, tid, order_id)


def stats(tid: int) -> dict:
    c = connect()
    try:
        today = datetime.now().date().isoformat()
        month = today[:7]

        def val(q, *a):
            return c.execute(q, (tid, *a)).fetchone()[0] or 0

        by_status = {s: 0 for s in STATUSES}
        for r in c.execute("SELECT status, COUNT(*) n FROM orders WHERE tenant_id=? GROUP BY status", (tid,)):
            by_status[r["status"]] = r["n"]
        return {
            "ingresos_hoy": val("SELECT SUM(total) FROM orders WHERE tenant_id=? AND payment_status='pagado' AND substr(paid_at,1,10)=?", today),
            "ingresos_mes": val("SELECT SUM(total) FROM orders WHERE tenant_id=? AND payment_status='pagado' AND substr(paid_at,1,7)=?", month),
            "ingresos_total": val("SELECT SUM(total) FROM orders WHERE tenant_id=? AND payment_status='pagado'"),
            "por_cobrar": val("SELECT SUM(total) FROM orders WHERE tenant_id=? AND payment_status IN ('pendiente','por_verificar') AND status!='cancelado'"),
            "pagos_por_verificar": val("SELECT COUNT(*) FROM orders WHERE tenant_id=? AND payment_status='por_verificar'"),
            "pedidos_hoy": val("SELECT COUNT(*) FROM orders WHERE tenant_id=? AND substr(created_at,1,10)=?", today),
            "pedidos_activos": val("SELECT COUNT(*) FROM orders WHERE tenant_id=? AND status IN ('recibido','preparacion','reparto')"),
            "conversaciones_humano": val("SELECT COUNT(*) FROM conversations WHERE tenant_id=? AND needs_human=1"),
            "preguntas_pendientes": val("SELECT COUNT(*) FROM owner_questions WHERE tenant_id=? AND status='pendiente'"),
            "por_estado": by_status,
            "por_canal": rows(c.execute(
                "SELECT source, COUNT(*) n, COALESCE(SUM(CASE WHEN payment_status='pagado' THEN total END),0) ingresos "
                "FROM orders WHERE tenant_id=? GROUP BY source ORDER BY n DESC", (tid,))),
            "ingresos_diarios": rows(c.execute(
                "SELECT substr(paid_at,1,10) dia, SUM(total) ingresos, COUNT(*) pedidos FROM orders "
                "WHERE tenant_id=? AND payment_status='pagado' AND paid_at >= date('now','localtime','-29 days') "
                "GROUP BY dia ORDER BY dia", (tid,))),
        }
    finally:
        c.close()


# =========================================================================== conversaciones

def get_or_create_conversation(tid: int, channel: str, external_user_id: str, name: str = "", phone: str = "",
                               ai_enabled: bool = True) -> dict:
    with tx() as c:
        r = one(c.execute("SELECT * FROM conversations WHERE tenant_id=? AND channel=? AND external_user_id=?",
                          (tid, channel, external_user_id)))
        if r:
            if name and not r["customer_name"]:
                c.execute("UPDATE conversations SET customer_name=? WHERE id=?", (name, r["id"]))
                r["customer_name"] = name
            return r
        if not name:
            saved = c.execute("SELECT name FROM contacts WHERE tenant_id=? AND channel=? AND external_user_id=?",
                              (tid, channel, external_user_id)).fetchone()
            name = saved["name"] if saved else ""
        cur = c.execute(
            """INSERT INTO conversations (tenant_id, channel, external_user_id, customer_name, phone, ai_enabled,
               created_at, last_message_at) VALUES (?,?,?,?,?,?,?,?)""",
            (tid, channel, external_user_id, name, phone, int(ai_enabled), now(), now()))
        return one(c.execute("SELECT * FROM conversations WHERE id=?", (cur.lastrowid,)))


def get_conversation(tid: int, cid: int) -> dict | None:
    c = connect()
    try:
        return one(c.execute("SELECT * FROM conversations WHERE id=? AND tenant_id=?", (cid, tid)))
    finally:
        c.close()


def list_conversations(tid: int) -> list[dict]:
    c = connect()
    try:
        return rows(c.execute(
            """SELECT cv.*, (SELECT text FROM messages m WHERE m.conversation_id = cv.id AND m.direction != 'internal'
                             ORDER BY m.created_at DESC, m.id DESC LIMIT 1) AS last_text
               FROM conversations cv WHERE tenant_id=?
               ORDER BY needs_human DESC, waiting_owner DESC, last_message_at DESC LIMIT 300""", (tid,)))
    finally:
        c.close()


def add_message(tid: int, cid: int, direction: str, author: str, text: str,
                media_kind: str = "", media_ref: str = "", external_id: str | None = None,
                created_at: str | None = None, history: bool = False) -> dict | None:
    """Guarda un mensaje. Con `external_id` (wamid) no se duplica: devuelve None si ya existía.
    `history=True` (historial importado) no cuenta como mensaje nuevo sin leer."""
    ts = created_at or now()
    with tx() as c:
        if not c.execute("SELECT 1 FROM conversations WHERE id=? AND tenant_id=?", (cid, tid)).fetchone():
            raise LookupError("Conversación no encontrada")
        if external_id and c.execute("SELECT 1 FROM messages WHERE tenant_id=? AND external_id=?",
                                     (tid, external_id)).fetchone():
            return None
        cur = c.execute(
            """INSERT INTO messages (tenant_id, conversation_id, direction, author, text, media_kind, media_ref,
               external_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (tid, cid, direction, author, text, media_kind, media_ref, external_id, ts))
        if history:
            c.execute("UPDATE conversations SET last_message_at=MAX(COALESCE(last_message_at,''), ?) WHERE id=?", (ts, cid))
        elif direction == "in":
            c.execute("UPDATE conversations SET last_message_at=?, unread=unread+1 WHERE id=?", (ts, cid))
        else:
            c.execute("UPDATE conversations SET last_message_at=? WHERE id=?", (ts, cid))
        return one(c.execute("SELECT * FROM messages WHERE id=?", (cur.lastrowid,)))


def get_messages(tid: int, cid: int, limit: int = 200) -> list[dict]:
    c = connect()
    try:
        msgs = rows(c.execute("SELECT * FROM messages WHERE tenant_id=? AND conversation_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
                              (tid, cid, limit)))
        return list(reversed(msgs))
    finally:
        c.close()


def get_message(tid: int, mid: int) -> dict | None:
    c = connect()
    try:
        return one(c.execute("SELECT * FROM messages WHERE id=? AND tenant_id=?", (mid, tid)))
    finally:
        c.close()


def update_conversation(tid: int, cid: int, **fields) -> dict | None:
    allowed = {"ai_enabled", "needs_human", "waiting_owner", "unread", "customer_name", "phone", "ai_paused_until"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    with tx() as c:
        if sets:
            c.execute(f"UPDATE conversations SET {', '.join(f'{k}=?' for k in sets)} WHERE id=? AND tenant_id=?",
                      (*sets.values(), cid, tid))
        return one(c.execute("SELECT * FROM conversations WHERE id=? AND tenant_id=?", (cid, tid)))


# =========================================================================== preguntas a la dueña

def create_question(tid: int, cid: int | None, question: str) -> dict:
    with tx() as c:
        cur = c.execute("INSERT INTO owner_questions (tenant_id, conversation_id, question, created_at) VALUES (?,?,?,?)",
                        (tid, cid, question, now()))
        if cid:
            c.execute("UPDATE conversations SET waiting_owner=1 WHERE id=? AND tenant_id=?", (cid, tid))
        return one(c.execute("SELECT * FROM owner_questions WHERE id=?", (cur.lastrowid,)))


def list_questions(tid: int, status: str | None = "pendiente", limit: int = 50) -> list[dict]:
    q = """SELECT q.*, cv.customer_name, cv.channel, cv.phone FROM owner_questions q
           LEFT JOIN conversations cv ON cv.id = q.conversation_id WHERE q.tenant_id=?"""
    args: list = [tid]
    if status:
        q += " AND q.status=?"
        args.append(status)
    c = connect()
    try:
        return rows(c.execute(q + " ORDER BY q.id DESC LIMIT ?", (*args, limit)))
    finally:
        c.close()


def answer_question(tid: int, qid: int, answer: str) -> dict | None:
    with tx() as c:
        q = one(c.execute("SELECT * FROM owner_questions WHERE id=? AND tenant_id=?", (qid, tid)))
        if not q:
            return None
        c.execute("UPDATE owner_questions SET answer=?, status='respondida', answered_at=? WHERE id=?", (answer, now(), qid))
        if q["conversation_id"]:
            pending = c.execute("SELECT COUNT(*) FROM owner_questions WHERE conversation_id=? AND status='pendiente'",
                                (q["conversation_id"],)).fetchone()[0]
            c.execute("UPDATE conversations SET waiting_owner=? WHERE id=?", (int(pending > 0), q["conversation_id"]))
        return one(c.execute("SELECT * FROM owner_questions WHERE id=?", (qid,)))


def answered_questions(tid: int, cid: int) -> list[dict]:
    c = connect()
    try:
        return rows(c.execute("SELECT * FROM owner_questions WHERE tenant_id=? AND conversation_id=? AND status='respondida'",
                              (tid, cid)))
    finally:
        c.close()


# =========================================================================== chat con la asistente

def add_assistant_message(tid: int, user_id: int | None, role: str, text: str) -> dict:
    with tx() as c:
        cur = c.execute("INSERT INTO assistant_messages (tenant_id, user_id, role, text, created_at) VALUES (?,?,?,?,?)",
                        (tid, user_id, role, text, now()))
        return one(c.execute("SELECT * FROM assistant_messages WHERE id=?", (cur.lastrowid,)))


def get_assistant_messages(tid: int, limit: int = 60) -> list[dict]:
    c = connect()
    try:
        return list(reversed(rows(c.execute(
            "SELECT * FROM assistant_messages WHERE tenant_id=? ORDER BY id DESC LIMIT ?", (tid, limit)))))
    finally:
        c.close()


def message_exists(tid: int, external_id: str) -> bool:
    c = connect()
    try:
        return bool(c.execute("SELECT 1 FROM messages WHERE tenant_id=? AND external_id=?", (tid, external_id)).fetchone())
    finally:
        c.close()


def set_message_external_id(tid: int, mid: int, external_id: str) -> None:
    with tx() as c:
        c.execute("UPDATE messages SET external_id=? WHERE id=? AND tenant_id=?", (external_id, mid, tid))


def update_message_text_by_external(tid: int, external_id: str, text: str) -> bool:
    with tx() as c:
        cur = c.execute("UPDATE messages SET text=? WHERE tenant_id=? AND external_id=?", (text, tid, external_id))
        return cur.rowcount > 0


def last_message(tid: int, cid: int) -> dict | None:
    c = connect()
    try:
        return one(c.execute("""SELECT * FROM messages WHERE tenant_id=? AND conversation_id=? AND direction!='internal'
                                ORDER BY created_at DESC, id DESC LIMIT 1""", (tid, cid)))
    finally:
        c.close()


def save_contact(tid: int, channel: str, external_user_id: str, name: str) -> None:
    """Libreta de contactos (p. ej. la del celular en coexistencia): nombra las conversaciones."""
    with tx() as c:
        c.execute("""INSERT INTO contacts (tenant_id, channel, external_user_id, name) VALUES (?,?,?,?)
                     ON CONFLICT(tenant_id, channel, external_user_id) DO UPDATE SET name=excluded.name""",
                  (tid, channel, external_user_id, name))
        c.execute("""UPDATE conversations SET customer_name=? WHERE tenant_id=? AND channel=? AND external_user_id=?
                     AND (customer_name IS NULL OR customer_name='')""", (name, tid, channel, external_user_id))


def delete_contact(tid: int, channel: str, external_user_id: str) -> None:
    with tx() as c:
        c.execute("DELETE FROM contacts WHERE tenant_id=? AND channel=? AND external_user_id=?",
                  (tid, channel, external_user_id))


def tenants_with_whatsapp_token_expiring(before_iso: str) -> list[dict]:
    c = connect()
    try:
        return rows(c.execute("""SELECT * FROM tenants WHERE whatsapp_mode IN ('api','coexistencia')
                                 AND whatsapp_token_expires_at IS NOT NULL AND whatsapp_token_expires_at < ?""",
                              (before_iso,)))
    finally:
        c.close()
