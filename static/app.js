// Integralo — dashboard (JavaScript sin dependencias)
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const FLOW = ["recibido", "preparacion", "reparto", "entregado"];
const PAY_LABELS = { pendiente: "Pendiente", por_verificar: "Por verificar", pagado: "Pagado", reembolsado: "Reembolsado" };
const CH_LABELS = { web: "🌐 Web", whatsapp: "🟢 WhatsApp", instagram: "📸 Instagram", facebook: "💬 Facebook", tiktok: "🎵 TikTok", manual: "✍️ Manual", simulador: "🧪 Prueba" };
const state = { tab: "resumen", convId: null, products: [], me: null, estados: {}, busy: false };

const T = () => state.me.tenant;
const isOwner = () => state.me.user.role === "dueno";
const money = (n) => new Intl.NumberFormat("es-CO", { style: "currency", currency: T()?.currency || "COP", maximumFractionDigits: 0 }).format(n || 0);
const fdate = (s) => s ? new Date(s).toLocaleString("es-CO", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "";

async function api(path, opts = {}) {
  const isForm = opts.body instanceof FormData;
  const res = await fetch(path, { ...opts, headers: isForm ? {} : { "Content-Type": "application/json" },
    body: opts.body && !isForm && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body });
  if (res.status === 401) { location.href = "/login"; throw new Error("401"); }
  if (!res.ok) {
    let msg = res.statusText;
    try { const d = await res.json(); msg = typeof d.detail === "string" ? d.detail : "Revisa los datos"; } catch {}
    toast("Error: " + msg);
    throw new Error(msg);
  }
  return res.json();
}

function toast(text) {
  const t = $("#toast");
  t.textContent = text; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 3500);
}

const statusChip = (s) => `<span class="chip">${esc(state.estados[s] || s)}</span>`;
const payChip = (p) => `<span class="chip pay-${esc(p)}">${esc(PAY_LABELS[p] || p)}</span>`;
const chChip = (c) => `<span class="chip channel">${esc(CH_LABELS[c] || c)}</span>`;
const md = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

// =================================================================== colores de marca
function rgb(hex) { const h = hex.replace("#", ""); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)); }
function toHex(c) { return "#" + c.map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0")).join(""); }
function lum(hex) { return rgb(hex).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }).reduce((a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i], 0); }
function contrast(a, b) { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); }
function bestInk(bg) { return contrast(bg, "#ffffff") >= contrast(bg, "#0b0b0b") ? "#ffffff" : "#0b0b0b"; }
function ensureContrast(hex, bg, ratio) {
  // oscurece o aclara el color de marca lo mínimo necesario para que se lea sobre el fondo
  const target = lum(bg) > 0.4 ? [0, 0, 0] : [255, 255, 255];
  let c = rgb(hex);
  for (let i = 0; i < 20 && contrast(toHex(c), bg) < ratio; i++) c = c.map((v, k) => v + (target[k] - v) * 0.1);
  return toHex(c);
}
function applyBrand(primary, secondary) {
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  const surface = dark ? "#1a1a19" : "#fcfcfb";
  const s = document.documentElement.style;
  s.setProperty("--accent", primary);
  s.setProperty("--accent-ink", bestInk(primary));
  s.setProperty("--accent-text", ensureContrast(primary, surface, 4.5));
  s.setProperty("--chart-bar", ensureContrast(primary, surface, 3));
  s.setProperty("--bar-bg", secondary);
  const barInk = bestInk(secondary);
  s.setProperty("--bar-ink", barInk);
  s.setProperty("--bar-hover", barInk === "#ffffff" ? "rgba(255,255,255,.14)" : "rgba(0,0,0,.07)");
  $("#brand-mark").style.color = ensureContrast(primary, secondary, 3);
}
function applyTenant() {
  const t = T();
  applyBrand(t.brand_primary, t.brand_secondary);
  $("#biz-name").textContent = t.name;
  document.title = `${t.name} · Integralo`;
  const logo = $("#brand-logo");
  logo.hidden = !t.logo_url; $("#brand-mark").hidden = !!t.logo_url;
  if (t.logo_url) logo.src = t.logo_url;
  $("#assistant-tab").textContent = t.assistant_name;
  $("#assistant-title").textContent = t.assistant_name;
  $("#assistant-avatar").textContent = (t.assistant_name || "A").trim()[0].toUpperCase();
  $("#btn-tn-sync").hidden = !t.channels.tiendanube;
}

// =================================================================== pestañas
$$(".tab").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(tab) {
  state.tab = tab;
  if (location.hash !== "#" + tab) history.replaceState(null, "", "#" + tab);
  $$(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + tab));
  refresh();
}
async function refresh() {
  const loaders = { resumen: loadSummary, pedidos: loadOrders, mensajes: loadConversations, asistente: loadAssistant,
                    productos: loadProducts, configuracion: renderSettings };
  await loaders[state.tab]?.();
  updateBadges();
}
async function updateBadges() {
  const s = await api("/api/stats");
  const set = (el, n, title) => { el.hidden = !n; el.textContent = n; el.title = title; };
  set($("#badge-msgs"), s.conversaciones_humano, `${s.conversaciones_humano} conversación(es) necesitan atención`);
  set($("#badge-q"), s.preguntas_pendientes, `${s.preguntas_pendientes} pregunta(s) de clientes sin responder`);
}
$("#logout").addEventListener("click", async () => { await api("/auth/logout", { method: "POST" }); location.href = "/login"; });

