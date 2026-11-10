"""Flujos de negocio compartidos por la API, los webhooks y la asistente interna."""
import logging
import threading

from . import ai_agent, channels, db, excel, llm

log = logging.getLogger("service")

STATUS_MESSAGES = {
    "preparacion": "¡Hola! Tu pedido {code} ya está en preparación ✨",
    "reparto": "¡Tu pedido {code} va en camino! 🛵 Pronto llegará a tu dirección.",
    "entregado": "Tu pedido {code} fue entregado ✅ ¡Gracias por tu compra!",
    "cancelado": "Tu pedido {code} fue cancelado. Si tienes dudas, escríbenos.",
}


def in_background(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()


def handle_incoming(tenant: dict, msg: dict) -> dict:
    """Guarda un mensaje entrante de un cliente y, si corresponde, hace que la IA responda en segundo plano."""
    tid = tenant["id"]
    conv = db.get_or_create_conversation(tid, msg["channel"], msg["user_id"], msg.get("name", ""), msg.get("phone", ""),
                                         ai_enabled=bool(tenant["ai_enabled_default"]))
    db.add_message(tid, conv["id"], "in", "cliente", msg["text"], msg.get("media_kind", ""), msg.get("media_ref", ""))
    if conv["ai_enabled"] and not conv["needs_human"]:
        if llm.available():
            in_background(reply_with_ai, tid, conv["id"])
        else:
            db.update_conversation(tid, conv["id"], needs_human=1)
            db.add_message(tid, conv["id"], "internal", "sistema", "⚠️ La IA no está configurada en el servidor: responde manualmente.")
    return conv


def reply_with_ai(tid: int, cid: int) -> None:
    text = ai_agent.generate_reply(tid, cid)
    if text:
        tenant = db.get_tenant(tid)
        conv = db.get_conversation(tid, cid)
        if not channels.send_message(tenant, conv["channel"], conv["external_user_id"], text):
            db.add_message(tid, cid, "internal", "sistema", "⚠️ La respuesta no se pudo enviar al canal; revisa Configuración → Canales.")


def send_to_customer(tenant: dict, cid: int, text: str, author: str = "humano") -> bool:
    conv = db.get_conversation(tenant["id"], cid)
    if not conv:
        raise LookupError("Conversación no encontrada")
    db.add_message(tenant["id"], cid, "out", author, text)
    return channels.send_message(tenant, conv["channel"], conv["external_user_id"], text)


def change_status(tenant: dict, order_id: int, status: str, note: str = "", notify: bool = True) -> dict:
    order = db.update_status(tenant["id"], order_id, status, note)
    if notify and order.get("conversation_id") and status in STATUS_MESSAGES:
        send_to_customer(tenant, order["conversation_id"], STATUS_MESSAGES[status].format(code=order["code"]))
    excel.safe_sync(tenant["id"])
    return order


def change_payment(tenant: dict, order_id: int, payment_status: str, method: str = "", reference: str = "",
                   notify: bool = True) -> dict:
    order = db.update_payment(tenant["id"], order_id, payment_status, method, reference)
    if notify and payment_status == "pagado" and order.get("conversation_id"):
        send_to_customer(tenant, order["conversation_id"],
                         f"¡Confirmamos tu pago del pedido {order['code']}! 💚 Ya lo estamos gestionando.")
    excel.safe_sync(tenant["id"])
    return order


def answer_owner_question(tenant: dict, qid: int, answer: str) -> dict | None:
    """La dueña responde una pregunta de la IA: la respuesta llega a la conversación como nota interna
    y la IA retoma la conversación con el cliente."""
    q = db.answer_question(tenant["id"], qid, answer)
    if not q:
        return None
    cid = q["conversation_id"]
    if cid:
        db.add_message(tenant["id"], cid, "internal", "duena", f"Sobre «{q['question']}»: {answer}")
        conv = db.get_conversation(tenant["id"], cid)
        if conv and conv["ai_enabled"] and not conv["needs_human"] and llm.available():
            in_background(reply_with_ai, tenant["id"], cid)
    return q
