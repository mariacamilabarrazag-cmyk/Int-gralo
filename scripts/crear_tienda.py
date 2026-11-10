"""Crea una tienda (cliente de Integralo) con su usuario dueño.

Uso:
    python -m scripts.crear_tienda --nombre "Hey Map" --email dueña@correo.com --asistente Mapi --preset personalizacion

La contraseña se pide por consola (no queda en el historial). Útil cuando ALLOW_SIGNUP=false
y es Integralo quien da de alta a cada cliente.
"""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402

PRESETS = {
    "personalizacion": """Somos una tienda de personalización de camisetas y otros productos.
Tenemos dos tipos de productos:
1) Diseños establecidos: los que están publicados en nuestra página web, con precio fijo.
2) Productos personalizados: el cliente nos envía su idea, logo, foto o texto y lo estampamos. El precio final depende de la cantidad, el tamaño y el número de colores del diseño, así que siempre se confirma con la dueña antes de dar un precio.
Productos que personalizamos: [completar: camisetas, buzos, gorras, mugs, tote bags…]
Tallas disponibles: [completar]
Colores de prenda: [completar]
Tiempo de producción: [completar, p. ej. 3 a 5 días hábiles después de aprobar el diseño y el pago]
Para personalizar pedimos: imagen o logo en buena calidad, texto exacto, ubicación del estampado (frente, espalda, manga) y tallas por cantidad.
Envíos: [completar ciudades, costo y tiempos]
Medios de pago: [completar: Nequi, Daviplata, Bancolombia, pago en la web…]
Pedidos personalizados: [completar, p. ej. se pide el 50% de anticipo]
Cambios y devoluciones: los productos personalizados no tienen cambio, salvo defecto de fabricación.
Horario de atención: [completar]""",
}


def main():
    ap = argparse.ArgumentParser(description="Crear una tienda en Integralo")
    ap.add_argument("--nombre", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--nombre-duena", default="")
    ap.add_argument("--asistente", default="Asistente")
    ap.add_argument("--como-te-llama", default="la dueña")
    ap.add_argument("--color-principal", default=None)
    ap.add_argument("--color-secundario", default=None)
    ap.add_argument("--preset", choices=sorted(PRESETS), default=None)
    args = ap.parse_args()

    db.init_db()
    if db.get_user_by_email(args.email):
        sys.exit("Ya existe un usuario con ese correo.")
    password = getpass.getpass("Contraseña para la dueña (mín. 8 caracteres): ")
    if len(password) < 8:
        sys.exit("La contraseña debe tener al menos 8 caracteres.")

    fields = {"assistant_name": args.asistente, "owner_title": args.como_te_llama}
    if args.color_principal:
        fields["brand_primary"] = args.color_principal
    if args.color_secundario:
        fields["brand_secondary"] = args.color_secundario
    if args.preset:
        fields["business_info"] = PRESETS[args.preset]
    tenant = db.create_tenant(args.nombre, **fields)
    db.create_user(tenant["id"], args.email, args.nombre_duena or args.nombre, password, role="dueno")
    print(f"✓ Tienda «{tenant['name']}» creada (id {tenant['id']}, slug {tenant['slug']}). Ya puede entrar con {args.email}.")


if __name__ == "__main__":
    main()