// =================================================================== resumen
async function loadSummary() {
  const s = await api("/api/stats");
  $("#kpis").innerHTML = [
    ["Ingresos de hoy", money(s.ingresos_hoy), "pagos confirmados"],
    ["Ingresos del mes", money(s.ingresos_mes), "pagos confirmados"],
    ["Por cobrar", money(s.por_cobrar), `${s.pagos_por_verificar} pago(s) por verificar`],
    ["Pedidos de hoy", s.pedidos_hoy, `${s.pedidos_activos} activos en total`],
    ["Te necesitan", s.conversaciones_humano + s.preguntas_pendientes, `${s.preguntas_pendientes} pregunta(s) · ${s.conversaciones_humano} chat(s)`],
  ].map(([l, v, sub]) => `<div class="kpi"><div class="label">${l}</div><div class="value">${esc(v)}</div><div class="sub">${esc(sub)}</div></div>`).join("");

  const total = Object.values(s.por_estado).reduce((a, b) => a + b, 0) || 1;
  $("#by-status").innerHTML = Object.entries(s.por_estado).map(([k, n]) =>
    `<div class="row">${statusChip(k)}<span class="meter"><span style="width:${(n / total) * 100}%"></span></span><span class="n">${n}</span></div>`).join("");
  $("#by-channel").innerHTML = s.por_canal.length ? `<table><tr><th>Canal</th><th class="num">Pedidos</th><th class="num">Ingresos</th></tr>
    ${s.por_canal.map((c) => `<tr><td>${chChip(c.source)}</td><td class="num">${c.n}</td><td class="num">${money(c.ingresos)}</td></tr>`).join("")}</table>`
    : `<p class="hint">Aún no hay pedidos.</p>`;
  drawRevenue(s.ingresos_diarios);

  const pend = await api("/api/orders?status=");
  const verify = pend.filter((o) => o.payment_status === "por_verificar");
  $("#to-verify").innerHTML = verify.length ? `<table><tr><th>Pedido</th><th>Cliente</th><th>Método / ref.</th><th class="num">Total</th><th></th></tr>
    ${verify.map((o) => `<tr><td><a href="#" data-open="${o.id}">${esc(o.code)}</a></td><td>${esc(o.customer_name)}</td>
      <td>${esc(o.payment_method)} ${esc(o.payment_reference)}</td><td class="num">${money(o.total)}</td>
      <td><button class="btn small good" data-confirm="${o.id}">✓ Confirmar</button></td></tr>`).join("")}</table>`
    : `<p class="hint">No hay pagos pendientes de verificar.</p>`;
  renderQuestions($("#home-questions"), await api("/api/questions"));
}

