"""Excel de pedidos por tienda (data/tiendas/<id>/pedidos.xlsx) e importación del catálogo desde Excel."""
import logging
import threading
from collections import defaultdict
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import config, db

log = logging.getLogger("excel")
_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)
PAYMENT_LABELS = {"pendiente": "Pendiente", "por_verificar": "Por verificar", "pagado": "Pagado", "reembolsado": "Reembolsado"}


def excel_path(tid: int) -> Path:
    d = config.TENANTS_DIR / str(tid)
    d.mkdir(parents=True, exist_ok=True)
    return d / "pedidos.xlsx"


def _header_style(tenant: dict):
    color = (tenant.get("brand_primary") or "#1F4E78").lstrip("#").upper()
    r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    ink = "000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 160 else "FFFFFF"
    return PatternFill("solid", fgColor=color), Font(color=ink, bold=True)


def _sheet(wb, title, headers, widths, style):
    fill, font = style
    ws = wb.create_sheet(title)
    ws.append(headers)
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
        cell = ws.cell(row=1, column=i)
        cell.fill, cell.font = fill, font
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"
    return ws


def sync_orders_to_excel(tid: int) -> Path:
    """Reescribe el Excel completo de la tienda."""
    path = excel_path(tid)
    tenant = db.get_tenant(tid)
    style = _header_style(tenant)
    with _locks[tid]:
        orders = list(reversed(db.list_orders(tid, limit=100000)))
        wb = Workbook()
        wb.remove(wb.active)
        ws = _sheet(wb, "Pedidos",
                    ["Código", "Fecha", "Canal", "Cliente", "Teléfono", "Dirección", "Ciudad", "Productos",
                     "Envío", "Total", "Estado", "Pago", "Método de pago", "Referencia", "Fecha de pago", "Notas"],
                    [12, 18, 11, 24, 15, 32, 14, 50, 10, 12, 15, 13, 16, 16, 18, 30], style)
        for o in orders:
            productos = "; ".join(f"{i['quantity']}x {i['product_name']}" + (f" ({i['details']})" if i["details"] else "")
                                  for i in o["items"])
            ws.append([o["code"], o["created_at"].replace("T", " "), o["source"], o["customer_name"], o["phone"],
                       o["address"], o["city"], productos, o["shipping"], o["total"],
                       db.STATUS_LABELS.get(o["status"], o["status"]), PAYMENT_LABELS.get(o["payment_status"], o["payment_status"]),
                       o["payment_method"], o["payment_reference"], (o["paid_at"] or "").replace("T", " "), o["notes"]])
        for row in ws.iter_rows(min_row=2, min_col=9, max_col=10):
            for cell in row:
                cell.number_format = "#,##0"

        wi = _sheet(wb, "Detalle productos",
                    ["Código pedido", "Producto", "SKU", "Personalización / detalle", "Cantidad", "Precio unitario", "Subtotal"],
                    [14, 36, 14, 45, 10, 15, 15], style)
        for o in orders:
            for i in o["items"]:
                wi.append([o["code"], i["product_name"], i["sku"], i["details"], i["quantity"], i["unit_price"],
                           i["quantity"] * i["unit_price"]])

        s = db.stats(tid)
        wr = _sheet(wb, "Resumen", ["Indicador", "Valor"], [32, 18], style)
        for label, key in [("Ingresos de hoy", "ingresos_hoy"), ("Ingresos del mes", "ingresos_mes"),
                           ("Ingresos totales (pagados)", "ingresos_total"), ("Por cobrar", "por_cobrar"),
                           ("Pedidos activos", "pedidos_activos")]:
            wr.append([label, s[key]])
        wr.append([])
        wr.append(["Pedidos por estado", ""])
        for st, n in s["por_estado"].items():
            wr.append([db.STATUS_LABELS[st], n])

        tmp = path.with_suffix(".tmp.xlsx")
        try:
            wb.save(tmp)
            tmp.replace(path)
        except PermissionError:
            log.warning("No se pudo escribir %s (¿abierto en Excel?).", path)
        return path


def safe_sync(tid: int) -> None:
    try:
        sync_orders_to_excel(tid)
    except Exception:
        log.exception("Error sincronizando Excel de la tienda %s", tid)


def import_products_from_excel(tid: int, file_path: Path) -> int:
    """Columnas (fila 1): Nombre, Precio y opcionales SKU, Stock, Descripción, Variantes, Personalizable, Imagen, URL."""
    wb = load_workbook(file_path, read_only=True, data_only=True)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    headers = [str(h or "").strip().lower() for h in next(it)]

    def col(*names):
        return next((headers.index(n) for n in names if n in headers), None)

    c = {
        "name": col("nombre", "producto", "name"), "price": col("precio", "price", "valor"),
        "sku": col("sku", "código", "codigo", "referencia"), "stock": col("stock", "inventario", "cantidad"),
        "description": col("descripción", "descripcion", "description"), "variants": col("variantes", "tallas", "opciones"),
        "personalizable": col("personalizable", "personalizado"), "image_url": col("imagen", "image", "foto"),
        "url": col("url", "enlace", "link"),
    }
    if c["name"] is None or c["price"] is None:
        raise ValueError("El Excel debe tener al menos las columnas 'Nombre' y 'Precio' en la primera fila.")

    def get(r, k):
        i = c[k]
        return r[i] if i is not None and i < len(r) and r[i] not in (None, "") else None

    n = 0
    for r in it:
        if not r or get(r, "name") is None:
            continue
        pers = str(get(r, "personalizable") or "").strip().lower()
        db.upsert_product(tid, {
            "name": str(get(r, "name")).strip(), "price": float(get(r, "price") or 0),
            "sku": str(get(r, "sku")).strip() if get(r, "sku") is not None else None,
            "stock": int(get(r, "stock")) if get(r, "stock") is not None else None,
            "description": str(get(r, "description") or ""), "variants": str(get(r, "variants") or ""),
            "personalizable": pers in ("si", "sí", "x", "1", "true", "yes"),
            "image_url": str(get(r, "image_url") or ""), "url": str(get(r, "url") or ""),
        })
        n += 1
    return n
