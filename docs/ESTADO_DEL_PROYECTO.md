# Integralo: estado del proyecto y lo que falta

> **Para el asistente de código (Gemini en Antigravity u otro):** lee este archivo completo antes de cambiar nada. Explica qué es Integralo, cómo está construido, qué está terminado, qué está probado y qué falta, en orden de prioridad. Las reglas de la sección 6 son obligatorias.

Última actualización: noviembre de 2026.

---

## 1. Qué es Integralo

Plataforma **multi-tienda** (SaaS) para negocios que venden por internet y por redes sociales. Cada tienda tiene su cuenta, su equipo, su marca y sus datos, separados de las demás.

| Función | Estado |
|---|---|
| Pedidos de la tienda en línea (Tiendanube, Shopify, WooCommerce, web genérica) | ✅ Hecho |
| Panel con seguimiento de cada pedido: Recibido → En preparación → En reparto → Entregado (+ Cancelado), historial y aviso automático al cliente | ✅ Hecho |
| Pagos (pendiente / por verificar / pagado / reembolsado) e ingresos (hoy, mes, por canal, últimos 30 días) | ✅ Hecho |
| Excel automático por tienda (`pedidos.xlsx`) e importación del catálogo desde Excel | ✅ Hecho |
| Bandeja única de mensajes: WhatsApp, Instagram, Facebook Messenger, TikTok (solo recepción) | ✅ Hecho |
| IA de atención al cliente: responde, cotiza con el catálogo, toma pedidos, informa estados, registra pagos reportados y pasa a una persona cuando hace falta | ✅ Hecho (opcional: sin clave de IA todo lo demás funciona) |
| "Asistente/secretaria" interna con nombre configurable (ej. "Mapi"): responde preguntas del negocio, ejecuta acciones y le pregunta a la dueña lo que la IA de clientes no sabe | ✅ Hecho |
| Marca de cada tienda (logo y colores, con contraste automático) | ✅ Hecho |
| WhatsApp con un clic en **coexistencia** (mismo número en la app WhatsApp Business del celular y en Integralo) | ✅ Hecho (falta probar con Meta real) |
| Despliegue gratis con HTTPS automático | ✅ Hecho (instalación real en curso en Google Cloud) |
| Modo **dropshipping con Dropi** | ⏳ Pendiente (diseño listo, ver §5.2) |
| Responder por TikTok | ⏳ Pendiente de acceso aprobado por TikTok |

Primeras tiendas:
- **Hey Map** (Colombia, COP): personalización de camisetas y otros productos, más diseños establecidos en su web de Tiendanube. Asistente "Mapi".
- **Tienda en Chile** (CLP): dropshipping integrada con **Dropi** (ver §5.2).

Repositorio: `github.com/mariacamilabarrazag-cmyk/Int-gralo` (privado).

---

## 2. Arquitectura