function drawRevenue(daily) {
  const map = Object.fromEntries(daily.map((d) => [d.dia, d]));
  const days = [];
  for (let i = 29; i >= 0; i--) {
    const d = new Date(); d.setDate(d.getDate() - i);
    const key = d.toLocaleDateString("sv-SE");
    days.push({ dia: key, ingresos: map[key]?.ingresos || 0, pedidos: map[key]?.pedidos || 0 });
  }
  const W = 720, H = 240, L = 64, R = 8, Tp = 10, B = 26;
  const max = Math.max(...days.map((d) => d.ingresos), 1);
  const step = niceStep(max / 4), top = Math.ceil(max / step) * step;
  const y = (v) => Tp + (H - Tp - B) * (1 - v / top);
  const bw = (W - L - R) / days.length;
  let svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Ingresos confirmados por día, últimos 30 días"><g class="grid axis">`;
  for (let v = 0; v <= top; v += step) svg += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${shortMoney(v)}</text>`;
  days.forEach((d, i) => { if (i % 5 === 4 || i === 29) svg += `<text x="${L + bw * i + bw / 2}" y="${H - 8}" text-anchor="middle">${d.dia.slice(8)}/${d.dia.slice(5, 7)}</text>`; });
  svg += `</g>`;
  days.forEach((d, i) => {
    const x = L + bw * i + 1, w = Math.max(bw - 2, 1), h = y(0) - y(d.ingresos);
    svg += `<g class="col" data-i="${i}"><rect class="hit" x="${L + bw * i}" y="${Tp}" width="${bw}" height="${H - Tp - B}"/>`;
    if (d.ingresos > 0) svg += `<path class="bar" d="${roundedTop(x, y(d.ingresos), w, h, Math.min(4, w / 2, h))}"/>`;
    svg += `</g>`;
  });
  $("#chart").innerHTML = svg + `</svg>`;
  const tip = $("#tooltip");
  $$("#chart g.col").forEach((g) => {
    g.addEventListener("mousemove", (e) => {
      const d = days[g.dataset.i];
      tip.innerHTML = `<strong>${esc(d.dia)}</strong><br>${money(d.ingresos)} · ${d.pedidos} pedido(s)`;
      tip.hidden = false; tip.style.left = e.clientX + 12 + "px"; tip.style.top = e.clientY - 40 + "px";
    });
    g.addEventListener("mouseleave", () => (tip.hidden = true));
  });
  $("#chart-table").innerHTML = `<table><tr><th>Día</th><th class="num">Pedidos pagados</th><th class="num">Ingresos</th></tr>
    ${days.filter((d) => d.ingresos).map((d) => `<tr><td>${d.dia}</td><td class="num">${d.pedidos}</td><td class="num">${money(d.ingresos)}</td></tr>`).join("") || `<tr><td colspan="3" class="hint">Sin ingresos en este periodo</td></tr>`}</table>`;
}
const roundedTop = (x, yt, w, h, r) => `M${x},${yt + h}V${yt + r}Q${x},${yt} ${x + r},${yt}H${x + w - r}Q${x + w},${yt} ${x + w},${yt + r}V${yt + h}Z`;
function niceStep(raw) { const p = Math.pow(10, Math.floor(Math.log10(raw || 1))); return [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw); }
function shortMoney(v) { return v >= 1e6 ? (v / 1e6).toLocaleString("es-CO", { maximumFractionDigits: 1 }) + " M" : v >= 1e3 ? Math.round(v / 1e3) + " mil" : String(v); }

// =================================================================== pedidos
$("#order-search").addEventListener("input", debounce(loadOrders, 300));
$("#show-cancelled").addEventListener("change", loadOrders);
$("#btn-new-order").addEventListener("click", () => openNewOrder());

async function loadOrders() {
  const q = $("#order-search").value.trim();
  const orders = await api("/api/orders" + (q ? "?q=" + encodeURIComponent(q) : ""));
  const cols = $("#show-cancelled").checked ? [...FLOW, "cancelado"] : FLOW;
  const board = $("#board");
  board.style.setProperty("--cols", cols.length);
  board.innerHTML = cols.map((st) => {
    let list = orders.filter((o) => o.status === st);
    const extra = st === "entregado" && list.length > 30 ? list.length - 30 : 0;
    if (extra) list = list.slice(0, 30);
    return `<div class="column" data-status="${st}"><h4><span>${esc(state.estados[st])}</span><span>${list.length + extra}</span></h4>
      ${list.map(orderCard).join("") || `<p class="hint" style="margin:4px">Sin pedidos</p>`}
      ${extra ? `<p class="hint" style="margin:4px">…y ${extra} más (usa el buscador)</p>` : ""}</div>`;
  }).join("");
  $$(".order-card", board).forEach((c) => c.addEventListener("dragstart", (e) => e.dataTransfer.setData("text/plain", c.dataset.id)));
  $$(".column", board).forEach((col) => {
    col.addEventListener("dragover", (e) => { e.preventDefault(); col.classList.add("drop"); });
    col.addEventListener("dragleave", () => col.classList.remove("drop"));
    col.addEventListener("drop", async (e) => { e.preventDefault(); col.classList.remove("drop"); await setStatus(e.dataTransfer.getData("text/plain"), col.dataset.status); });
  });
}
function orderCard(o) {
  const next = FLOW[FLOW.indexOf(o.status) + 1];
  const items = o.items.map((i) => `${i.quantity}× ${i.product_name}${i.details ? ` (${i.details})` : ""}`).join(", ");
  return `<div class="order-card" draggable="true" data-id="${o.id}" data-open="${o.id}">
    <div class="top"><span>${esc(o.code)}</span><span>${money(o.total)}</span></div>
    <div class="meta" title="${esc(items)}">${esc(o.customer_name || "Sin nombre")} · ${esc(items)}</div>
    <div class="bottom">${chChip(o.source)}${payChip(o.payment_status)}
      ${next ? `<button class="btn small next" data-next="${o.id}" data-status="${next}">→ ${esc(state.estados[next])}</button>` : ""}</div></div>`;
}
async function setStatus(id, status) {
  await api(`/api/orders/${id}/status`, { method: "POST", body: { status } });
  toast(`Pedido movido a "${state.estados[status]}"`);
  refresh();
}
async function confirmPayment(id) {
  await api(`/api/orders/${id}/payment`, { method: "POST", body: { payment_status: "pagado" } });
  toast("Pago confirmado ✓"); refresh();
}

document.addEventListener("click", async (e) => {
  const t = e.target.closest("[data-next],[data-confirm],[data-open],[data-conv]");
  if (!t) return;
  e.preventDefault();
  if (t.dataset.next) { e.stopPropagation(); return setStatus(t.dataset.next, t.dataset.status); }
  if (t.dataset.confirm) return confirmPayment(t.dataset.confirm);
  if (t.dataset.open) return openOrder(t.dataset.open);
  if (t.dataset.conv) return openConversation(+t.dataset.conv);
});

const modal = $("#modal");
function showModal(html) { $("#modal-body").innerHTML = html; if (!modal.open) modal.showModal(); }
modal.addEventListener("click", (e) => { if (e.target === modal || e.target.closest("[data-close]")) modal.close(); });

async function openOrder(id) {
  const o = await api(`/api/orders/${id}`);
  const idx = FLOW.indexOf(o.status);
  showModal(`
    <div class="modal-head"><h2>${esc(o.code)} · ${money(o.total)}</h2><button class="btn ghost small" data-close>✕</button></div>
    <div class="modal-content">
      <div class="steps">${FLOW.map((s, i) => `<button class="step ${i < idx ? "done" : ""} ${i === idx ? "current" : ""}" data-step="${s}">${i < idx ? "✓ " : ""}${esc(state.estados[s])}</button>`).join("")}</div>
      ${o.status === "cancelado" ? `<p>${statusChip("cancelado")}</p>` : ""}
      <div class="form-grid">
        <label>Cliente<input name="customer_name" value="${esc(o.customer_name)}"></label>
        <label>Teléfono<input name="phone" value="${esc(o.phone)}"></label>
        <label>Dirección<input name="address" value="${esc(o.address)}"></label>
        <label>Ciudad<input name="city" value="${esc(o.city)}"></label>
        <label class="full">Notas<textarea name="notes" rows="2">${esc(o.notes)}</textarea></label>
      </div>
      <h3 class="mt">Productos</h3>
      <table><tr><th>Producto</th><th>Detalle / personalización</th><th class="num">Cant.</th><th class="num">Precio</th><th class="num">Subtotal</th></tr>
        ${o.items.map((i) => `<tr><td>${esc(i.product_name)}</td><td>${esc(i.details)}</td><td class="num">${i.quantity}</td><td class="num">${money(i.unit_price)}</td><td class="num">${money(i.quantity * i.unit_price)}</td></tr>`).join("")}
        <tr><td colspan="4">Envío</td><td class="num">${money(o.shipping)}</td></tr>
        <tr><th colspan="4">Total</th><th class="num">${money(o.total)}</th></tr></table>
      <h3 class="mt">Pago ${payChip(o.payment_status)}</h3>
      <div class="form-grid">
        <label>Método<input id="pay-method" value="${esc(o.payment_method)}" placeholder="Nequi, transferencia, efectivo…"></label>
        <label>Referencia<input id="pay-ref" value="${esc(o.payment_reference)}" placeholder="N.º de comprobante"></label>
      </div>
      <h3 class="mt">Historial</h3>
      <ul class="timeline">${o.events.map((ev) => `<li><span class="when">${fdate(ev.created_at)}</span> — ${ev.kind === "estado" ? "Estado: " + esc(state.estados[ev.value] || ev.value) : "Pago: " + esc(PAY_LABELS[ev.value] || ev.value)} ${ev.note ? "· " + esc(ev.note) : ""}</li>`).join("")}</ul>
      <p class="hint">Origen: ${chChip(o.source)} ${o.conversation_id ? `· <a href="#" data-conv="${o.conversation_id}">Ver conversación</a>` : ""}</p>
    </div>
    <div class="modal-foot">
      ${o.status !== "cancelado" ? `<button class="btn ghost" id="o-cancel">Cancelar pedido</button>` : ""}
      ${o.payment_status !== "pagado" ? `<button class="btn good" id="o-paid">✓ Confirmar pago</button>` : `<button class="btn ghost" id="o-unpaid">Marcar como no pagado</button>`}
      <button class="btn" id="o-save">Guardar cambios</button>
    </div>`);
  $$(".step", modal).forEach((s) => s.addEventListener("click", async () => { await setStatus(o.id, s.dataset.step); openOrder(o.id); }));
  $("#o-save").onclick = async () => {
    await api(`/api/orders/${o.id}`, { method: "PATCH", body: Object.fromEntries($$(".form-grid [name]", modal).map((i) => [i.name, i.value])) });
    toast("Pedido actualizado"); modal.close(); refresh();
  };
  const pay = async (st) => {
    await api(`/api/orders/${o.id}/payment`, { method: "POST", body: { payment_status: st, method: $("#pay-method").value, reference: $("#pay-ref").value } });
    toast(st === "pagado" ? "Pago confirmado ✓" : "Pago marcado como pendiente"); openOrder(o.id); refresh();
  };
  $("#o-paid") && ($("#o-paid").onclick = () => pay("pagado"));
  $("#o-unpaid") && ($("#o-unpaid").onclick = () => pay("pendiente"));
  $("#o-cancel") && ($("#o-cancel").onclick = async () => { if (confirm("¿Cancelar este pedido?")) { await setStatus(o.id, "cancelado"); modal.close(); } });
}

async function openNewOrder() {
  state.products = await api("/api/products");
  const opts = state.products.filter((p) => p.active).map((p) => `<option value="${esc(p.name)}">`).join("");
  showModal(`
    <div class="modal-head"><h2>Nuevo pedido</h2><button class="btn ghost small" data-close>✕</button></div>
    <div class="modal-content">
      <div class="form-grid">
        <label>Cliente<input name="customer_name" required></label>
        <label>Teléfono<input name="phone"></label>
        <label>Dirección<input name="address"></label>
        <label>Ciudad<input name="city"></label>
        <label>Canal<select name="source">${["manual", "whatsapp", "instagram", "facebook", "tiktok", "web"].map((c) => `<option value="${c}">${CH_LABELS[c]}</option>`).join("")}</select></label>
        <label>Envío<input name="shipping" type="number" min="0" value="0"></label>
        <label>Método de pago<input name="payment_method"></label>
        <label>Pago<select name="payment_status"><option value="pendiente">Pendiente</option><option value="pagado">Pagado</option></select></label>
        <label class="full">Notas<textarea name="notes" rows="2"></textarea></label>
      </div>
      <h3 class="mt">Productos</h3>
      <datalist id="prod-list">${opts}</datalist>
      <div id="items"></div>
      <button class="btn ghost small" id="add-item">+ Agregar producto</button>
      <p><strong>Total: <span id="new-total">${money(0)}</span></strong></p>
    </div>
    <div class="modal-foot"><button class="btn ghost" data-close>Cancelar</button><button class="btn" id="save-order">Crear pedido</button></div>`);
  const items = () => $$(".item-row", modal).map((r) => ({ product_name: $(".i-name", r).value, quantity: +$(".i-qty", r).value || 1,
    unit_price: +$(".i-price", r).value || 0, details: $(".i-det", r).value })).filter((i) => i.product_name);
  const calc = () => { $("#new-total").textContent = money(items().reduce((a, i) => a + i.quantity * i.unit_price, 0) + (+$("[name=shipping]", modal).value || 0)); };
  const addRow = () => {
    const row = document.createElement("div");
    row.className = "item-row";
    row.innerHTML = `<input list="prod-list" placeholder="Producto" class="i-name"><input type="number" min="1" value="1" class="i-qty" aria-label="Cantidad"><input type="number" min="0" placeholder="Precio" class="i-price"><input placeholder="Talla, color, diseño…" class="i-det"><button class="btn ghost small" title="Quitar">✕</button>`;
    row.querySelector("button").onclick = () => { row.remove(); calc(); };
    row.querySelector(".i-name").addEventListener("change", (e) => { const p = state.products.find((p) => p.name === e.target.value); if (p) row.querySelector(".i-price").value = p.price; calc(); });
    row.addEventListener("input", calc);
    $("#items").appendChild(row);
  };
  $("#add-item").onclick = addRow;
  $("[name=shipping]", modal).addEventListener("input", calc);
  addRow();
  $("#save-order").onclick = async () => {
    const body = Object.fromEntries($$(".form-grid [name]", modal).map((i) => [i.name, i.value]));
    body.shipping = +body.shipping || 0; body.items = items();
    if (!body.items.length) return toast("Agrega al menos un producto");
    const o = await api("/api/orders", { method: "POST", body });
    toast(`Pedido ${o.code} creado`); modal.close(); refresh();
  };
}

// =================================================================== mensajes con clientes
async function loadConversations() {
  const convs = await api("/api/conversations");
  $("#conv-list").innerHTML = convs.map((c) => `
    <div class="conv ${c.id === state.convId ? "active" : ""}" data-conv="${c.id}">
      <div class="line1"><span class="name">${esc(c.customer_name || c.phone || c.external_user_id)}</span><span class="hint">${fdate(c.last_message_at)}</span></div>
      <div class="last">${esc(c.last_text || "")}</div>
      <div class="flags">${chChip(c.channel)}${c.needs_human ? `<span class="chip alert">🙋 Atención humana</span>` : ""}
        ${c.waiting_owner ? `<span class="chip waiting">❓ Esperando tu respuesta</span>` : ""}
        ${!c.ai_enabled ? `<span class="chip">IA apagada</span>` : ""}${c.unread ? `<span class="chip">${c.unread} nuevo(s)</span>` : ""}</div>
    </div>`).join("") || `<p class="hint" style="padding:12px">Aún no hay conversaciones. Usa “Probar IA” para simular un cliente.</p>`;
  if (state.convId) openConversation(state.convId, true);
}

function messageHtml(m) {
  const t = T();
  const who = { cliente: "Cliente", ia: `🤖 ${t.assistant_name}`, humano: "👤 Equipo", duena: `Respuesta de ${t.owner_title}` }[m.author] || "";
  let media = "";
  if (m.media_ref) media = m.media_kind === "image" || m.media_kind === "sticker"
    ? `<a href="/api/media/${m.id}" target="_blank" rel="noopener"><img class="media" src="/api/media/${m.id}" alt="Imagen del cliente" loading="lazy"></a>`
    : `<div><a href="/api/media/${m.id}" target="_blank" rel="noopener">📎 Ver archivo</a></div>`;
  return `<div class="msg ${m.direction} ${m.author}">${esc(m.text)}${media}${m.author !== "sistema" ? `<div class="who">${who} · ${fdate(m.created_at)}</div>` : ""}</div>`;
}

async function openConversation(id, keepScroll = false) {
  if (state.tab !== "mensajes") { state.convId = id; return showTab("mensajes"); }
  if (modal.open) modal.close();
  const prev = state.convId;
  state.convId = id;
  $$(".conv").forEach((c) => c.classList.toggle("active", +c.dataset.conv === id));
  const { conversation: c, messages, orders } = await api(`/api/conversations/${id}`);
  const chat = $("#chat");
  const draft = prev === id ? $("#reply")?.value || "" : "";
  const simDraft = prev === id ? $("#sim-text")?.value || "" : "";
  const box0 = $(".messages", chat);
  const atBottom = !box0 || box0.scrollHeight - box0.scrollTop - box0.clientHeight < 40;
  chat.innerHTML = `
    <div class="chat-head">
      <span class="name">${esc(c.customer_name || c.phone || c.external_user_id)}</span>${chChip(c.channel)}
      <div class="actions">
        <label class="switch"><input type="checkbox" id="ai-toggle" ${c.ai_enabled ? "checked" : ""}> 🤖 ${esc(T().assistant_name)} responde</label>
        ${c.needs_human ? `<button class="btn small" id="resolve">✓ Resuelto, devolver a la IA</button>` : `<button class="btn small ghost" id="take">🙋 Tomar conversación</button>`}
      </div>
    </div>
    ${orders.length ? `<div class="chat-orders">Pedidos: ${orders.map((o) => `<a href="#" data-open="${o.id}">${esc(o.code)}</a> ${statusChip(o.status)} ${payChip(o.payment_status)}`).join(" · ")}</div>` : ""}
    <div class="messages">${messages.map(messageHtml).join("")}</div>
    <div class="composer">
      ${c.channel === "simulador" ? `<textarea id="sim-text" placeholder="Escribe como si fueras el cliente… (Enter)">${esc(simDraft)}</textarea><button class="btn ghost" id="sim-send">Enviar como cliente</button>` : ""}
      <textarea id="reply" placeholder="Responder como tienda…">${esc(draft)}</textarea><button class="btn" id="send">Enviar</button>
    </div>`;
  const box = $(".messages", chat);
  if (!keepScroll || atBottom) box.scrollTop = box.scrollHeight;
  const settings = (body) => api(`/api/conversations/${id}/settings`, { method: "POST", body }).then(() => { loadConversations(); updateBadges(); });
  $("#ai-toggle").onchange = (e) => settings({ ai_enabled: e.target.checked });
  $("#resolve") && ($("#resolve").onclick = () => settings({ needs_human: false, ai_enabled: true }));
  $("#take") && ($("#take").onclick = () => settings({ needs_human: true }));
  const send = async () => {
    const text = $("#reply").value.trim();
    if (!text) return;
    const r = await api(`/api/conversations/${id}/reply`, { method: "POST", body: { text } });
    if (!r.enviado && c.channel !== "simulador") toast("Guardado, pero el canal no está conectado: no le llegó al cliente");
    $("#reply").value = ""; openConversation(id);
  };
  $("#send").onclick = send;
  onEnter($("#reply"), send);
  if (c.channel === "simulador") {
    const simSend = async () => {
      const text = $("#sim-text").value.trim();
      if (!text) return;
      $("#sim-text").value = "";
      await api("/api/simulate", { method: "POST", body: { user: c.external_user_id, name: c.customer_name, text } });
      await openConversation(id);
      setTimeout(() => openConversation(id, true), 4000);
    };
    $("#sim-send").onclick = simSend;
    onEnter($("#sim-text"), simSend);
    if (!keepScroll) $("#sim-text").focus();
  }
}
function onEnter(el, fn) { el.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); fn(); } }); }

$("#btn-simulate").addEventListener("click", async () => {
  const name = prompt("Nombre del cliente de prueba:", "Cliente de prueba");
  if (!name) return;
  const text = prompt("Primer mensaje del cliente:", "Hola! quiero una camiseta personalizada, ¿cuánto vale?");
  if (!text) return;
  const r = await api("/api/simulate", { method: "POST", body: { user: "prueba-" + Date.now(), name, text } });
  state.convId = r.conversation_id;
  await loadConversations();
  setTimeout(() => openConversation(r.conversation_id, true), 4000);
});

// =================================================================== asistente interna
const SUGGESTIONS = ["¿Cuánto he vendido hoy y este mes?", "¿Qué pagos tengo por verificar?", "¿Qué pedidos están en preparación?", "¿Qué me están preguntando los clientes?"];

async function loadAssistant() {
  const [msgs, qs] = await Promise.all([api("/api/assistant/messages"), api("/api/questions")]);
  renderAssistant(msgs);
  renderQuestions($("#questions"), qs);
}
function renderAssistant(msgs, pending = null) {
  const t = T();
  const box = $("#assistant-messages");
  const hello = `<div class="msg assistant">¡Hola! Soy ${esc(t.assistant_name)}, tu asistente de ${esc(t.name)} 💬 Pregúntame lo que necesites sobre tus pedidos, pagos y clientes. Cuando un cliente me pregunte algo que no sé, te lo consulto aquí.</div>`;
  box.innerHTML = hello + msgs.map((m) => `<div class="msg ${m.role}">${md(m.text)}<div class="who">${m.role === "assistant" ? esc(t.assistant_name) : "Tú"} · ${fdate(m.created_at)}</div></div>`).join("")
    + (pending ? `<div class="msg user">${esc(pending)}</div><div class="msg assistant typing">${esc(t.assistant_name)} está escribiendo…</div>` : "");
  box.scrollTop = box.scrollHeight;
  $("#assistant-suggestions").innerHTML = msgs.length ? "" : SUGGESTIONS.map((s) => `<button class="btn small ghost" type="button">${esc(s)}</button>`).join("");
  $$("#assistant-suggestions button").forEach((b) => (b.onclick = () => sendAssistant(b.textContent)));
}
async function sendAssistant(text) {
  if (!text.trim() || state.busy) return;
  state.busy = true;
  $("#assistant-input").value = "";
  renderAssistant(await api("/api/assistant/messages"), text);
  try { await api("/api/assistant/chat", { method: "POST", body: { text } }); }
  finally { state.busy = false; loadAssistant(); updateBadges(); }
}
$("#assistant-form").addEventListener("submit", (e) => { e.preventDefault(); sendAssistant($("#assistant-input").value); });
onEnter($("#assistant-input"), () => sendAssistant($("#assistant-input").value));

function renderQuestions(el, qs) {
  el.innerHTML = qs.length ? qs.map((q) => `
    <div class="question">
      <div class="hint">${chChip(q.channel || "")} ${esc(q.customer_name || "Cliente")} · ${fdate(q.created_at)} ${q.conversation_id ? `· <a href="#" data-conv="${q.conversation_id}">ver chat</a>` : ""}</div>
      <div class="q">${esc(q.question)}</div>
      <form data-q="${q.id}"><input placeholder="Tu respuesta…" required aria-label="Respuesta"><button class="btn small">Responder</button></form>
    </div>`).join("") : `<p class="hint">No hay preguntas pendientes 🎉</p>`;
  $$("form[data-q]", el).forEach((f) => f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("input", f).value.trim();
    if (!text) return;
    await api(`/api/questions/${f.dataset.q}/answer`, { method: "POST", body: { text } });
    toast(`Enviado: ${T().assistant_name} le responde al cliente`); refresh();
  }));
}

// =================================================================== productos
$("#btn-new-product").addEventListener("click", () => editProduct({ active: 1 }));
$("#product-search").addEventListener("input", debounce(renderProducts, 200));
$("#import-file").addEventListener("change", async (e) => {
  const fd = new FormData(); fd.append("file", e.target.files[0]);
  e.target.value = "";
  const r = await api("/api/products/import", { method: "POST", body: fd });
  toast(`${r.importados} productos importados`); loadProducts();
});
$("#btn-tn-sync").addEventListener("click", async (e) => {
  e.target.disabled = true;
  try { const r = await api("/api/tiendanube/sincronizar-productos", { method: "POST" }); toast(`${r.productos} productos sincronizados`); loadProducts(); }
  finally { e.target.disabled = false; }
});
async function loadProducts() { state.products = await api("/api/products"); renderProducts(); }
function renderProducts() {
  const q = $("#product-search").value.trim().toLowerCase();
  const list = state.products.filter((p) => !q || (p.name + " " + p.variants + " " + p.description).toLowerCase().includes(q));
  $("#products").innerHTML = list.length ? `<table><tr><th></th><th>Producto</th><th>Variantes</th><th class="num">Precio</th><th class="num">Stock</th><th>Tipo</th><th></th></tr>
    ${list.map((p) => `<tr><td>${p.image_url ? `<img class="prod-thumb" src="${esc(p.image_url)}" alt="" loading="lazy">` : ""}</td>
      <td><strong>${esc(p.name)}</strong>${p.active ? "" : ` <span class="chip">Inactivo</span>`}<br><span class="hint">${esc(p.description.slice(0, 90))}</span></td>
      <td class="hint">${esc(p.variants.slice(0, 80))}</td><td class="num">${money(p.price)}</td><td class="num">${p.stock ?? "—"}</td>
      <td>${p.personalizable ? `<span class="chip">✏️ Personalizable</span>` : p.external_id ? `<span class="chip">Diseño de la web</span>` : ""}</td>
      <td><button class="btn small ghost" data-edit="${p.id}">Editar</button></td></tr>`).join("")}</table>`
    : `<p class="hint">${q ? "Sin resultados." : "Agrega tus productos, impórtalos desde Excel o conecta Tiendanube en Configuración."}</p>`;
  $$("[data-edit]").forEach((b) => (b.onclick = () => editProduct(state.products.find((p) => p.id === +b.dataset.edit))));
}
function editProduct(p) {
  showModal(`
    <div class="modal-head"><h2>${p.id ? "Editar" : "Nuevo"} producto</h2><button class="btn ghost small" data-close>✕</button></div>
    <div class="modal-content"><div class="form-grid">
      <label>Nombre<input name="name" value="${esc(p.name || "")}"></label>
      <label>SKU / referencia<input name="sku" value="${esc(p.sku || "")}"></label>
      <label>Precio${p.personalizable ? " base" : ""}<input name="price" type="number" min="0" value="${p.price ?? ""}"></label>
      <label>Stock (vacío = sin control)<input name="stock" type="number" value="${p.stock ?? ""}"></label>
      <label class="full">Variantes (tallas, colores…)<input name="variants" value="${esc(p.variants || "")}" placeholder="S, M, L, XL · Blanco, Negro"></label>
      <label class="full">Descripción (la IA la usa para responder)<textarea name="description" rows="3">${esc(p.description || "")}</textarea></label>
      <label class="full">Enlace en la tienda<input name="url" value="${esc(p.url || "")}" placeholder="https://"></label>
      <label class="check"><input type="checkbox" name="personalizable" ${p.personalizable ? "checked" : ""}> Personalizable (la IA te consulta el precio final)</label>
      <label class="check"><input type="checkbox" name="active" ${p.active ? "checked" : ""}> Activo (visible para la IA)</label>
    </div></div>
    <div class="modal-foot">${p.id ? `<button class="btn danger" id="p-del">Eliminar</button>` : ""}<button class="btn ghost" data-close>Cancelar</button><button class="btn" id="p-save">Guardar</button></div>`);
  $("#p-save").onclick = async () => {
    const f = Object.fromEntries($$(".form-grid [name]", modal).map((i) => [i.name, i.type === "checkbox" ? (i.checked ? 1 : 0) : i.value]));
    f.stock = f.stock === "" ? null : +f.stock; f.price = +f.price || 0;
    if (p.id) { f.id = p.id; f.external_id = p.external_id; f.image_url = p.image_url; }
    await api("/api/products", { method: "POST", body: f });
    modal.close(); loadProducts();
  };
  $("#p-del") && ($("#p-del").onclick = async () => { if (confirm("¿Eliminar producto?")) { await api(`/api/products/${p.id}`, { method: "DELETE" }); modal.close(); loadProducts(); } });
}

// =================================================================== configuración
const INFO_TEMPLATE = `Qué vendemos: …
Envíos: ciudades, costo y tiempo de entrega…
Tiempos de producción de pedidos personalizados: …
Medios de pago: Nequi …, Bancolombia …, contra entrega …
Horario de atención: …
Cambios y devoluciones: …`;

async function renderSettings() {
  if ($("#settings").contains(document.activeElement) && document.activeElement.value) return; // no interrumpir si está escribiendo
  state.me = await api("/api/me");
  const t = T(), owner = isOwner(), dis = owner ? "" : "disabled";
  const secretInput = (key, label, ph = "") => `<label>${label}<input type="password" autocomplete="off" data-secret="${key}" ${dis}
      placeholder="${t.secrets_set[key] ? "•••••• guardado (escribe para cambiar)" : esc(ph)}"></label>`;
  const copy = (label, value) => `<div class="field">${label}<div class="copy"><input readonly value="${esc(value)}"><button class="btn small ghost" type="button" data-copy="${esc(value)}">Copiar</button></div></div>`;
  const status = (on) => on ? `<span class="chip ok-chip">Conectado</span>` : `<span class="chip off-chip">Sin conectar</span>`;
  const tn = !state.me.tiendanube_disponible
    ? `<p class="hint">La conexión con Tiendanube aún no está activada en este servidor de Integralo.</p>`
    : t.channels.tiendanube
      ? `<p class="hint">Tienda #${esc(t.tiendanube_store_id)}. Los pedidos nuevos llegan solos y los diseños de tu web se cargan como productos.</p>
         <div class="row-actions"><button class="btn small ghost" id="tn-products" ${dis}>↻ Sincronizar productos</button>
         <button class="btn small ghost" id="tn-orders" ${dis}>⬇ Traer pedidos de los últimos 30 días</button>
         <button class="btn small ghost" id="tn-off" ${dis}>Desconectar</button></div>`
      : `<p class="hint">Conecta tu tienda para recibir los pedidos automáticamente y cargar tus diseños como productos.</p>
         <a class="btn" href="/tiendanube/conectar" ${owner ? "" : 'aria-disabled="true" onclick="return false"'}>Conectar Tiendanube</a>`;

  $("#settings").innerHTML = `
  ${owner ? "" : `<p class="hint">Solo la dueña o el dueño de la cuenta puede cambiar la configuración.</p>`}
  <div class="settings-grid">
    <div class="card" id="s-brand">
      <h2>Marca</h2><p class="hint">Así se ve tu panel. Los colores se ajustan solos para que el texto siempre se lea bien.</p>
      <div class="form-grid">
        <label class="full">Nombre del negocio<input name="name" value="${esc(t.name)}" ${dis}></label>
        <div class="field">Color principal (botones)<div class="color-row"><input type="color" data-color="brand_primary" value="${t.brand_primary}" ${dis}><input type="text" name="brand_primary" value="${t.brand_primary}" ${dis}></div></div>
        <div class="field">Color secundario (barra superior)<div class="color-row"><input type="color" data-color="brand_secondary" value="${t.brand_secondary}" ${dis}><input type="text" name="brand_secondary" value="${t.brand_secondary}" ${dis}></div></div>
        <div class="field">Logo<div class="color-row"><div class="logo-preview">${t.logo_url ? `<img src="${esc(t.logo_url)}" alt="Logo">` : "Sin logo"}</div>
          <label class="btn small ghost">Subir logo<input type="file" id="logo-file" accept="image/*" hidden ${dis}></label></div></div>
        <label>Moneda<select name="currency" ${dis}>${["COP", "USD", "MXN", "PEN", "CLP", "ARS", "EUR"].map((c) => `<option ${c === t.currency ? "selected" : ""}>${c}</option>`).join("")}</select></label>
      </div>
      <div class="row-actions"><button class="btn" data-save="s-brand" ${dis}>Guardar marca</button></div>
    </div>

    <div class="card" id="s-assistant">
      <h2>Tu asistente</h2><p class="hint">Atiende a tus clientes y te ayuda a ti. Ponle el nombre que quieras.</p>
      <div class="form-grid">
        <label>Nombre de la asistente<input name="assistant_name" value="${esc(t.assistant_name)}" ${dis} maxlength="40"></label>
        <label>¿Cómo te llama a ti?<input name="owner_title" value="${esc(t.owner_title)}" ${dis} placeholder="la dueña, Caro, el equipo…"></label>
        <label class="full">Lo que tu asistente debe saber del negocio
          <textarea name="business_info" rows="10" ${dis} placeholder="${esc(INFO_TEMPLATE)}">${esc(t.business_info)}</textarea></label>
        <label class="check full"><input type="checkbox" name="ai_enabled_default" ${t.ai_enabled_default ? "checked" : ""} ${dis}> Responder automáticamente las conversaciones nuevas</label>
      </div>
      <p class="hint">${state.me.ia_disponible ? "✓ IA activa en el servidor." : "⚠️ La IA no está configurada en el servidor (falta la clave de Claude)."}</p>
      <div class="row-actions"><button class="btn" data-save="s-assistant" ${dis}>Guardar asistente</button></div>
    </div>

    <div class="card" id="s-channels">
      <h2>Canales</h2><p class="hint">Conecta tu tienda en línea y tus redes. Las direcciones (URL) son privadas de tu cuenta.</p>
      <div class="ch-block"><div class="channel-head"><strong>🛍️ Tiendanube</strong>${status(t.channels.tiendanube)}</div>${tn}</div>
      <div class="ch-block"><div class="channel-head"><strong>🟢 WhatsApp Business</strong>${status(t.channels.whatsapp)}</div>
        ${copy("URL del webhook (Meta → WhatsApp → Configuración)", t.webhooks.whatsapp)}${copy("Token de verificación", t.meta_verify_token)}
        <div class="form-grid">${secretInput("whatsapp_token", "Token de acceso permanente")}${secretInput("whatsapp_phone_number_id", "Phone number ID")}</div></div>
      <div class="ch-block"><div class="channel-head"><strong>📸 Instagram y 💬 Messenger</strong>${status(t.channels.facebook_instagram)}</div>
        ${copy("URL del webhook", t.webhooks.meta)}${copy("Token de verificación", t.meta_verify_token)}
        <div class="form-grid">${secretInput("meta_page_token", "Token de la página de Facebook")}${secretInput("meta_app_secret", "App secret de Meta (recomendado)")}</div></div>
      <div class="ch-block"><div class="channel-head"><strong>🎵 TikTok</strong>${status(t.channels.tiktok)}</div>
        <p class="hint">Requiere acceso aprobado a TikTok Business Messaging.</p>
        ${copy("URL del webhook", t.webhooks.tiktok)}<div class="form-grid">${secretInput("tiktok_access_token", "Access token")}${secretInput("tiktok_webhook_secret", "Secreto del webhook")}</div></div>
      <details class="ch-block"><summary><strong>🌐 Otras páginas web (Shopify, WooCommerce, formulario propio)</strong></summary>
        ${copy("Shopify", t.webhooks.shopify)}${copy("WooCommerce", t.webhooks.woocommerce)}${copy("Web genérica (POST JSON)", t.webhooks.web)}
        <div class="form-grid">${secretInput("shopify_secret", "Secreto de Shopify")}${secretInput("woocommerce_secret", "Secreto de WooCommerce")}${secretInput("web_token", "Token X-Token (web genérica)")}</div></details>
      <div class="row-actions"><button class="btn" id="save-secrets" ${dis}>Guardar credenciales</button></div>
    </div>

    <div class="card" id="s-team">
      <h2>Equipo</h2>
      <table>${(await api("/api/users")).map((u) => `<tr><td>${esc(u.name)}<br><span class="hint">${esc(u.email)}</span></td><td>${u.role === "dueno" ? "Dueña/o" : "Equipo"}</td>
        <td>${owner && u.role !== "dueno" ? `<button class="btn small ghost" data-deluser="${u.id}">Quitar</button>` : ""}</td></tr>`).join("")}</table>
      ${owner ? `<h3 class="mt">Agregar persona</h3><form id="add-user" class="form-grid"><input name="name" placeholder="Nombre" required><input name="email" type="email" placeholder="Correo" required>
        <input name="password" type="password" placeholder="Contraseña inicial (mín. 8)" minlength="8" required><button class="btn">Agregar</button></form>` : ""}
      <h3 class="mt">Cambiar mi contraseña</h3>
      <form id="change-pass" class="form-grid"><input name="current" type="password" placeholder="Contraseña actual" required autocomplete="current-password">
        <input name="new" type="password" placeholder="Nueva (mín. 8)" minlength="8" required autocomplete="new-password"><button class="btn ghost">Cambiar</button></form>
    </div>
  </div>`;

  // vista previa de colores en vivo
  $$("#s-brand [data-color]").forEach((c) => c.addEventListener("input", () => {
    $(`#s-brand [name=${c.dataset.color}]`).value = c.value; previewBrand();
  }));
  $$("#s-brand input[name^=brand_]").forEach((i) => i.addEventListener("input", () => {
    if (/^#[0-9a-f]{6}$/i.test(i.value)) { $(`#s-brand [data-color=${i.name}]`).value = i.value; previewBrand(); }
  }));
  function previewBrand() { applyBrand($("#s-brand [name=brand_primary]").value, $("#s-brand [name=brand_secondary]").value); }

  $$("[data-save]").forEach((b) => b.addEventListener("click", async () => {
    const card = $("#" + b.dataset.save);
    const body = Object.fromEntries($$("[name]", card).map((i) => [i.name, i.type === "checkbox" ? i.checked : i.value]));
    await api("/api/settings", { method: "PUT", body });
    state.me = await api("/api/me"); applyTenant(); toast("Guardado ✓");
  }));
  $("#logo-file")?.addEventListener("change", async (e) => {
    const fd = new FormData(); fd.append("file", e.target.files[0]);
    await api("/api/settings/logo", { method: "POST", body: fd });
    state.me = await api("/api/me"); applyTenant(); renderSettings(); toast("Logo actualizado");
  });
  $("#save-secrets").addEventListener("click", async () => {
    const body = {};
    $$("[data-secret]").forEach((i) => { if (i.value.trim()) body[i.dataset.secret] = i.value.trim(); });
    if (!Object.keys(body).length) return toast("No escribiste credenciales nuevas");
    await api("/api/settings/secrets", { method: "POST", body });
    toast("Credenciales guardadas (cifradas) ✓"); renderSettings();
  });
  $$("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); toast("Copiado"); } catch { b.previousElementSibling.select(); }
  }));
  const tnAction = (id, url, msg) => $(id)?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    try { const r = await api(url, { method: "POST" }); toast(msg(r)); renderSettings(); } finally { e.target.disabled = false; }
  });
  tnAction("#tn-products", "/api/tiendanube/sincronizar-productos", (r) => `${r.productos} productos sincronizados`);
  tnAction("#tn-orders", "/api/tiendanube/importar-pedidos", (r) => `${r.pedidos} pedidos revisados`);
  $("#tn-off")?.addEventListener("click", async () => {
    if (!confirm("¿Desconectar Tiendanube? Dejarán de llegar los pedidos de la web.")) return;
    await api("/api/tiendanube/desconectar", { method: "POST" }); state.me = await api("/api/me"); applyTenant(); renderSettings();
  });
  $$("[data-deluser]").forEach((b) => b.addEventListener("click", async () => {
    if (confirm("¿Quitar a esta persona?")) { await api(`/api/users/${b.dataset.deluser}`, { method: "DELETE" }); renderSettings(); }
  }));
  $("#add-user")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    await api("/api/users", { method: "POST", body: Object.fromEntries(new FormData(e.target)) });
    toast("Persona agregada. Compártele su correo y contraseña."); renderSettings();
  });
  $("#change-pass").addEventListener("submit", async (e) => {
    e.preventDefault();
    await api("/api/me/password", { method: "POST", body: Object.fromEntries(new FormData(e.target)) });
    e.target.reset(); toast("Contraseña cambiada ✓");
  });
}

// =================================================================== inicio
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

(async function init() {
  state.me = await api("/api/me");
  state.estados = state.me.estados;
  applyTenant();
  const tab = location.hash.slice(1);
  showTab($(`.tab[data-tab="${tab}"]`) ? tab : "resumen");
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTenant);
  setInterval(() => {
    const a = document.activeElement;
    const typing = a && ["TEXTAREA", "INPUT", "SELECT"].includes(a.tagName) && a.type !== "checkbox" && a.value;
    if (!modal.open && !typing && !state.busy && state.tab !== "configuracion") refresh();
  }, 6000);
})();
