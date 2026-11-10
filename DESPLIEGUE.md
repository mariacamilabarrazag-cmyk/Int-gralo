# Publicar Integralo gratis (Oracle Cloud + HTTPS)

Esta guía deja Integralo funcionando en internet **sin pagar**:

- un servidor de **Oracle Cloud "Always Free"** (encendido todo el tiempo, con disco permanente);
- un **subdominio gratis** de DuckDNS (por ejemplo `heymap.duckdns.org`);
- un **certificado HTTPS gratis** de Let's Encrypt, que se pide y se renueva solo.

Tiempo estimado: 30 a 45 minutos la primera vez.

> **Ojo:** los planes gratuitos cambian. Al crear la cuenta, confirma que el servidor diga **"Always Free-eligible"**.

---

## Paso 1. Crear la cuenta de Oracle Cloud

1. Entra a https://www.oracle.com/cloud/free/ → **Start for free**.
2. Llena tus datos. Oracle pide una **tarjeta** solo para verificar que eres una persona; no cobra mientras uses recursos *Always Free*.
3. Elige la **región principal** (*home region*). Después no se puede cambiar y los recursos gratis solo existen ahí. Escoge la más cercana con disponibilidad (por ejemplo *Colombia Central (Bogotá)* o *US East (Ashburn)*).

> **Recomendado:** cuando la cuenta esté activa, ve a *Billing → Upgrade and Manage Payment* y pásala a **Pay As You Go**. Sigues sin pagar si te quedas en lo gratuito, pero Oracle **ya no recupera los servidores gratis "inactivos"**. Integralo pasa mucho tiempo quieto, así que en una cuenta solo gratuita Oracle podría apagarlo. Para no llevarte sorpresas, crea una alerta de presupuesto de $1 en *Billing → Budgets*.

## Paso 2. Crear el servidor

1. Menú ☰ → **Compute → Instances → Create instance**.
2. **Name:** `integralo`.
3. **Image:** *Change image* → **Canonical Ubuntu 24.04**.
4. **Shape:** *Change shape* y elige una de las dos opciones con la etiqueta *Always Free-eligible*:
   - **Ampere (A1.Flex)**: 1 OCPU y 6 GB de RAM es más que suficiente. Si sale "Out of capacity", prueba más tarde o usa la otra.
   - **AMD VM.Standard.E2.1.Micro**: 1 GB de RAM. También funciona, porque el script agrega memoria de intercambio (swap).
5. **Networking:** deja *Create new virtual cloud network* y *Assign a public IPv4 address* marcados.
6. **Add SSH keys:** elige *Generate a key pair for me* y **descarga la llave privada** (`.key`). Guárdala bien: es la llave para entrar al servidor.
7. **Create**. Cuando el estado diga *Running*, copia la **Public IP address** (por ejemplo `129.151.x.x`).

## Paso 3. Abrir los puertos web en Oracle

Oracle bloquea todo por defecto, salvo la conexión SSH.

1. En la página del servidor → **Primary VNIC → Subnet** → abre la subred.
2. **Security Lists** → abre la *Default Security List*.
3. **Add Ingress Rules** y agrega estas dos reglas:

| Source CIDR | IP Protocol | Destination Port Range |
|---|---|---|
| `0.0.0.0/0` | TCP | `80` |
| `0.0.0.0/0` | TCP | `443` |

(El firewall interno del servidor lo abre el script.)

## Paso 4. Conseguir el subdominio gratis

1. Entra a https://www.duckdns.org e inicia sesión con Google o GitHub.
2. Escribe el nombre (por ejemplo `heymap`) → **add domain**.
3. En **current ip** pon la IP pública del servidor → **update ip**.
4. Copia el **token** que aparece arriba: el script lo pedirá.

> ¿Tienes un dominio propio, como `app.heymap.co`? Entonces crea un registro **A** que apunte a la IP del servidor y sáltate DuckDNS.

## Paso 5. Entrar al servidor

En tu computador, abre la **Terminal** (Mac/Linux) o **PowerShell** (Windows):

```bash
ssh -i RUTA/A/TU-LLAVE.key ubuntu@IP-DEL-SERVIDOR
```

