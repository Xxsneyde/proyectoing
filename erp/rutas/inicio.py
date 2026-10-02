"""Ingreso, cambio de sede, panel de monitoreo y búsqueda de productos."""

from datetime import date, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from erp import auth, graficos, reportes
from erp.db import ErrorNegocio, config, hoy, todos, transaccion, uno, valor
from erp.demo import CLAVE_DEMO
from erp.formato import pesos
from erp.rutas.base import PLANTILLAS, Ctx, obtener_con, redirigir, requiere

router = APIRouter()
DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


@router.get("/")
def raiz(request: Request, con=Depends(obtener_con)):
    usuario = auth.usuario_de_sesion(con, request.cookies.get("sesion"))
    if not usuario:
        return RedirectResponse("/ingresar", status_code=303)
    return RedirectResponse(auth.INICIO_POR_ROL[usuario["rol"]], status_code=303)


def _pagina_ingreso(request, con, error="", siguiente="/", usuario_previo="", status=200):
    return PLANTILLAS.TemplateResponse(request, "login.html", {
        "empresa": config(con, "empresa_nombre", "Ferretería"), "error": error, "siguiente": siguiente,
        "usuario_previo": usuario_previo, "demo": config(con, "demo") == "1", "clave_demo": CLAVE_DEMO,
    }, status_code=status)


@router.get("/ingresar")
def ingresar_form(request: Request, siguiente: str = "/", con=Depends(obtener_con)):
    return _pagina_ingreso(request, con, siguiente=siguiente)


@router.post("/ingresar")
def ingresar(request: Request, usuario: str = Form(...), clave: str = Form(...), siguiente: str = Form("/"),
             con=Depends(obtener_con)):
    try:
        with transaccion(con):
            token = auth.iniciar_sesion(con, usuario, clave)
    except ErrorNegocio as e:
        return _pagina_ingreso(request, con, str(e), siguiente, usuario, status=401)
    destino = siguiente if siguiente.startswith("/") and not siguiente.startswith("//") else "/"
    resp = RedirectResponse(destino, status_code=303)
    resp.set_cookie("sesion", token, httponly=True, samesite="lax", secure=True, max_age=12 * 3600)
    return resp


@router.post("/salir")
def salir(request: Request, con=Depends(obtener_con)):
    token = request.cookies.get("sesion")
    if token:
        with transaccion(con):
            auth.cerrar_sesion(con, token)
    resp = RedirectResponse("/ingresar", status_code=303)
    resp.delete_cookie("sesion")
    return resp


@router.post("/sesion/sede")
def cambiar_sede(sede_id: int = Form(...), ctx: Ctx = Depends(requiere())):
    with transaccion(ctx.con):
        auth.cambiar_sede(ctx.con, ctx.usuario, sede_id)
    nombre = valor(ctx.con, "SELECT nombre FROM sedes WHERE id = ?", sede_id)
    destino = ctx.request.headers.get("HX-Current-URL", "/")
    return redirigir(ctx, destino.split("?")[0] if "/panel" not in destino else "/panel", f"Ahora trabajas en la sede {nombre}.")


def _datos_panel(ctx: Ctx) -> dict:
    sede = ctx.sede_filtro()
    k = reportes.kpis(ctx.con, sede)
    dias = reportes.ventas_diarias(ctx.con, sede, 30)
    orden = [s["nombre"] for s in todos(ctx.con, "SELECT nombre FROM sedes WHERE activa = 1 ORDER BY id")]
    series = [s for s in orden if any(d["sedes"].get(s) for d in dias)] or orden[:1]
    h = date.fromisoformat(hoy())
    desde = (h - timedelta(days=29)).isoformat()

    def delta(actual, antes):
        return round(100 * (actual - antes) / antes, 1) if antes else None

    alertas = []

    def alerta(nivel, ic, titulo, texto, cifra, url):
        alertas.append({"nivel": nivel, "icono": ic, "titulo": titulo, "texto": texto, "cifra": cifra, "url": url})

    if k["negativos"]:
        alerta("critico", "critico", "Existencias negativas", "Se vendió más de lo registrado: revisa con un conteo", k["negativos"], "/inventario?filtro=negativos")
    if k["agotados"]:
        alerta("critico", "critico", "Productos agotados", "Con mínimo definido y sin existencias", k["agotados"], "/inventario?filtro=agotados")
    if k["cxp_vencidas"]:
        alerta("critico", "reloj", "Pagos a proveedores vencidos", "Facturas de compra sin pagar después del plazo", k["cxp_vencidas"], "/compras?vista=por_pagar")
    if k["bajo_minimo"]:
        alerta("aviso", "alerta", "Bajo el mínimo", "Hay un pedido sugerido listo para revisar", k["bajo_minimo"], "/compras/nueva")
    if k["cartera_vencida"]:
        alerta("serio", "cartera", "Cartera vencida", f"De {pesos(k['cartera_total'])} por cobrar", pesos(k["cartera_vencida"]), "/clientes?vista=cartera")
    if k["descuadres"]:
        alerta("serio", "caja", "Descuadres de caja", "Cierres con diferencia en los últimos 7 días", len(k["descuadres"]), "/caja/historial")
    if k["cxp_7dias"]:
        alerta("aviso", "reloj", "Pagos a proveedores esta semana", "Vencen en los próximos 7 días", pesos(k["cxp_7dias"]), "/compras?vista=por_pagar")
    if k["traslados_transito"]:
        alerta("info", "traslado", "Traslados en camino", "Mercancía enviada que falta recibir", k["traslados_transito"], "/traslados")
    if k["compras_por_recibir"]:
        alerta("info", "compra", "Compras por recibir", "Pedidos enviados a proveedores", k["compras_por_recibir"], "/compras?vista=por_recibir")
    if k["conteos_abiertos"]:
        alerta("info", "conteo", "Conteos abiertos", "Pendientes de terminar y aprobar", k["conteos_abiertos"], "/conteos")

    top = reportes.top_productos(ctx.con, sede, desde, h.isoformat(), 6)
    cats = reportes.ventas_por(ctx.con, "categoria", sede, desde, h.isoformat())[:6]
    semana_pasada = h - timedelta(days=7)
    return {
        "k": k, "dias": dias, "series": series, "sede_panel": sede,
        "nombre_sede": valor(ctx.con, "SELECT nombre FROM sedes WHERE id = ?", sede) if sede else "",
        "grafico": graficos.columnas_apiladas(dias, series, "Ventas diarias de los últimos 30 días"),
        "delta_hoy": delta(k["hoy"]["total"], k["semana_pasada"]["total"]),
        "dia_semana_pasada": DIAS[semana_pasada.weekday()],
        "delta_mes": delta(k["mes"]["total"], k["mes_anterior"]["total"]),
        "alertas": alertas, "hora": datetime.now().strftime("%-I:%M %p").lower().replace("am", "a. m.").replace("pm", "p. m."),
        "top": graficos.barras(top, detalle=lambda f: [
            {"nombre": "Cantidad", "valor": f"{f['cantidad']:g} {f['unidad']}".replace(".", ",")},
            {"nombre": "Utilidad", "valor": pesos(f["utilidad"])}]) if top else None,
        "categorias": graficos.barras(cats, detalle=lambda f: [{"nombre": "Utilidad", "valor": pesos(f["utilidad"])}]) if cats else None,
        "actividad": reportes.actividad(ctx.con, sede, 8),
    }


