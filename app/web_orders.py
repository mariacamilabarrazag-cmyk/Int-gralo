"""Convierte pedidos que llegan desde la página web al formato interno."""


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def from_shopify(p: dict) -> dict:
    ship = p.get("shipping_address") or p.get("billing_address") or {}
    cust = p.get("customer") or {}
    name = ship.get("name") or f"{cust.get('first_name', '')} {cust.get('last_name', '')}".strip()
    paid = p.get("financial_status") == "paid"
    return {
        "source": "web",
        "external_id": f"shopify-{p.get('id')}",
        "customer_name": name,
        "phone": ship.get("phone") or p.get("phone") or cust.get("phone") or "",
        "email": p.get("email") or cust.get("email") or "",
        "address": " ".join(x for x in [ship.get("address1"), ship.get("address2")] if x),
        "city": ship.get("city", ""),
        "notes": f"Shopify {p.get('name', '')} {p.get('note') or ''}".strip(),
        "shipping": sum(_f(s.get("price")) for s in p.get("shipping_lines", [])),
        "total": _f(p.get("total_price")),
        "payment_status": "pagado" if paid else "pendiente",
        "payment_method": ", ".join(p.get("payment_gateway_names") or []),
        "items": [{"product_name": li.get("title", ""), "sku": li.get("sku") or "", "quantity": li.get("quantity", 1),
                   "unit_price": _f(li.get("price")),
                   "details": ", ".join(f"{m.get('key', m.get('name'))}: {m.get('value')}" for m in li.get("meta_data", li.get("properties", [])) or []
                                        if isinstance(m, dict) and not str(m.get("key", m.get("name", ""))).startswith("_"))}
                  for li in p.get("line_items", [])],
    }


def from_woocommerce(p: dict) -> dict:
    ship = p.get("shipping") or {}
    bill = p.get("billing") or {}
    name = f"{ship.get('first_name') or bill.get('first_name', '')} {ship.get('last_name') or bill.get('last_name', '')}".strip()
    paid = bool(p.get("date_paid")) or p.get("status") in ("processing", "completed")
    return {
        "source": "web",
        "external_id": f"woo-{p.get('id')}",
        "customer_name": name,
        "phone": bill.get("phone", ""),
        "email": bill.get("email", ""),
        "address": " ".join(x for x in [ship.get("address_1") or bill.get("address_1"), ship.get("address_2") or bill.get("address_2")] if x),
        "city": ship.get("city") or bill.get("city", ""),
        "notes": p.get("customer_note", ""),
        "shipping": _f(p.get("shipping_total")),
        "total": _f(p.get("total")),
        "payment_status": "pagado" if paid else "pendiente",
        "payment_method": p.get("payment_method_title", ""),
        "items": [{"product_name": li.get("name", ""), "sku": li.get("sku") or "", "quantity": li.get("quantity", 1),
                   "unit_price": _f(li.get("price")),
                   "details": ", ".join(f"{m.get('key', m.get('name'))}: {m.get('value')}" for m in li.get("meta_data", li.get("properties", [])) or []
                                        if isinstance(m, dict) and not str(m.get("key", m.get("name", ""))).startswith("_"))}
                  for li in p.get("line_items", [])],
    }


def from_generic(p: dict) -> dict:
    """Formato propio para cualquier web (Wix, Tiendanube, formulario, etc.):
    {"id": "123", "cliente": "Ana", "telefono": "...", "direccion": "...", "ciudad": "...", "notas": "...",
     "envio": 8000, "pagado": false, "metodo_pago": "Nequi",
     "productos": [{"nombre": "Camiseta", "cantidad": 2, "precio": 45000, "sku": "CAM-01"}]}"""
    return {
        "source": "web",
        "external_id": f"web-{p['id']}" if p.get("id") else None,
        "customer_name": p.get("cliente") or p.get("nombre", ""),
        "phone": p.get("telefono", ""),
        "email": p.get("email", ""),
        "address": p.get("direccion", ""),
        "city": p.get("ciudad", ""),
        "notes": p.get("notas", ""),
        "shipping": _f(p.get("envio")),
        "total": p.get("total"),
        "payment_status": "pagado" if p.get("pagado") else "pendiente",
        "payment_method": p.get("metodo_pago", ""),
        "items": [{"product_name": i.get("nombre", ""), "sku": i.get("sku", ""), "quantity": i.get("cantidad", 1),
                   "unit_price": _f(i.get("precio")),
                   "details": i.get("detalle", "")} for i in p.get("productos", [])],
    }
