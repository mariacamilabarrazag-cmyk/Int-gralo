# Integralo

**Integralo** reúne en un solo panel todo lo que una tienda necesita para vender por internet y por redes:

- 🛍️ **Pedidos de la tienda en línea**: Tiendanube (conexión con un clic), Shopify, WooCommerce o cualquier web.
- 💬 **Atención al cliente con IA** en WhatsApp, Instagram, Facebook y TikTok. Responde, toma pedidos y avisa a cada cliente cómo va su pedido.
- 🤝 **Una asistente para la dueña** (con el nombre que ella elija). Le puedes preguntar "¿cuánto vendí hoy?" o pedirle "confirma el pago de PED-00012". Cuando un cliente pregunta algo que la IA no sabe (por ejemplo, cuánto vale un diseño personalizado), ella te consulta, tú respondes y ella sigue la conversación con el cliente.
- 📦 **Seguimiento de cada pedido**: Recibido → En preparación → En reparto → Entregado, con historial y aviso automático al cliente.
- 💰 **Pagos e ingresos**: confirmas los pagos y ves tus ingresos (hoy, mes, por canal, últimos 30 días).
- 📊 **Excel automático** con todos los pedidos.
- 🎨 **Con tu marca**: el panel usa tu logo y tus colores.

Es **multi-tienda**: cada negocio tiene su propia cuenta, su equipo y sus datos, separados de los demás.

---

> 📋 **Estado del proyecto, decisiones y pendientes:** [docs/ESTADO_DEL_PROYECTO.md](docs/ESTADO_DEL_PROYECTO.md)