- **Backend:** Python 3.12 + FastAPI + SQLite (modo WAL). Sin ORM: SQL directo en `app/db.py`.
- **Frontend:** HTML, CSS y JavaScript **sin dependencias ni build** (`static/`), servido por FastAPI.
- **IA:** API de Anthropic (Claude) mediante el SDK oficial `anthropic`, con herramientas (tool use). Toda la comunicación con el modelo pasa por **`app/llm.py`** (ver §5.3 para cambiar de proveedor).
- **Producción:** Docker + Caddy (HTTPS automático con Let's Encrypt) en un servidor Ubuntu. Ver `deploy/` y `DESPLIEGUE.md`.

### 2.1 Archivos

```
app/
  main.py         Cuentas (registro/login con cookie), API del panel, Tiendanube OAuth, WhatsApp con un clic
  webhooks.py     Webhooks entrantes: /w/<token>/<canal> por tienda; /webhooks/meta y /webhooks/tiendanube de plataforma
  db.py           Esquema SQLite + migraciones automáticas + TODAS las consultas (siempre filtradas por tenant_id)
  security.py     Contraseñas (scrypt), tokens, firma HMAC, cifrado Fernet de credenciales
  config.py       Variables de entorno de la PLATAFORMA (lo de cada tienda vive en la base de datos)
  llm.py          Cliente del modelo de IA y ciclo de herramientas (un solo punto de integración)
  ai_agent.py     IA de atención a clientes: prompt, herramientas, historial, pausas
  secretary.py    Asistente interna de la dueña: prompt y herramientas
  service.py      Flujos compartidos: mensaje entrante → IA, envío al cliente, cambio de estado/pago, respuestas de la dueña
  whatsapp.py     WhatsApp: Embedded Signup, coexistencia (ecos, historial, contactos), webhooks, renovación de tokens
  channels.py     Envío por canal (WhatsApp, Messenger/Instagram, TikTok) + verificación de firmas + parseo Meta/TikTok
  tiendanube.py   OAuth, registro de webhooks, mapeo de pedidos/productos, sincronización
  web_orders.py   Mapeo de pedidos de Shopify, WooCommerce y web genérica
  excel.py        Excel por tienda e importación de catálogo
static/           index.html, login.html, app.js (todo el panel), styles.css
scripts/          crear_tienda.py (alta de tiendas por consola), respaldo.py (copia segura de SQLite)
deploy/           docker-compose.yml, Caddyfile, instalar.sh, actualizar.sh
DESPLIEGUE.md     Guía paso a paso para publicar gratis (Google Cloud u Oracle)
```

### 2.2 Modelo de datos (`app/db.py`)

| Tabla | Contenido |
|---|---|
| `tenants` | Tienda: nombre, `assistant_name`, `owner_title`, colores, logo, moneda, `business_info` (lo que sabe la IA), `webhook_token` (URL secreta), `tiendanube_store_id`, `secrets_enc` (credenciales cifradas), datos de WhatsApp (`whatsapp_mode`, `whatsapp_phone_number_id`, `whatsapp_waba_id`, `smb_sync`…), `human_pause_minutes`, `ai_reply_delay_seconds` |
| `users`, `sessions` | Usuarios por tienda (rol `dueno` o `equipo`) y sesiones (se guarda solo el hash del token) |
| `products` | Catálogo: precio, stock, variantes, `personalizable`, `external_id` (ej. `tn-123` de Tiendanube), imagen, URL |
| `orders`, `order_items`, `order_events` | Pedidos con numeración por tienda (`PED-00001`), detalle por producto (`details` = talla, color, diseño…) e historial de estados y pagos |
| `conversations`, `messages` | Chats por canal. `messages.direction`: `in`, `out` o `internal` (no lo ve el cliente). `author`: `cliente`, `ia`, `humano`, `celular` (respondido desde la app del teléfono), `sistema`, `sistema_auto`, `duena`. `external_id` = wamid (evita duplicados) |
| `owner_questions` | Preguntas que la IA de clientes le deja a la dueña, con su respuesta |
| `assistant_messages` | Chat de la dueña con su asistente |
| `contacts` | Libreta de contactos (de la app del celular en coexistencia) para nombrar conversaciones |

Las columnas nuevas se agregan con la lista `MIGRATIONS` de `db.py`: **nunca borres ni renombres columnas**; agrega migraciones.

### 2.3 Flujos clave

1. **Pedido web:** la plataforma envía un webhook → `webhooks.py` verifica la firma → `web_orders.py`/`tiendanube.py` lo convierten → `db.create_order` (sin duplicar por `external_id`) → Excel.
2. **Mensaje de un cliente:** el webhook llega → `service.handle_incoming` lo guarda (sin duplicar por wamid). Si la IA está activa y no está en pausa, `service.reply_with_ai` corre en segundo plano (con espera opcional) → `ai_agent.generate_reply` → `channels.send_message`.
3. **La IA no sabe algo** (ej. cotizar un diseño personalizado): usa la herramienta `preguntar_a_la_duena` → crea `owner_questions` → le dice al cliente que va a confirmar. La dueña responde desde el panel o le dice a su asistente "dile que vale 38 mil" → `service.answer_owner_question` deja una nota interna → la IA retoma el chat. La IA solo puede crear pedidos con precio fuera del catálogo si existe una respuesta de la dueña en esa conversación.
4. **Coexistencia WhatsApp:**
   - Lo que la dueña responde desde el celular llega como `smb_message_echoes` → se guarda como `celular` → la IA hace una pausa en ese chat (`human_pause_minutes`). Si la IA estaba generando una respuesta en ese momento, la descarta.
   - `history` y `smb_app_state_sync` importan los chats anteriores y los nombres de los contactos (Meta solo acepta pedirlos una vez, dentro de las 24 h de la conexión).
5. **Avisos al cliente:** al cambiar el estado o confirmar un pago se envía un mensaje automático (`sistema_auto`), que no pausa la IA.

---

## 3. Cómo ejecutar

### Local
```bash
cp .env.example .env
```
```bash
./iniciar.sh
```
Abre `http://localhost:8000/registro`. Sin `ANTHROPIC_API_KEY` la IA queda desactivada y los mensajes se marcan para atención humana. En **Mensajes → 🧪 Probar IA** se simula un cliente sin conectar redes.

### Variables de entorno de la plataforma (`.env.example`)
| Variable | Para qué |
|---|---|
| `PUBLIC_URL`, `DOMAIN` | URL pública (https) y dominio para Caddy |
| `SECRET_KEY` | Cifra credenciales y firma sesiones. **No cambiarla nunca después de empezar** |
| `ALLOW_SIGNUP` | `true` = autorregistro en `/registro`; `false` = solo `scripts/crear_tienda.py` |
| `ANTHROPIC_API_KEY`, `CLAUDE_MODEL` | IA (opcional). Por defecto `claude-opus-5-5`; la opción económica es `claude-haiku-5-5` |
| `META_APP_ID`, `META_APP_SECRET`, `META_CONFIG_ID`, `META_VERIFY_TOKEN`, `META_SIGNUP_EXTRAS` | App de Integralo en Meta para WhatsApp con un clic + coexistencia |
| `TIENDANUBE_APP_ID`, `TIENDANUBE_CLIENT_SECRET`, `TIENDANUBE_USER_AGENT` | App de Integralo en el Portal de Socios de Tiendanube |

Las credenciales de cada tienda (tokens de WhatsApp, Instagram, Shopify, etc.) **no** van en `.env`: se guardan cifradas desde **Configuración → Canales**.

---

## 4. Qué está probado y qué no

**Probado en este desarrollo (con datos simulados):**
- Aislamiento entre tiendas: una tienda no puede leer pedidos de otra (404).
- Webhooks de Tiendanube con firma válida e inválida, pedido creado → pagado, mapeo de productos con variantes.
- Webhook genérico, Shopify y WooCommerce (mapeo).
- Ciclo completo "cliente pide cotización → IA pregunta a la dueña → la dueña responde vía asistente → IA retoma → pedido con precio acordado", con respuestas del modelo simuladas.
- Coexistencia, con webhooks de Meta simulados:
  - Embedded Signup sin `/register`, suscripción de la WABA y las dos sincronizaciones.
  - Ecos del celular con pausa de la IA, sin duplicados y descartando la respuesta de la IA si alguien respondió mientras pensaba.
  - Importación del historial y de los contactos.
  - Error de 24 h y `PARTNER_REMOVED`.
- Panel revisado con capturas en escritorio y móvil (sin desbordes).
- Script de respaldo y funciones del instalador que editan `.env`.

**NO probado contra servicios reales** (no había credenciales ni acceso de red):
- Llamadas reales a Claude.
- Meta: Embedded Signup real, formato exacto de `smb_message_echoes` y `history`, envío real por WhatsApp e Instagram. Los formatos se tomaron de un proyecto de código abierto que ya funciona con coexistencia (`brianpolanco25/wacrm`), porque la documentación de Meta no era accesible.
- Tiendanube real: URL de autorización (`TIENDANUBE_AUTH_URL`), versión de API `2025-03` y nombres de campos tomados de integraciones públicas.
- `deploy/instalar.sh` completo en un servidor (sintaxis revisada; la ejecución real está en curso).

> ⚠️ **No hay pruebas automatizadas** (pytest) en el repositorio: las pruebas se hicieron con scripts temporales. Crear la carpeta `tests/` es una de las primeras tareas (§5.1).

---

## 5. Lo que falta, por prioridad

### 5.1 Inmediato
1. **Terminar la instalación en Google Cloud** (ver `DESPLIEGUE.md`, sección Google Cloud). Ya están creados el servidor e2-micro en `us-east1` y el presupuesto de alerta de USD 1. Faltan: DuckDNS, el token de GitHub, `bash deploy/instalar.sh` (dejando la clave de IA vacía por ahora) y crear la tienda Hey Map.
2. **Activar la cuenta completa de Google Cloud antes de los 90 días** para que no se apague el servidor (sigue costando $0 dentro del nivel gratuito).
3. **Pruebas automatizadas con pytest** (`tests/`), usando `fastapi.testclient.TestClient` y un `DATA_DIR` temporal. Mínimo:
   - aislamiento entre tiendas;
   - creación de pedidos sin duplicados;
   - webhooks con firma (Tiendanube, Meta);
   - flujo de coexistencia;
   - ciclo de herramientas de la IA con un cliente simulado (sustituir `llm._client` por un objeto falso).
4. **Configurar la tienda Hey Map:** logo, colores reales y la "información del negocio". La plantilla del preset `personalizacion` tiene campos `[completar]`.

### 5.2 Modo dropshipping con Dropi (tienda de Chile)
**Objetivo:** que Integralo sirva igual para tiendas propias y para dropshippers conectados a Dropi, en cualquier país.

Diseño acordado:
- **Tipo de tienda** en `tenants` (nueva migración): `business_type` = `propia` | `dropshipping`, y `dropi_country` (`co`, `cl`, `mx`…).
- **Etapas para dropshipping:** Recibido → **Confirmado por el cliente** → **Guía generada** → En tránsito → Entregado, más **Novedad** y **Devolución**. Implementarlo como etapas por tipo de tienda, sin romper las actuales (`STATUSES` está en `db.py`, `FLOW` en `static/app.js`).
- **Campos de envío** en `orders`: `carrier` (transportadora), `tracking_number` (guía), `tracking_url`, `shipping_status`, `cod` (contra entrega, sí/no).
- **Confirmación de pedidos contra entrega por WhatsApp:** al entrar un pedido, la IA escribe al cliente para confirmar dirección y que va a recibir, y marca *Confirmado* o *Cancelado*. Ojo: escribir primero a un cliente por WhatsApp requiere una **plantilla aprobada por Meta** (ver 5.4).
- **Ganancia en vez de ingresos:** `cost_price` (costo Dropi) por producto o ítem, `shipping_cost` real, y ganancia = venta − costo − flete. Agregar el porcentaje de devoluciones al resumen.
- **Herramientas de la IA:** `consultar_pedidos` debe devolver transportadora, guía y estado de envío.
- **Integración con la API de Dropi:**
  - **Solo con la API oficial.** No usar el login del panel con usuario, contraseña y 2FA ni imitar el navegador: hay proyectos no oficiales que lo hacen, pero pueden bloquear la cuenta y se rompen con cada cambio.
  - Según fuentes secundarias, las credenciales se generan en el panel de Dropi (**Configuración → Desarrolladores**: API Key y Secret). Los servicios de integración usan un encabezado `dropi-integration-key`. Servidores mencionados: `https://api.dropi.co`, pruebas en `test-api.dropi.co`, documentación en `https://dev.dropi.co`. **Nada de esto está verificado**: pedir a la dueña una captura de esa sección y, con la documentación oficial a la vista, implementar un `app/dropi.py` (sincronizar estados y guías, crear pedidos si la tienda no los envía sola) siguiendo el patrón de `tiendanube.py`.
  - Credenciales de Dropi: agregar la clave a `db.SECRET_KEYS` (cifrada).
- **Pendiente de la dueña:** confirmar en qué plataforma está la tienda de Chile (¿Shopify? ¿Tiendanube?) y mandar la captura de la sección de API de Dropi.

### 5.3 IA: costo y proveedor
- Hoy la IA usa Claude mediante `app/llm.py` (función `run(system, tools, messages, execute, effort)`). Si no hay `ANTHROPIC_API_KEY`, la IA queda apagada y el resto sigue funcionando.
- **Opción económica sin cambiar código:** `CLAUDE_MODEL=claude-haiku-5-5` (aprox. USD 0,50 por cada 1.000 mensajes).
- **Para usar otro proveedor** (por ejemplo Gemini), el cambio va **solo en `app/llm.py`**. Hay que hacer cuatro cosas:
  1. Implementar la misma función `run` con el SDK del nuevo proveedor.
  2. Convertir el formato de herramientas (las listas `TOOLS` de `ai_agent.py` y `secretary.py` usan JSON Schema en `input_schema`).
  3. Convertir el historial (`role`, `content`) y los resultados de herramientas.
  4. Mantener el contrato: devolver el texto final y lanzar `LLMError` con un mensaje en español.
- **Privacidad:** en los planes gratuitos de algunos proveedores, las conversaciones pueden usarse para entrenar modelos. Aquí son datos de clientes (nombres, teléfonos, direcciones): revisar los términos antes de usar un plan gratis en producción.
- Las instrucciones de la IA (prompts) están en `ai_agent.system_prompt` y `secretary.system_prompt`.

### 5.4 Canales
1. **Meta (WhatsApp con un clic + coexistencia):** crear la app de Integralo en developers.facebook.com, verificar el negocio y completar el proceso de **Proveedor de Tecnología**. Después:
   - crear la configuración de *Embedded Signup* con coexistencia;
   - configurar el webhook `https://DOMINIO/webhooks/meta` con los campos `messages`, `smb_message_echoes`, `history`, `smb_app_state_sync` y `account_update`;
   - conectar el número real y **verificar los formatos** contra lo que llegue. Se recomienda registrar temporalmente el payload crudo, sin datos personales en los logs de producción.
2. **Plantillas de WhatsApp:** para escribir fuera de la ventana de 24 h (confirmación de pedidos contra entrega, avisos de "tu pedido va en camino" tardíos). Falta implementar el envío de plantillas y su configuración por tienda. Hoy, si pasaron más de 24 h, el panel lo avisa y sugiere responder desde el celular.
3. **Instagram y Messenger:** funcionan con conexión manual (token de página). Para clientes reales, Meta exige la revisión de la app (`pages_messaging`, `instagram_manage_messages`).
4. **TikTok:** la recepción está lista (`/w/<token>/tiktok`, parser genérico). Falta `channels._send_tiktok` cuando la cuenta tenga acceso aprobado a *TikTok Business Messaging*: cuenta Business vinculada a TikTok for Business / Business Center. Disponible en Latinoamérica, no en la UE ni el Reino Unido. Ajustar `parse_tiktok` al formato real.
5. **Tiendanube:** registrar la app de Integralo en el Portal de Socios. Configurar la URL de redirección `https://DOMINIO/tiendanube/callback` y los webhooks de privacidad hacia `/webhooks/tiendanube`, y probar con Hey Map. Verificar la URL de autorización y los campos `shipping_cost_customer`, `contact_*` y `variant_values`.

### 5.5 Mejoras planeadas
- **Avisar a la dueña** (push o WhatsApp a su número personal) cuando haya una pregunta pendiente o un chat que necesite atención humana.
- Pasar a la IA las **imágenes** que mandan los clientes (por ejemplo, el logo a estampar) para que las describa y cotice mejor.
- **Respaldos fuera del servidor** (Google Drive o almacenamiento en la nube). Hoy quedan en el mismo disco.
- Límite de intentos de inicio de sesión **persistente**: hoy está en memoria y se reinicia con la app.
- Si crece mucho el número de tiendas: pasar de SQLite a PostgreSQL y mover el procesamiento en segundo plano (hilos) a una cola.
- Facturación y planes de Integralo para cobrar a las tiendas.

---

## 6. Reglas del proyecto (obligatorias)

1. **Aislamiento entre tiendas:** toda función de `db.py` recibe `tid` y **todas** las consultas filtran por `tenant_id`. Nunca buscar un registro solo por su `id`.
2. **Credenciales:** las de cada tienda van en `db.SECRET_KEYS` (cifradas con `security.encrypt_json`). Nunca en texto plano, en logs ni en respuestas de la API (`public_tenant` solo expone si están configuradas, no su valor).
3. **Webhooks:** siempre verificar la firma cuando la plataforma la ofrece, responder rápido y procesar en segundo plano. No duplicar: usar `external_id`.
4. **Base de datos:** cambios de esquema solo como migración en `MIGRATIONS` (agregar columnas; no borrar ni renombrar).
5. **Panel:** todo texto visible en **español**. Escapar siempre con `esc()` en `app.js` los datos que vengan de clientes. Sin librerías externas en el frontend. Los colores de marca pasan por `applyBrand` (ajusta el contraste solo). Los estados nunca se indican solo con color: siempre ícono + texto.
6. **La IA nunca inventa precios:** solo el catálogo o una respuesta de la dueña. Antes de crear un pedido, la IA confirma con el cliente.
7. **No usar APIs no oficiales** ni automatizar paneles con usuario y contraseña (WhatsApp Web, el panel de Dropi, etc.). Pueden bloquear las cuentas de las tiendas.
8. **Después de cada cambio:** correr las pruebas, revisar el panel en el navegador y actualizar este documento.

---

## 7. Historial de decisiones
- **Python + FastAPI + SQLite:** simple de instalar en un servidor gratis de 1 GB de RAM. El frontend va sin build para que cualquier persona pueda modificarlo.
- **Multi-tienda desde el inicio:** Integralo es para cualquier negocio, no solo para Hey Map.
- **WhatsApp por API oficial con coexistencia**, en lugar de librerías tipo WhatsApp Web: estas violan los términos de WhatsApp y arriesgan el bloqueo del número.
- **Hosting:** Oracle Cloud bloqueó el registro (error en el método de pago), así que se usa **Google Cloud e2-micro (us-east1)**, gratis dentro del nivel *Always Free*. El instalador sirve para cualquier Ubuntu.
- **IA opcional:** la tienda puede empezar gratis respondiendo ella misma y activar la IA después.