- Mac/Linux: si dice *"permissions are too open"*, ejecuta antes `chmod 600 RUTA/A/TU-LLAVE.key`.
- También puedes entrar desde el navegador con el botón **Cloud Shell** de Oracle, subiendo la llave.

## Paso 6. Descargar Integralo en el servidor

El repositorio es privado, así que necesitas un token de GitHub **de solo lectura**:

1. En GitHub: *Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token*.
2. **Repository access:** *Only select repositories* → `Int-gralo`.
3. **Permissions → Contents:** *Read-only*. Genera el token y cópialo.

En el servidor:

```bash
git config --global credential.helper store
```

```bash
git clone https://github.com/mariacamilabarrazag-cmyk/Int-gralo.git integralo
```

Cuando pida **Username** escribe tu usuario de GitHub, y en **Password** pega el token. El servidor lo recuerda para las actualizaciones.

## Paso 7. Instalar

```bash
cd integralo && bash deploy/instalar.sh
```

El script pregunta:
- el **dominio** (por ejemplo `heymap.duckdns.org`);
- la **clave de Claude** (`ANTHROPIC_API_KEY`), que puedes dejar para después;
- si cualquier tienda puede **registrarse sola** (responde *N* si solo tú vas a crear las cuentas);
- el **token de DuckDNS**, si usas DuckDNS.

Al final muestra: **✓ ¡Listo! Integralo está en https://heymap.duckdns.org** 🎉

> Si el HTTPS no responde: revisa el Paso 3 (puertos) y que DuckDNS tenga la IP correcta. Caddy reintenta solo, así que no hace falta reinstalar.

## Paso 8. Crear la tienda de Hey Map

Si permitiste el registro, entra a `https://TU-DOMINIO/registro`. Si no, en el servidor:

```bash
cd ~/integralo && sudo docker compose --env-file .env -f deploy/docker-compose.yml exec app python -m scripts.crear_tienda --nombre "Hey Map" --email correo-de-la-duena@ejemplo.com --asistente Mapi --preset personalizacion
```

Luego entra a **Configuración** y sube el logo, elige los colores, completa la información del negocio y conecta los canales.

---

## Mantenimiento

| Para… | Ejecuta en el servidor (dentro de `~/integralo`) |
|---|---|
| Actualizar a la última versión de GitHub | `bash deploy/actualizar.sh` |
| Ver si está funcionando | `sudo docker compose --env-file .env -f deploy/docker-compose.yml ps` |
| Ver los registros de la app | `sudo docker compose --env-file .env -f deploy/docker-compose.yml logs --tail 100 app` |
| Hacer un respaldo ahora | `sudo integralo-respaldo` |
| Reiniciar | `sudo docker compose --env-file .env -f deploy/docker-compose.yml restart` |
| Cambiar la clave de Claude u otra variable | edita `.env` (`nano .env`) y luego ejecuta `bash deploy/actualizar.sh` |

**Respaldos:** se hacen solos todos los días a las 3:30 a. m. y se guardan 14 días **dentro del servidor**. De vez en cuando descarga uno a tu computador:

```bash
sudo docker compose --env-file .env -f deploy/docker-compose.yml cp app:/data/respaldos ./respaldos
```

```bash
scp -i TU-LLAVE.key -r ubuntu@IP-DEL-SERVIDOR:~/integralo/respaldos .
```

Guarda también una copia del archivo `.env`: tiene la `SECRET_KEY`, sin la cual no se pueden leer las credenciales guardadas.

---

## Alternativa: usar tu propio computador

Si prefieres no usar Oracle, Integralo puede correr en un computador de la casa o la oficina con **Cloudflare Tunnel**, que da HTTPS gratis:

1. `./iniciar.sh` (o `iniciar.bat` en Windows).
2. Instala `cloudflared` y ejecuta `cloudflared tunnel --url http://localhost:8000`. Te da una dirección `https://….trycloudflare.com`.
3. Pon esa dirección en `PUBLIC_URL` dentro de `.env`.

Limitaciones: la dirección cambia cada vez que reinicias el túnel (para que sea fija necesitas un dominio propio en Cloudflare), y **solo funciona mientras el computador esté prendido**. Si se apaga, WhatsApp e Instagram reintentan durante un tiempo y luego se pierden mensajes. Para una tienda real, mejor el servidor de Oracle.