@router.get("/panel")
def panel(ctx: Ctx = Depends(requiere("panel"))):
    return ctx.render("panel.html", **_datos_panel(ctx))


@router.get("/panel/contenido")
def panel_contenido(ctx: Ctx = Depends(requiere("panel"))):
    return ctx.render("panel_contenido.html", **_datos_panel(ctx))


# --- Productos para buscadores, POS y escáner ---------------------------

def _producto_json(p: dict) -> dict:
    return {k: p[k] for k in ("id", "codigo", "nombre", "unidad", "precio", "costo", "iva", "existencia", "categoria", "ubicacion")
            if k in p}


@router.get("/api/productos")
def api_productos(q: str = "", sede: int | None = None, ctx: Ctx = Depends(requiere("productos.ver"))):
    q = q.strip()
    if len(q) < 2:
        return {"productos": []}
    sede = sede or ctx.sede_id
    palabras = [w for w in q.lower().split() if w]
    condiciones = " AND ".join(["LOWER(p.nombre) LIKE ?"] * len(palabras))
    filas = todos(ctx.con, f"""
        SELECT p.id, p.codigo, p.nombre, p.unidad, p.precio, p.costo, p.iva, c.nombre AS categoria,
               COALESCE(e.cantidad, 0) AS existencia, COALESCE(e.ubicacion, '') AS ubicacion
        FROM productos p LEFT JOIN categorias c ON c.id = p.categoria_id
        LEFT JOIN existencias e ON e.producto_id = p.id AND e.sede_id = ?
        WHERE p.activo = 1 AND (p.codigo = ? OR ({condiciones}))
        ORDER BY (p.codigo = ?) DESC, (LOWER(p.nombre) LIKE ?) DESC, p.nombre LIMIT 12""",
                  sede, q, *[f"%{w}%" for w in palabras], q, f"{q.lower()}%")
    return {"productos": [_producto_json(f) for f in filas]}


@router.get("/api/productos/codigo/{codigo}")
def api_producto_codigo(codigo: str, sede: int | None = None, ctx: Ctx = Depends(requiere("productos.ver"))):
    p = uno(ctx.con, """
        SELECT p.id, p.codigo, p.nombre, p.unidad, p.precio, p.costo, p.iva, c.nombre AS categoria,
               COALESCE(e.cantidad, 0) AS existencia, COALESCE(e.ubicacion, '') AS ubicacion
        FROM productos p LEFT JOIN categorias c ON c.id = p.categoria_id
        LEFT JOIN existencias e ON e.producto_id = p.id AND e.sede_id = ?
        WHERE p.codigo = ? AND p.activo = 1""", sede or ctx.sede_id, codigo.strip())
    if not p:
        raise ErrorNegocio(f"No hay ningún producto con el código {codigo}.")
    return {"producto": _producto_json(p)}


@router.get("/productos/codigo/{codigo}")
def producto_por_codigo(codigo: str, ctx: Ctx = Depends(requiere("productos.ver"))):
    p = uno(ctx.con, "SELECT id FROM productos WHERE codigo = ?", codigo.strip())
    if not p:
        return redirigir(None, f"/productos?q={quote(codigo)}", f"No hay ningún producto con el código {codigo}.")
    return RedirectResponse(f"/productos/{p['id']}", status_code=303)
