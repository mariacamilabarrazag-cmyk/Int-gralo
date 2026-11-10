"""Asistente interna ("secretaria") con la que conversa la dueña y su equipo.

Puede responder preguntas sobre el negocio (ventas, pedidos, pagos pendientes, conversaciones), ejecutar
acciones (cambiar estados, confirmar pagos, escribirle a un cliente), recibir respuestas a las preguntas
que la IA de clientes dejó pendientes y aprender datos nuevos del negocio.
"""
import json
from datetime import datetime

from . import db, llm, service

TOOLS = [
    {"name": "resumen_del_negocio", "description": "Ingresos de hoy y del mes, por cobrar, pedidos por estado y por canal, pendientes.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "buscar_pedidos", "description": "Lista pedidos filtrando por texto (código, cliente, teléfono), estado o estado de pago.",
     "input_schema": {"type": "object", "properties": {
         "texto": {"type": "string"},
         "estado": {"type": "string", "enum": db.STATUSES},
         "pago": {"type": "string", "enum": db.PAYMENT_STATUSES},
         "limite": {"type": "integer", "minimum": 1, "maximum": 50}}}},
    {"name": "ver_pedido", "description": "Detalle completo de un pedido con su historial.",
     "input_schema": {"type": "object", "properties": {"codigo": {"type": "string"}}, "required": ["codigo"]}},
    {"name": "cambiar_estado_pedido", "description": "Cambia la etapa de un pedido. Por defecto avisa al cliente si el pedido vino por chat.",
     "input_schema": {"type": "object", "properties": {
         "codigo": {"type": "string"}, "estado": {"type": "string", "enum": db.STATUSES},
         "avisar_cliente": {"type": "boolean"}}, "required": ["codigo", "estado"]}},
    {"name": "registrar_pago", "description": "Marca el pago de un pedido (pagado, pendiente, reembolsado).",
     "input_schema": {"type": "object", "properties": {
         "codigo": {"type": "string"}, "estado_pago": {"type": "string", "enum": db.PAYMENT_STATUSES},
         "metodo": {"type": "string"}, "referencia": {"type": "string"}}, "required": ["codigo", "estado_pago"]}},
    {"name": "ver_preguntas_pendientes", "description": "Preguntas que la IA de clientes dejó para la dueña y aún no tienen respuesta.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "responder_pregunta_de_cliente", "description": "Envía la respuesta de la dueña a una pregunta pendiente; la IA retoma la "
     "conversación con el cliente usando esa respuesta. Úsala cuando la dueña te dé la información que pedía una pregunta.",
     "input_schema": {"type": "object", "properties": {"pregunta_id": {"type": "integer"}, "respuesta": {"type": "string"}},
                      "required": ["pregunta_id", "respuesta"]}},
    {"name": "ver_conversaciones", "description": "Conversaciones recientes con clientes (id, canal, cliente, último mensaje, si esperan atención).",
     "input_schema": {"type": "object", "properties": {"solo_pendientes": {"type": "boolean"}}}},
    {"name": "leer_conversacion", "description": "Lee los últimos mensajes de una conversación con un cliente.",
     "input_schema": {"type": "object", "properties": {"conversacion_id": {"type": "integer"}}, "required": ["conversacion_id"]}},
    {"name": "enviar_mensaje_a_cliente", "description": "Envía un mensaje al cliente en nombre de la tienda. Solo cuando la dueña lo pida "
     "explícitamente; muéstrale el texto si no te lo dictó.",
     "input_schema": {"type": "object", "properties": {"conversacion_id": {"type": "integer"}, "texto": {"type": "string"}},
                      "required": ["conversacion_id", "texto"]}},
    {"name": "buscar_productos", "description": "Busca en el catálogo.",
     "input_schema": {"type": "object", "properties": {"consulta": {"type": "string"}}, "required": ["consulta"]}},
    {"name": "guardar_dato_del_negocio", "description": "Agrega un dato permanente a la información de la tienda que usa la IA con los "
     "clientes (precios de personalización, tiempos de entrega, políticas...). Úsala cuando la dueña diga algo como "
     "'de ahora en adelante...' o 'recuerda que...'.",
     "input_schema": {"type": "object", "properties": {"dato": {"type": "string"}}, "required": ["dato"]}},
]


