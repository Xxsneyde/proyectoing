"""Piezas comunes de la web: plantillas, contexto de la petición, permisos y respuestas."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from erp import auth
from erp.db import ErrorNegocio, conectar, config, todos, valor
from erp.formato import cant, fecha, pct, pesos

PLANTILLAS = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "plantillas"))

ICONOS = {
    "panel": '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
    "venta": '<circle cx="9" cy="20" r="1.4"/><circle cx="18" cy="20" r="1.4"/><path d="M2.5 3h2.6l2.3 11.2a1.5 1.5 0 0 0 1.5 1.2h8.7a1.5 1.5 0 0 0 1.5-1.1L21 7H6"/>',
    "caja": '<rect x="2.5" y="6" width="19" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 9.5v.01M18 14.5v.01"/>',
    "inventario": '<path d="M21 8.5 12 3 3 8.5v7L12 21l9-5.5z"/><path d="M3 8.5 12 14l9-5.5M12 14v7"/>',
    "conteo": '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V3h6v1M9 13l2 2 4-4"/>',
    "producto": '<path d="M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9z"/><circle cx="7.5" cy="7.5" r="1.3"/>',
    "compra": '<path d="M3 6h11v10H3zM14 9h4l3 3.5V16h-7"/><circle cx="7" cy="17.5" r="1.8"/><circle cx="17" cy="17.5" r="1.8"/>',
    "traslado": '<path d="M4 8h15l-3.5-3.5M20 16H5l3.5 3.5"/>',
    "cliente": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M21.5 20a6.5 6.5 0 0 0-4-6"/>',
    "cartera": '<path d="M5 3h14v18l-3-2-2 2-2-2-2 2-2-2-3 2z"/><path d="M9 8h6M9 12h6"/>',
    "proveedor": '<path d="M3 21V9l6 4V9l6 4V5h6v16z"/><path d="M3 21h18"/>',
    "reporte": '<path d="M4 20V11M10 20V5M16 20v-7M21 20H3"/>',
    "ajustes": '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    "auditoria": '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/>',
    "buscar": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "codigo": '<path d="M4 7V5a1 1 0 0 1 1-1h2M17 4h2a1 1 0 0 1 1 1v2M20 17v2a1 1 0 0 1-1 1h-2M7 20H5a1 1 0 0 1-1-1v-2M8 8v8M11 8v8M14 8v8M17 8v8"/>',
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>',
    "alerta": '<path d="M12 3.5 2.5 20h19z"/><path d="M12 10v4M12 17h.01"/>',
    "critico": '<circle cx="12" cy="12" r="9"/><path d="m15 9-6 6M9 9l6 6"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    "mas": '<path d="M12 5v14M5 12h14"/>',
    "menos": '<path d="M5 12h14"/>',
    "x": '<path d="M18 6 6 18M6 6l12 12"/>',
    "salir": '<path d="M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4M10 17l5-5-5-5M15 12H3"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "sube": '<path d="M7 17 17 7M9 7h8v8"/>',
    "baja": '<path d="m7 7 10 10M17 9v8H9"/>',
    "reloj": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "descarga": '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
    "llave": '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.2L3.5 17.3a1.5 1.5 0 0 0 2.1 2.1l5.8-5.8a4 4 0 0 0 5.2-5.4l-2.4 2.4-2.2-.6-.6-2.2z"/>',
    "camara": '<path d="M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"/><circle cx="12" cy="13.5" r="3.5"/>',
    "usuario": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "sede": '<path d="M3 9.5 4.5 4h15L21 9.5M3 9.5h18v1a3 3 0 0 1-6 0 3 3 0 0 1-6 0 3 3 0 0 1-6 0zM5 13.3V20h14v-6.7M10 20v-4h4v4"/>',
    "editar": '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
    "asistente": '<path d="M12 3.5 13.6 8a2 2 0 0 0 1.2 1.2L19.5 11l-4.7 1.8a2 2 0 0 0-1.2 1.2L12 18.5 10.4 14a2 2 0 0 0-1.2-1.2L4.5 11l4.7-1.8A2 2 0 0 0 10.4 8z"/><path d="M19 3v4M17 5h4M5 17v3M3.5 18.5h3"/>',
    "enviar": '<path d="M4 12 20 4l-4 16-4.5-6.5z"/><path d="m11.5 13.5 4-4"/>',
    "parlante": '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>',
    "nota": '<path d="M5 3.5h10l4 4V20a.5.5 0 0 1-.5.5h-13A.5.5 0 0 1 5 20z"/><path d="M15 3.5v4h4M8.5 12h7M8.5 16h5"/>',
}


def icono(nombre: str, clase: str = "") -> Markup:
    return Markup(
        f'<svg class="{clase}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONOS[nombre]}</svg>'
    )


PLANTILLAS.env.globals.update(icono=icono, puede=auth.puede, ROLES=auth.ROLES)
MEDIOS_PAGO = {"efectivo": "Efectivo", "tarjeta": "Tarjeta", "transferencia": "Transferencia", "credito": "Crédito"}
PLANTILLAS.env.filters.update(pesos=pesos, cant=cant, fecha=fecha, pct=pct, medio=lambda m: MEDIOS_PAGO.get(m, m),
                              fecha_corta=lambda t: fecha(t, hora=False))

MENU = [
    ("Operación", [
        ("asistente", "/asistente", "Asistente", "asistente"),
        ("panel", "/panel", "Panel", "panel"),
        ("ventas", "/ventas/pos", "Vender", "venta"),
        ("caja", "/caja", "Caja", "caja"),
        ("ventas", "/ventas", "Ventas", "cartera"),
    ]),
    ("Inventario", [
        ("productos.ver", "/productos", "Productos", "producto"),
        ("inventario.ver", "/inventario", "Existencias", "inventario"),
        ("conteos", "/conteos", "Conteos", "conteo"),
        ("traslados", "/traslados", "Traslados", "traslado"),
    ]),
    ("Compras y clientes", [
        ("compras.ver", "/compras", "Compras", "compra"),
        ("compras.gestionar", "/proveedores", "Proveedores", "proveedor"),
        ("clientes", "/clientes", "Clientes y cartera", "cliente"),
    ]),
    ("Gestión", [
        ("reportes", "/reportes", "Reportes", "reporte"),
        ("auditoria", "/auditoria", "Auditoría", "auditoria"),
        ("admin", "/admin", "Configuración", "ajustes"),
    ]),
]


class NoAutenticado(Exception):
    pass


class SinPermiso(Exception):
    pass


@dataclass
class Ctx:
    request: Request
    con: object
    usuario: dict
    sede_id: int
    extra_cookies: list = field(default_factory=list)

    def puede(self, permiso: str) -> bool:
        return auth.puede(self.usuario, permiso)

    def exigir(self, permiso: str):
        if not self.puede(permiso):
            raise SinPermiso()

    @property
    def es_htmx(self) -> bool:
        return self.request.headers.get("HX-Request") == "true"

    def sede_filtro(self) -> int | None:
        """Sede para reportes y panel: el dueño puede ver todas (?sede=todas)."""
        pedido = self.request.query_params.get("sede")
        if self.usuario["todas_las_sedes"] and pedido in (None, "", "todas"):
            return None
        if self.usuario["todas_las_sedes"] and pedido and pedido.isdigit():
            return int(pedido)
        return self.sede_id

    def menu(self) -> list:
        contadores = self._contadores()
        salida = []
        for grupo, items in MENU:
            visibles = [{"url": url, "texto": texto, "icono": ic, "contador": contadores.get(url)}
                        for permiso, url, texto, ic in items if self.puede(permiso)]
            if visibles:
                salida.append((grupo, visibles))
        # La opción activa es la de ruta más larga que coincide: /ventas/pos no marca también /ventas
        ruta = self.request.url.path
        candidatos = [it for _, items in salida for it in items if ruta == it["url"] or ruta.startswith(it["url"] + "/")]
        if candidatos:
            max(candidatos, key=lambda it: len(it["url"]))["actual"] = True
        return salida

    def _contadores(self) -> dict:
        s = self.sede_id
        c = {
            "/inventario": valor(self.con, "SELECT COUNT(*) FROM existencias e JOIN productos p ON p.id = e.producto_id AND p.activo = 1 "
                                           "WHERE e.sede_id = ? AND e.minimo > 0 AND e.cantidad <= 0", s, defecto=0),
            "/conteos": valor(self.con, "SELECT COUNT(*) FROM conteos WHERE sede_id = ? AND estado = 'abierto'", s, defecto=0),
            "/traslados": valor(self.con, "SELECT COUNT(*) FROM traslados WHERE destino_id = ? AND estado = 'en_transito'", s, defecto=0),
            "/compras": valor(self.con, "SELECT COUNT(*) FROM compras WHERE sede_id = ? AND estado IN ('enviada', 'parcial')", s, defecto=0),
        }
        return {k: v for k, v in c.items() if v}

    def render(self, plantilla: str, status: int = 200, **datos) -> HTMLResponse:
        crudo = self.request.cookies.get("aviso")
        aviso = unquote(crudo) if crudo else None
        ctx = {
            "request": self.request, "usuario": self.usuario, "sede_id": self.sede_id, "ctx": self,
            "empresa": config(self.con, "empresa_nombre", "Ferretería"),
            "ruta": self.request.url.path, "aviso": aviso, "nube": getattr(self.request.app.state, "nube", False),
            **datos,
        }
        if not self.es_htmx or plantilla.startswith("base"):
            ctx["menu"] = self.menu()
            ctx["sedes"] = todos(self.con, "SELECT id, nombre FROM sedes WHERE activa = 1 ORDER BY id")
        resp = PLANTILLAS.TemplateResponse(self.request, plantilla, ctx, status_code=status)
        if crudo:
            resp.delete_cookie("aviso")
        return resp


def obtener_con():
    con = conectar()
    try:
        yield con
    finally:
        con.close()


def requiere(permiso: str | None = None):
    def dependencia(request: Request, con=Depends(obtener_con)) -> Ctx:
        usuario = auth.usuario_de_sesion(con, request.cookies.get("sesion"))
        if not usuario:
            raise NoAutenticado()
        ctx = Ctx(request, con, usuario, usuario["sede_id"])
        if permiso:
            ctx.exigir(permiso)
        return ctx
    return dependencia


def redirigir(ctx: Ctx | None, url: str, aviso: str | None = None) -> Response:
    """Tras un POST exitoso: htmx navega con HX-Redirect; el aviso se muestra en la página siguiente."""
    if ctx and ctx.es_htmx:
        resp = Response(status_code=200, headers={"HX-Redirect": url})
    else:
        resp = RedirectResponse(url, status_code=303)
    if aviso:
        resp.set_cookie("aviso", quote(aviso), max_age=30, httponly=False, samesite="lax", secure=True)
    return resp


def aviso_htmx(texto: str, tipo: str = "ok") -> Response:
    """Respuesta htmx sin cambiar la página, solo con un aviso emergente."""
    return Response(status_code=200, headers={
        "HX-Trigger": json.dumps({"aviso": {"texto": texto, "tipo": tipo}}, ensure_ascii=True),
        "HX-Reswap": "none",
    })


def error_negocio(request: Request, exc: ErrorNegocio) -> Response:
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": str(exc)}, status_code=422)
    if request.headers.get("HX-Request") == "true":
        return aviso_htmx(str(exc), "error")
    return HTMLResponse(f"<p>{exc}</p>", status_code=422)


def numero(texto, defecto=0.0) -> float:
    """'1.234,5' o '1234.5' -> 1234.5 (lo que escribe la gente en Colombia)."""
    if texto is None or str(texto).strip() == "":
        return defecto
    t = str(texto).strip().replace("$", "").replace(" ", "")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1 or (t.count(".") == 1 and len(t.split(".")[1]) == 3):
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        raise ErrorNegocio(f"«{texto}» no es un número válido.")
