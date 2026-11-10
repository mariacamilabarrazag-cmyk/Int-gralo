"""Flujos de negocio compartidos por la API, los webhooks y la asistente interna."""
import logging
import threading
import time
from datetime import datetime, timedelta

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


def ai_paused(conv: dict) -> bool:
    return bool(conv.get("ai_paused_until")) and conv["ai_paused_until"] > db.now()


def pause_ai(tenant: dict, cid: int) -> None:
    """Una persona del equipo respondió (desde el panel o desde el celular): la IA espera un rato en
    ese chat para no hablar encima. 0 minutos = no pausar."""
    minutes = int(tenant.get("human_pause_minutes") or 0)
    if minutes > 0:
        until = (datetime.now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
        db.update_conversation(tenant["id"], cid, ai_paused_until=until)


def handle_incoming(tenant: dict, msg: dict) -> dict:
    """Guarda un mensaje entrante de un cliente y, si corresponde, hace que la IA responda en segundo plano."""
    tid = tenant["id"]
    conv = db.get_or_create_conversation(tid, msg["channel"], msg["user_id"], msg.get("name", ""), msg.get("phone", ""),
                                         ai_enabled=bool(tenant["ai_enabled_default"]))
    saved = db.add_message(tid, conv["id"], "in", "cliente", msg["text"], msg.get("media_kind", ""),
                           msg.get("media_ref", ""), external_id=msg.get("external_id"))
    if not saved:  # Meta reenvía el mismo webhook si no respondimos a tiempo
        return conv
    if conv["ai_enabled"] and not conv["needs_human"] and not ai_paused(conv):
        if llm.available():
            in_background(reply_with_ai, tid, conv["id"])
        else:
            db.update_conversation(tid, conv["id"], needs_human=1)
            db.add_message(tid, conv["id"], "internal", "sistema", "⚠️ La IA no está configurada en el servidor: responde manualmente.")
    return conv


def reply_with_ai(tid: int, cid: int) -> None:
    tenant = db.get_tenant(tid)
    delay = int(tenant.get("ai_reply_delay_seconds") or 0)
    if delay > 0:
        time.sleep(min(delay, 600))  # da tiempo a que alguien del equipo responda primero desde el celular
    text = ai_agent.generate_reply(tid, cid)
    if text:
        conv = db.get_conversation(tid, cid)
        result = channels.send_message(tenant, conv["channel"], conv["external_user_id"], text)
        _after_send(tenant, cid, result, ai_agent.last_reply_id(tid, cid))


def _after_send(tenant: dict, cid: int, result: channels.SendResult, message_id: int | None) -> None:
    if result.ok and result.external_id and message_id:
        db.set_message_external_id(tenant["id"], message_id, result.external_id)
    if not result.ok:
        db.add_message(tenant["id"], cid, "internal", "sistema", f"⚠️ {result.error}")


def send_to_customer(tenant: dict, cid: int, text: str, author: str = "humano", pause: bool = True) -> channels.SendResult:
    """Envía un mensaje al cliente en nombre de la tienda. `pause` = pausar la IA (respuestas de personas)."""
    conv = db.get_conversation(tenant["id"], cid)
    if not conv:
        raise LookupError("Conversación no encontrada")
    msg = db.add_message(tenant["id"], cid, "out", author, text)
    result = channels.send_message(tenant, conv["channel"], conv["external_user_id"], text)
    _after_send(tenant, cid, result, msg["id"] if msg else None)
    if pause:
        pause_ai(tenant, cid)
    return result


def change_status(tenant: dict, order_id: int, status: str, note: str = "", notify: bool = True) -> dict:
    order = db.update_status(tenant["id"], order_id, status, note)
    if notify and order.get("conversation_id") and status in STATUS_MESSAGES:
        send_to_customer(tenant, order["conversation_id"], STATUS_MESSAGES[status].format(code=order["code"]),
                         author="sistema_auto", pause=False)
    excel.safe_sync(tenant["id"])
    return order


def change_payment(tenant: dict, order_id: int, payment_status: str, method: str = "", reference: str = "",
                   notify: bool = True) -> dict:
    order = db.update_payment(tenant["id"], order_id, payment_status, method, reference)
    if notify and payment_status == "pagado" and order.get("conversation_id"):
        send_to_customer(tenant, order["conversation_id"],
                         f"¡Confirmamos tu pago del pedido {order['code']}! 💚 Ya lo estamos gestionando.",
                         author="sistema_auto", pause=False)
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
        db.update_conversation(tenant["id"], cid, ai_paused_until=None)  # la dueña quiere que la IA continúe
        conv = db.get_conversation(tenant["id"], cid)
        if conv and conv["ai_enabled"] and not conv["needs_human"] and llm.available():
            in_background(reply_with_ai, tenant["id"], cid)
    return q
