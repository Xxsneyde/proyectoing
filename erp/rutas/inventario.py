"""Existencias por sede y movimientos de inventario."""

import math

from fastapi import APIRouter, Depends

from erp.db import todos, valor
from erp.rutas.base import Ctx, requiere

router = APIRouter()
FILTROS = {
    "": "Todos", "bajo": "Bajo el mínimo", "agotados": "Agotados", "negativos": "Negativos",
    "sin_movimiento": "Sin ventas en 60 días", "sin_conteo": "Sin contar en 60 días",
}
TIPOS = {
    "venta": "Ventas", "compra": "Compras", "traslado_salida": "Traslados (salida)", "traslado_entrada": "Traslados (entrada)",
    "ajuste": "Ajustes manuales", "conteo": "Ajustes por conteo", "danado": "Dañados", "devolucion": "Devoluciones",
    "anulacion_venta": "Anulaciones", "inicial": "Inventario inicial",
}
POR_PAGINA = 60


def _sede(ctx: Ctx) -> int:
    pedido = ctx.request.query_params.get("sede")
    if ctx.usuario["todas_las_sedes"] and pedido and pedido.isdigit():
        return int(pedido)
    return ctx.sede_id


@router.get("/inventario")
def existencias(filtro: str = "", q: str = "", categoria: int | None = None, pagina: int = 1,
                ctx: Ctx = Depends(requiere("inventario.ver"))):
    sede = _sede(ctx)
    condiciones, params = ["p.activo = 1", "e.sede_id = ?"], [sede]
    if filtro == "bajo":
        condiciones.append("e.minimo > 0 AND e.cantidad <= e.minimo")
    elif filtro == "agotados":
        condiciones.append("e.minimo > 0 AND e.cantidad <= 0")
    elif filtro == "negativos":
        condiciones.append("e.cantidad < 0")
    elif filtro == "sin_conteo":
        condiciones.append("(e.ultimo_conteo IS NULL OR e.ultimo_conteo < datetime('now', 'localtime', '-60 days'))")
    elif filtro == "sin_movimiento":
        condiciones.append("e.cantidad > 0 AND NOT EXISTS (SELECT 1 FROM movimientos m WHERE m.producto_id = p.id "
                           "AND m.sede_id = e.sede_id AND m.tipo = 'venta' AND m.fecha >= datetime('now', 'localtime', '-60 days'))")
    for palabra in q.lower().split():
        condiciones.append("(LOWER(p.nombre) LIKE ? OR p.codigo LIKE ? OR LOWER(e.ubicacion) LIKE ?)")
        params += [f"%{palabra}%", f"{palabra}%", f"%{palabra}%"]
    if categoria:
        condiciones.append("p.categoria_id = ?")
        params.append(categoria)
    donde = " AND ".join(condiciones)
    base = f"FROM existencias e JOIN productos p ON p.id = e.producto_id LEFT JOIN categorias c ON c.id = p.categoria_id WHERE {donde}"
    total = valor(ctx.con, f"SELECT COUNT(*) {base}", *params, defecto=0)
    paginas = max(1, math.ceil(total / POR_PAGINA))
    pagina = min(max(1, pagina), paginas)
    filas = todos(ctx.con, f"""
        SELECT p.id, p.codigo, p.nombre, p.unidad, p.costo, c.nombre AS categoria, e.cantidad, e.minimo, e.maximo,
               e.ubicacion, e.ultimo_conteo, ROUND(MAX(e.cantidad, 0) * p.costo) AS valor {base}
        ORDER BY {'e.cantidad / NULLIF(e.minimo, 0), ' if filtro in ('bajo', 'agotados') else ''}{'valor DESC, ' if filtro == 'sin_movimiento' else ''}e.ubicacion, p.nombre
        LIMIT ? OFFSET ?""", *params, POR_PAGINA, (pagina - 1) * POR_PAGINA)
    resumen = {
        "valor": valor(ctx.con, "SELECT SUM(MAX(e.cantidad, 0) * p.costo) FROM existencias e JOIN productos p ON p.id = e.producto_id "
                                "WHERE e.sede_id = ? AND p.activo = 1", sede, defecto=0),
        "bajo": valor(ctx.con, "SELECT COUNT(*) FROM existencias e JOIN productos p ON p.id = e.producto_id AND p.activo = 1 "
                               "WHERE e.sede_id = ? AND e.minimo > 0 AND e.cantidad <= e.minimo", sede, defecto=0),
        "agotados": valor(ctx.con, "SELECT COUNT(*) FROM existencias e JOIN productos p ON p.id = e.producto_id AND p.activo = 1 "
                                   "WHERE e.sede_id = ? AND e.minimo > 0 AND e.cantidad <= 0", sede, defecto=0),
        "productos": valor(ctx.con, "SELECT COUNT(*) FROM existencias e JOIN productos p ON p.id = e.producto_id AND p.activo = 1 "
                                    "WHERE e.sede_id = ? AND e.cantidad > 0", sede, defecto=0),
    }
    datos = {"filas": filas, "filtro": filtro, "filtros": FILTROS, "q": q, "categoria": categoria, "pagina": pagina,
             "paginas": paginas, "total": total, "resumen": resumen, "sede_ver": sede,
             "nombre_sede": valor(ctx.con, "SELECT nombre FROM sedes WHERE id = ?", sede),
             "categorias": todos(ctx.con, "SELECT * FROM categorias ORDER BY nombre")}
    if ctx.es_htmx and ctx.request.headers.get("HX-Target") == "tabla":
        return ctx.render("inventario/_tabla.html", **datos)
    return ctx.render("inventario/lista.html", **datos)


@router.get("/inventario/movimientos")
def movimientos(tipo: str = "", desde: str = "", hasta: str = "", pagina: int = 1,
                ctx: Ctx = Depends(requiere("inventario.ver"))):
    sede = _sede(ctx)
    condiciones, params = ["m.sede_id = ?"], [sede]
    if tipo in TIPOS:
        condiciones.append("m.tipo = ?")
        params.append(tipo)
    if desde:
        condiciones.append("m.fecha >= ?")
        params.append(desde)
    if hasta:
        condiciones.append("m.fecha < date(?, '+1 day')")
        params.append(hasta)
    donde = " AND ".join(condiciones)
    total = valor(ctx.con, f"SELECT COUNT(*) FROM movimientos m WHERE {donde}", *params, defecto=0)
    paginas = max(1, math.ceil(total / POR_PAGINA))
    pagina = min(max(1, pagina), paginas)
    filas = todos(ctx.con, f"""
        SELECT m.*, p.nombre, p.unidad, u.nombre AS usuario, ROUND(m.cantidad * m.costo_unitario) AS valor
        FROM movimientos m JOIN productos p ON p.id = m.producto_id LEFT JOIN usuarios u ON u.id = m.usuario_id
        WHERE {donde} ORDER BY m.id DESC LIMIT ? OFFSET ?""", *params, POR_PAGINA, (pagina - 1) * POR_PAGINA)
    return ctx.render("inventario/movimientos.html", filas=filas, tipos=TIPOS, tipo=tipo, desde=desde, hasta=hasta,
                      pagina=pagina, paginas=paginas, total=total,
                      nombre_sede=valor(ctx.con, "SELECT nombre FROM sedes WHERE id = ?", sede))