class SecretaryTools:
    def __init__(self, tenant: dict):
        self.t, self.tid = tenant, tenant["id"]

    def __call__(self, name, args):
        fn = getattr(self, name, None)
        if not fn or name.startswith("_"):
            return f"Herramienta desconocida: {name}", True
        return fn(args)

    def _order(self, code):
        o = db.find_order_by_code(self.tid, code or "")
        if not o:
            raise LookupError(f"No existe el pedido {code}")
        return o

    def resumen_del_negocio(self, a):
        return db.to_json(db.stats(self.tid)), False

    def buscar_pedidos(self, a):
        orders = db.list_orders(self.tid, a.get("estado"), a.get("texto"), a.get("pago"), limit=int(a.get("limite") or 20))
        return db.to_json([{"codigo": o["code"], "fecha": o["created_at"][:16], "cliente": o["customer_name"], "canal": o["source"],
                            "estado": o["status"], "pago": o["payment_status"], "total": o["total"],
                            "productos": [f"{i['quantity']}x {i['product_name']}" for i in o["items"]]} for o in orders]), False

    def ver_pedido(self, a):
        try:
            return db.to_json(self._order(a["codigo"])), False
        except LookupError as e:
            return str(e), True

    def cambiar_estado_pedido(self, a):
        try:
            o = service.change_status(self.t, self._order(a["codigo"])["id"], a["estado"], "Desde la asistente",
                                      notify=a.get("avisar_cliente", True))
            return f"{o['code']} ahora está en {db.STATUS_LABELS[o['status']]}.", False
        except (LookupError, ValueError) as e:
            return str(e), True

    def registrar_pago(self, a):
        try:
            o = service.change_payment(self.t, self._order(a["codigo"])["id"], a["estado_pago"], a.get("metodo", ""),
                                       a.get("referencia", ""))
            return f"Pago de {o['code']}: {o['payment_status']}.", False
        except (LookupError, ValueError) as e:
            return str(e), True

    def ver_preguntas_pendientes(self, a):
        qs = db.list_questions(self.tid)
        return db.to_json([{"pregunta_id": q["id"], "pregunta": q["question"], "cliente": q["customer_name"],
                            "canal": q["channel"], "fecha": q["created_at"][:16]} for q in qs]) if qs else "No hay preguntas pendientes.", False

    def responder_pregunta_de_cliente(self, a):
        q = service.answer_owner_question(self.t, int(a["pregunta_id"]), a["respuesta"])
        if not q:
            return "No existe esa pregunta.", True
        return "Respuesta enviada: la IA ya está retomando la conversación con el cliente.", False

    def ver_conversaciones(self, a):
        convs = db.list_conversations(self.tid)
        if a.get("solo_pendientes"):
            convs = [c for c in convs if c["needs_human"] or c["waiting_owner"]]
        return db.to_json([{"conversacion_id": c["id"], "canal": c["channel"], "cliente": c["customer_name"] or c["phone"],
                            "ultimo_mensaje": (c["last_text"] or "")[:120], "fecha": c["last_message_at"],
                            "necesita_humano": bool(c["needs_human"]), "esperando_respuesta_duena": bool(c["waiting_owner"])}
                           for c in convs[:25]]), False

    def leer_conversacion(self, a):
        if not db.get_conversation(self.tid, int(a["conversacion_id"])):
            return "No existe esa conversación.", True
        msgs = db.get_messages(self.tid, int(a["conversacion_id"]), 30)
        return db.to_json([{"de": m["author"], "texto": m["text"], "fecha": m["created_at"][:16]} for m in msgs]), False

    def enviar_mensaje_a_cliente(self, a):
        try:
            ok = service.send_to_customer(self.t, int(a["conversacion_id"]), a["texto"])
        except LookupError as e:
            return str(e), True
        return "Mensaje enviado." if ok else "Mensaje guardado, pero el canal no está conectado: no le llegó al cliente.", False

    def buscar_productos(self, a):
        prods = db.search_products(self.tid, a.get("consulta", ""))
        return db.to_json([{"id": p["id"], "nombre": p["name"], "precio": p["price"], "stock": p["stock"],
                            "variantes": p["variants"]} for p in prods]), False

    def guardar_dato_del_negocio(self, a):
        dato = (a.get("dato") or "").strip()
        if not dato:
            return "Dato vacío.", True
        info = (self.t["business_info"] or "").rstrip()
        db.update_tenant(self.tid, {"business_info": f"{info}\n- {dato}".strip()})
        self.t = db.get_tenant(self.tid)
        return "Guardado: la IA de clientes ya usa este dato.", False