## Índice
1. [Instalar y probar en tu computador](#1-instalar-y-probar-en-tu-computador)
2. [Publicar en internet](#2-publicar-en-internet)
3. [Configurar la plataforma (una sola vez)](#3-configurar-la-plataforma-una-sola-vez)
4. [Dar de alta una tienda](#4-dar-de-alta-una-tienda-ej-hey-map)
5. [Conectar los canales de cada tienda](#5-conectar-los-canales-de-cada-tienda)
6. [Cómo se usa el día a día](#6-cómo-se-usa-el-día-a-día)
7. [Estructura del código](#7-estructura-del-código)

---

## 1. Instalar y probar en tu computador

Necesitas Python 3.10 o más reciente.

```bash
cp .env.example .env      # y completa ANTHROPIC_API_KEY
./iniciar.sh              # en Windows: doble clic en iniciar.bat
```

Abre http://localhost:8000/registro y crea la cuenta de una tienda de prueba. En **Mensajes → 🧪 Probar IA** puedes hacerte pasar por un cliente y ver cómo responde la asistente, sin conectar ninguna red.

## 2. Publicar en internet

Las redes sociales y Tiendanube necesitan una dirección pública con **https**.

👉 **Guía paso a paso para publicarla gratis (Google Cloud u Oracle): [DESPLIEGUE.md](DESPLIEGUE.md)**. Un script (`deploy/instalar.sh`) instala todo: Docker, HTTPS automático con Caddy, subdominio gratis de DuckDNS y respaldos diarios.

Si usas otro proveedor con Docker (Railway, Render con disco, un VPS), monta un **volumen persistente** en `/data` y define las variables del `.env.example`. Como mínimo: `PUBLIC_URL`, `SECRET_KEY` y `ANTHROPIC_API_KEY`.

> Si cambias `SECRET_KEY` después de empezar, se pierden las credenciales guardadas de todas las tiendas y las sesiones abiertas.

## 3. Configurar la plataforma (una sola vez)

### IA (Claude)
Crea la clave en https://console.anthropic.com y ponla en `ANTHROPIC_API_KEY`. La plataforma usa el modelo `claude-opus-5-5`, con esfuerzo bajo para los chats con clientes (respuestas rápidas) y medio para la asistente interna. Si el modelo declina un mensaje, el servidor reintenta con un modelo alternativo. Si aun así no hay respuesta, la conversación pasa a una persona.

### WhatsApp con un clic y en coexistencia (app de Meta de Integralo)
Así cada tienda conecta **su mismo número** sin perder la app **WhatsApp Business** del celular:
contesta desde el celular cuando quiera, todo se ve en Integralo y la IA se calla en ese chat mientras la persona atiende.

1. En https://developers.facebook.com crea una app tipo **Empresa (Business)** y agrega el producto **WhatsApp**.
2. Completa la **verificación del negocio** y el proceso de **Proveedor de Tecnología (Tech Provider)** que pide Meta para usar *Embedded Signup* con otras empresas.
3. En **Facebook Login for Business → Configuraciones**, crea una configuración de **WhatsApp Embedded Signup**. Activa la opción de incorporar usuarios de la app WhatsApp Business (coexistencia) y copia su **ID** en `META_CONFIG_ID`.
4. En **Configuración de la app → Básica**, agrega tu dominio en *Dominios de la app* y copia el **ID de la app** en `META_APP_ID` y la **clave secreta** en `META_APP_SECRET`.
5. En **WhatsApp → Configuración → Webhook**:
   - URL: `https://TU-DOMINIO/webhooks/meta`
   - Token de verificación: el valor de `META_VERIFY_TOKEN`
   - Suscríbete a los campos `messages`, `smb_message_echoes`, `history`, `smb_app_state_sync` y `account_update`.
6. Reinicia Integralo. En **Configuración → Canales**, cada tienda verá el botón **Conectar mi WhatsApp**.

Al conectar en modo coexistencia:
- Integralo pide automáticamente los **contactos** y el **historial** (hasta ~6 meses) del celular. Meta solo permite hacerlo **una vez y dentro de las 24 horas** siguientes.
- Los mensajes que respondes **desde el celular** aparecen como "📱 Desde el celular", y la IA hace una pausa en ese chat. Los minutos de pausa se ajustan en *Tu asistente*.
- Si pasaron más de 24 horas desde el último mensaje del cliente, WhatsApp no deja que la API le escriba sin una plantilla aprobada. Integralo te avisa y puedes responderle desde el celular, que no tiene esa limitación.
- Meta pide abrir la app WhatsApp Business del celular al menos una vez cada **14 días** para mantener la coexistencia activa.
- Los mensajes que envía la API tienen el costo de Meta. Los que envías desde la app del celular siguen siendo gratis.

> Sin la app de plataforma, cada tienda puede conectar WhatsApp **manualmente** con su propia app de Meta (Configuración → Canales → "Conexión manual").

### Tiendanube (para que cada tienda se conecte con un clic)
1. Regístrate como socio en https://partners.tiendanube.com y crea una **aplicación**.
2. Permisos: `read_orders`, `read_products` (y `read_customers` si quieres).
3. **URL de redirección**: `https://TU-DOMINIO/tiendanube/callback`
4. **Webhooks de privacidad (LGPD)**: `store/redact`, `customers/redact` y `customers/data_request` → `https://TU-DOMINIO/webhooks/tiendanube`
5. Copia el **ID de la app** en `TIENDANUBE_APP_ID` y el **Client Secret** en `TIENDANUBE_CLIENT_SECRET`.

Integralo registra solo los webhooks de pedidos y productos de cada tienda cuando esta se conecta.

## 4. Dar de alta una tienda (ej. Hey Map)

**Opción A (autoservicio):** la tienda entra a `https://TU-DOMINIO/registro` y crea su cuenta.

**Opción B (la creas tú):** pon `ALLOW_SIGNUP=false` y ejecuta:

```bash
python -m scripts.crear_tienda --nombre "Hey Map" --email correo-de-la-duena@ejemplo.com --asistente "Mapi" --preset personalizacion
```

El preset `personalizacion` deja escrita una plantilla de "información del negocio" pensada para tiendas de personalización (diseños de la web + diseños a medida), con los datos por completar marcados con `[completar]`.

Luego, en **Configuración**, la dueña:
- sube su **logo** y elige sus **colores** (el panel ajusta solo el contraste para que todo se lea bien);
- le pone **nombre a su asistente** y completa la **información del negocio**: precios de personalización, tiempos de producción, envíos, medios de pago. Con más detalle, la IA responde mejor;
- conecta sus **canales**.

## 5. Conectar los canales de cada tienda

Todo se hace en **Configuración → Canales**. Ahí cada tienda ve sus propias URLs de webhook, que son secretas, y su token de verificación. Las credenciales se guardan **cifradas**.

| Canal | Qué hacer |
|---|---|
| **Tiendanube** | Botón **Conectar Tiendanube**. Desde ese momento los pedidos llegan solos (incluido el cambio a *pagado* o *cancelado*) y los diseños de la web se cargan como productos. Con **Traer pedidos de los últimos 30 días** se importan los anteriores. |
| **WhatsApp** | Botón **Conectar mi WhatsApp**: abre la ventana de Meta. Elige conectar tu app de WhatsApp Business (coexistencia) y escanea el código QR desde el celular. Si el servidor no tiene la app de plataforma, usa la **conexión manual**: token permanente, *Phone number ID* y webhook de tu propia app de Meta. |
| **Instagram / Messenger** | En la misma app agrega Messenger e Instagram (la cuenta de Instagram debe ser profesional y estar vinculada a la página). Pega el token de la página y el *App secret*, configura el webhook con la URL de Integralo y suscríbete a `messages`. Para atender a clientes reales, Meta exige la **revisión de la app** (`pages_messaging`, `instagram_manage_messages`). |
| **TikTok** | Solo para cuentas aprobadas en *TikTok Business Messaging*. La recepción ya está lista; el envío de respuestas (`_send_tiktok` en `app/channels.py`) se completa con la documentación que entregue TikTok. |
| **Otras webs** | Shopify y WooCommerce: webhook de "pedido creado" a la URL que se muestra. Para cualquier otra web, un POST JSON a la URL "web genérica" (formato en `app/web_orders.py`). |

> **WhatsApp:** con la conexión en **coexistencia** el número sigue funcionando en la app WhatsApp Business del celular. Con la conexión manual a un número nuevo, ese número queda solo en Integralo.

## 6. Cómo se usa el día a día

- **Resumen:** ingresos, lo que falta por cobrar, pagos por verificar y preguntas de clientes que esperan tu respuesta.
- **Pedidos:** tablero por etapas. Mueve cada pedido con el botón "→" o arrastrándolo, y el cliente recibe un aviso. Al abrir un pedido ves los detalles de personalización (talla, color, diseño), el pago y el historial.
- **Mensajes:** todos los chats en un solo lugar, con las imágenes que mandan los clientes (por ejemplo, su logo). La IA responde sola. Si hay una queja o algo delicado, deja de responder y marca la conversación para que la atienda una persona.
- **Tu asistente** (aparece con el nombre que le pusiste):
  - Pregúntale lo que quieras sobre el negocio.
  - Responde las preguntas de los clientes que quedaron pendientes, en la lista de la derecha o escribiéndole: "dile a Sofía que las 15 camisetas quedan a 38 mil c/u".
  - Enséñale cosas nuevas: "de ahora en adelante el envío a Medellín vale 15 mil".
- **Productos:** el catálogo que usa la IA. Los productos **personalizables** tienen un precio base, y la IA te consulta el precio final antes de cotizar. Los diseños de la web llegan desde Tiendanube.
- **Excel:** el botón "⬇ Excel" descarga todos los pedidos (hojas *Pedidos*, *Detalle productos* y *Resumen*).

## 7. Estructura del código

```
app/
  main.py         cuentas, sesiones y API del panel
  webhooks.py     webhooks de WhatsApp, Meta, TikTok, Shopify, WooCommerce, web y Tiendanube
  ai_agent.py     IA de atención a clientes (herramientas: catálogo, pedidos, pagos, preguntar a la dueña)
  secretary.py    asistente interna de la dueña
  llm.py          cliente de Claude y ciclo de herramientas
  service.py      flujos compartidos (mensajes entrantes, avisos al cliente, respuestas de la dueña)
  tiendanube.py   instalación OAuth, webhooks, sincronización de productos y pedidos
  channels.py     envío y recepción por canal
  whatsapp.py     WhatsApp con un clic, coexistencia (ecos, historial, contactos) y webhooks
  excel.py        Excel por tienda e importación del catálogo
  db.py           base de datos SQLite multi-tienda
  security.py     contraseñas (scrypt), sesiones y cifrado de credenciales
static/           panel web (HTML/CSS/JS sin dependencias)
scripts/          crear_tienda.py, respaldo.py
deploy/           docker-compose + Caddy (HTTPS), instalar.sh, actualizar.sh
```

**Seguridad:**
- Cada consulta a la base de datos filtra por tienda.
- Las sesiones usan cookies `HttpOnly` y `SameSite=Lax`.
- Las contraseñas se guardan con scrypt y las credenciales de los canales con cifrado Fernet.
- Se verifica la firma de los webhooks de Meta, Shopify, WooCommerce y Tiendanube.
- Hay límite de intentos de inicio de sesión.
