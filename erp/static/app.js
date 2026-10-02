// ERP Ferretería: comportamiento compartido (htmx + Alpine).

const formatoPesos = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 0 });
window.pesos = (n) => (n < 0 ? "-$" : "$") + formatoPesos.format(Math.abs(Math.round(n || 0)));
window.cant = (n) => new Intl.NumberFormat("es-CO", { maximumFractionDigits: 3 }).format(n || 0);

// ---------- Avisos emergentes ----------
const ICONOS_AVISO = {
  ok: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
  error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/></svg>',
};
window.avisar = (texto, tipo = "ok") => {
  const caja = document.getElementById("avisos");
  if (!caja || !texto) return;
  const t = document.createElement("div");
  t.className = `toast ${tipo}`;
  t.setAttribute("role", tipo === "error" ? "alert" : "status");
  t.innerHTML = ICONOS_AVISO[tipo] || ICONOS_AVISO.ok;
  const p = document.createElement("div");
  p.textContent = texto;
  t.append(p);
  caja.append(t);
  setTimeout(() => t.remove(), tipo === "error" ? 7000 : 4000);
};
document.addEventListener("aviso", (e) => avisar(e.detail.texto, e.detail.tipo));
document.addEventListener("DOMContentLoaded", () => {
  const flash = document.body.dataset.aviso;
  if (flash) avisar(flash, document.body.dataset.avisoTipo || "ok");
});
document.addEventListener("htmx:sendError", () => avisar("No hay conexión con el servidor de la tienda.", "error"));
document.addEventListener("htmx:responseError", (e) => avisar(`El servidor respondió ${e.detail.xhr.status}.`, "error"));

// Peticiones JSON con manejo de errores uniforme
window.api = async (url, opciones = {}) => {
  const r = await fetch(url, {
    headers: opciones.body && !(opciones.body instanceof FormData) ? { "Content-Type": "application/json" } : {},
    ...opciones,
    body: opciones.body && !(opciones.body instanceof FormData) ? JSON.stringify(opciones.body) : opciones.body,
  }).catch(() => null);
  if (!r) throw new Error("No hay conexión con el servidor de la tienda.");
  const datos = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(datos.error || `Error ${r.status}`);
  return datos;
};

// ---------- Globos de los gráficos ----------
let globo = null;
function mostrarGlobo(el, x, y) {
  let datos;
  try { datos = JSON.parse(el.dataset.globo); } catch { return; }
  if (!globo) {
    globo = document.createElement("div");
    globo.className = "globo";
    globo.setAttribute("role", "tooltip");
    document.body.append(globo);
  }
  globo.replaceChildren();
  const titulo = document.createElement("div");
  titulo.className = "titulo";
  titulo.textContent = datos.titulo;
  globo.append(titulo);
  for (const r of datos.renglones || []) {
    const fila = document.createElement("div");
    fila.className = "renglon" + (r.total ? " total" : "");
    const nombre = document.createElement("span");
    if (r.clave) {
      const clave = document.createElement("i");
      clave.className = "clave";
      clave.style.background = `var(--${r.clave})`;
      nombre.append(clave);
    }
    nombre.append(document.createTextNode(r.nombre));
    const valor = document.createElement("b");
    valor.textContent = r.valor;
    fila.append(nombre, valor);
    globo.append(fila);
  }
  globo.style.display = "block";
  const ancho = globo.offsetWidth, alto = globo.offsetHeight;
  let left = x + 14, top = y - alto - 12;
  if (left + ancho > window.innerWidth - 8) left = x - ancho - 14;
  if (top < 8) top = y + 16;
  globo.style.left = `${Math.max(8, left)}px`;
  globo.style.top = `${top}px`;
}
document.addEventListener("pointermove", (e) => {
  const el = e.target.closest?.("[data-globo]");
  if (el) mostrarGlobo(el, e.clientX, e.clientY);
  else if (globo) globo.style.display = "none";
});
document.addEventListener("focusin", (e) => {
  const el = e.target.closest?.("[data-globo]");
  if (el) {
    const r = el.getBoundingClientRect();
    mostrarGlobo(el, r.left + r.width / 2, r.top);
  }
});
document.addEventListener("focusout", () => globo && (globo.style.display = "none"));

// ---------- Escáner con la cámara (iPhone y Android) ----------
let lectorActivo = null;
window.escanearCamara = async (alLeer) => {
  const dialogo = document.getElementById("dialogo-lector");
  if (!window.Html5Qrcode || !dialogo) return avisar("El lector de cámara no está disponible.", "error");
  dialogo.showModal();
  const F = Html5QrcodeSupportedFormats;
  lectorActivo = new Html5Qrcode("lector", { formatsToSupport: [F.EAN_13, F.EAN_8, F.UPC_A, F.UPC_E, F.CODE_128, F.CODE_39], verbose: false });
  try {
    await lectorActivo.start({ facingMode: "environment" }, { fps: 12, qrbox: { width: 280, height: 140 } }, async (codigo) => {
      await cerrarLector();
      navigator.vibrate?.(60);
      alLeer(codigo);
    });
  } catch (e) {
    await cerrarLector();
    avisar("No se pudo abrir la cámara. Revisa el permiso en Ajustes → Safari → Cámara.", "error");
  }
};
window.cerrarLector = async () => {
  try { if (lectorActivo?.isScanning) await lectorActivo.stop(); } catch {}
  lectorActivo = null;
  document.getElementById("dialogo-lector")?.close();
};

// ---------- Buscador de productos (búsqueda, lector USB y cámara) ----------
// alElegir(producto) recibe el producto elegido; sin callback, abre su ficha.
window.buscadorProductos = (alElegir = null, opciones = {}) => ({
  q: "", resultados: [], abierto: false, activo: 0, cargando: false,
  async buscar() {
    const q = this.q.trim();
    if (q.length < 2) { this.resultados = []; this.abierto = false; return; }
    this.cargando = true;
    try {
      const datos = await api(`/api/productos?q=${encodeURIComponent(q)}${opciones.sede ? "&sede=" + opciones.sede : ""}`);
      if (q !== this.q.trim()) return;
      this.resultados = datos.productos;
      this.activo = 0;
      this.abierto = true;
    } catch (e) { avisar(e.message, "error"); }
    finally { this.cargando = false; }
  },
  async enter() {
    const q = this.q.trim();
    if (!q) return;
    // Un lector USB escribe el código y presiona Enter: si coincide exacto, se toma directo
    if (/^\d{6,14}$/.test(q)) return this.porCodigo(q);
    if (this.resultados.length) this.elegir(this.resultados[this.activo]);
  },
  async porCodigo(codigo) {
    try {
      const datos = await api(`/api/productos/codigo/${encodeURIComponent(codigo)}${opciones.sede ? "?sede=" + opciones.sede : ""}`);
      this.elegir(datos.producto);
    } catch (e) { avisar(e.message, "error"); }
  },
  elegir(p) {
    if (!p) return;
    this.q = ""; this.resultados = []; this.abierto = false;
    if (alElegir) alElegir(p);
    else window.location.href = `/productos/${p.id}`;
  },
  mover(paso) {
    if (!this.resultados.length) return;
    this.activo = (this.activo + paso + this.resultados.length) % this.resultados.length;
  },
  camara() { escanearCamara((codigo) => this.porCodigo(codigo)); },
});

// htmx: mantener el CSRF simple (misma página) y desactivar botones mientras envía
document.addEventListener("htmx:configRequest", (e) => { e.detail.headers["X-Requested-With"] = "htmx"; });