def system_prompt(t: dict, user: dict) -> list[dict]:
    text = f"""Eres {t['assistant_name']}, la asistente personal (como una secretaria) de {t['name']}. Hablas con el equipo interno de la tienda, no con clientes. Además atiendes a los clientes por chat con el mismo nombre, así que cuando un cliente necesita algo que no sabes, le dejas una pregunta a {t['owner_title']} aquí.

Información de la tienda:
<informacion_tienda>
{t['business_info'] or '(sin información)'}
</informacion_tienda>
Moneda: {t['currency']}.

Cómo trabajas:
- Responde en español, breve y concreta, con cifras exactas sacadas de tus herramientas (nunca inventes datos). Puedes usar listas cortas.
- Si la persona te da una respuesta para un cliente (p. ej. "dile que la camiseta personalizada vale 55 mil"), identifica la pregunta pendiente correspondiente con ver_preguntas_pendientes y usa responder_pregunta_de_cliente. Si hay varias que podrían coincidir, pregunta cuál.
- Para acciones que afectan a clientes (cambiar estados, confirmar pagos, enviar mensajes) hazlas cuando te lo pidan claramente y confirma lo que hiciste.
- Si te comparten un dato permanente del negocio, guárdalo con guardar_dato_del_negocio."""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def chat(tenant: dict, user: dict, text: str) -> str:
    """Procesa un mensaje del equipo y devuelve la respuesta de la asistente."""
    tid = tenant["id"]
    db.add_assistant_message(tid, user["id"], "user", text)
    if not llm.available():
        reply = "Todavía no tengo la IA configurada en el servidor (falta ANTHROPIC_API_KEY)."
        db.add_assistant_message(tid, None, "assistant", reply)
        return reply
    history: list[dict] = []
    for m in db.get_assistant_messages(tid, 30):
        if history and history[-1]["role"] == m["role"]:
            history[-1]["content"] += "\n" + m["text"]
        else:
            history.append({"role": m["role"], "content": m["text"]})
    while history and history[0]["role"] != "user":
        history.pop(0)
    pending = db.list_questions(tid)
    ctx = f"Habla contigo: {user['name'] or user['email']}. Fecha y hora: {datetime.now():%Y-%m-%d %H:%M}."
    if pending:
        ctx += " Preguntas de clientes pendientes: " + json.dumps(
            [{"pregunta_id": q["id"], "cliente": q["customer_name"], "pregunta": q["question"]} for q in pending], ensure_ascii=False)
    history[-1]["content"] += f"\n\n<contexto_sistema>{ctx}</contexto_sistema>"
    try:
        reply = llm.run(system_prompt(tenant, user), TOOLS, history, SecretaryTools(tenant), effort="medium") or "Listo."
    except llm.LLMError as e:
        reply = f"⚠️ {e}"
    db.add_assistant_message(tid, None, "assistant", reply)
    return reply
