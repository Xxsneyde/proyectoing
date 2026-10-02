"""Traslados de mercancía entre sedes."""

import json

from fastapi import APIRouter, Body, Depends, Form, Request

from erp import traslados
from erp.db import ErrorNegocio, todos, transaccion, uno
from erp.rutas.base import Ctx, numero, redirigir, requiere

router = APIRouter()


@router.get("/traslados")
def lista(ctx: Ctx = Depends(requiere("traslados"))):
    s = ctx.sede_id
    base = """SELECT t.*, o.nombre AS origen, d.nombre AS destino, ue.nombre AS envia, ur.nombre AS recibe,
                     (SELECT COUNT(*) FROM traslado_items WHERE traslado_id = t.id) AS lineas,
                     (SELECT COALESCE(SUM(ti.cantidad * p.costo), 0) FROM traslado_items ti JOIN productos p ON p.id = ti.producto_id
                      WHERE ti.traslado_id = t.id) AS valor
              FROM traslados t JOIN sedes o ON o.id = t.origen_id JOIN sedes d ON d.id = t.destino_id
              LEFT JOIN usuarios ue ON ue.id = t.usuario_envia LEFT JOIN usuarios ur ON ur.id = t.usuario_recibe"""
    filtro = "" if ctx.usuario["todas_las_sedes"] else " AND (t.origen_id = ? OR t.destino_id = ?)"
    params = () if ctx.usuario["todas_las_sedes"] else (s, s)
    return ctx.render("traslados/lista.html",
                      por_recibir=todos(ctx.con, base + " WHERE t.estado = 'en_transito' AND t.destino_id = ? ORDER BY t.enviado", s),
                      en_camino=todos(ctx.con, base + f" WHERE t.estado = 'en_transito' AND t.destino_id <> ?{filtro} ORDER BY t.enviado", s, *params),
                      borradores=todos(ctx.con, base + f" WHERE t.estado = 'borrador'{filtro} ORDER BY t.id DESC", *params),
                      recientes=todos(ctx.con, base + f" WHERE t.estado IN ('recibido', 'anulado'){filtro} ORDER BY t.id DESC LIMIT 30", *params))


def _sugerencias(ctx: Ctx) -> dict:
    """Por cada sede destino: lo que allá está bajo el mínimo y aquí sobra por encima del mínimo."""
    salida = {}
    for d in todos(ctx.con, "SELECT id FROM sedes WHERE activa = 1 AND id <> ?", ctx.sede_id):
        filas = todos(ctx.con, """
            SELECT p.id AS producto_id, p.nombre, p.codigo, p.unidad, o.cantidad AS existencia,
                   MIN(e.maximo - e.cantidad, o.cantidad - o.minimo) AS cantidad
            FROM existencias e JOIN existencias o ON o.producto_id = e.producto_id AND o.sede_id = ?
            JOIN productos p ON p.id = e.producto_id AND p.activo = 1
            WHERE e.sede_id = ? AND e.minimo > 0 AND e.cantidad <= e.minimo AND o.cantidad - o.minimo >= 1
            ORDER BY e.cantidad / e.minimo LIMIT 25""", ctx.sede_id, d["id"])
        for f in filas:
            f["cantidad"] = max(1, round(f["cantidad"]))
        salida[d["id"]] = filas
    return salida


@router.get("/traslados/nuevo")
def nuevo(ctx: Ctx = Depends(requiere("traslados"))):
    destinos = todos(ctx.con, "SELECT id, nombre FROM sedes WHERE activa = 1 AND id <> ? ORDER BY id", ctx.sede_id)
    return ctx.render("traslados/nuevo.html", destinos=destinos, sugerencias=json.dumps(_sugerencias(ctx)))


@router.post("/api/traslados")
def api_crear(datos: dict = Body(...), ctx: Ctx = Depends(requiere("traslados"))):
    items = [{"producto_id": int(i["producto_id"]), "cantidad": float(i["cantidad"])}
             for i in datos.get("items", []) if float(i.get("cantidad") or 0) > 0]
    with transaccion(ctx.con):
        tid = traslados.crear(ctx.con, ctx.sede_id, int(datos.get("destino_id") or 0), items, ctx.usuario["id"], str(datos.get("nota", "")))
        if datos.get("enviar"):
            traslados.enviar(ctx.con, tid, ctx.usuario["id"])
    return {"id": tid}


def _traslado(ctx: Ctx, traslado_id: int) -> dict:
    t = uno(ctx.con, """SELECT t.*, o.nombre AS origen, d.nombre AS destino, ue.nombre AS envia, ur.nombre AS recibe
                        FROM traslados t JOIN sedes o ON o.id = t.origen_id JOIN sedes d ON d.id = t.destino_id
                        LEFT JOIN usuarios ue ON ue.id = t.usuario_envia LEFT JOIN usuarios ur ON ur.id = t.usuario_recibe
                        WHERE t.id = ?""", traslado_id)
    if not t or (not ctx.usuario["todas_las_sedes"] and ctx.sede_id not in (t["origen_id"], t["destino_id"])):
        raise ErrorNegocio("El traslado no existe.")
    return t


@router.get("/traslados/{traslado_id}")
def detalle(traslado_id: int, ctx: Ctx = Depends(requiere("traslados"))):
    t = _traslado(ctx, traslado_id)
    items = todos(ctx.con, """SELECT ti.*, p.nombre, p.codigo, p.unidad, p.costo FROM traslado_items ti
                              JOIN productos p ON p.id = ti.producto_id WHERE ti.traslado_id = ? ORDER BY p.nombre""", traslado_id)
    return ctx.render("traslados/detalle.html", t=t, items=items,
                      en_origen=ctx.sede_id == t["origen_id"], en_destino=ctx.sede_id == t["destino_id"],
                      valor=sum(i["cantidad"] * i["costo"] for i in items))


@router.post("/traslados/{traslado_id}/enviar")
def enviar(traslado_id: int, ctx: Ctx = Depends(requiere("traslados"))):
    t = _traslado(ctx, traslado_id)
    if ctx.sede_id != t["origen_id"]:
        raise ErrorNegocio(f"El envío se registra desde la sede {t['origen']}.")
    with transaccion(ctx.con):
        traslados.enviar(ctx.con, traslado_id, ctx.usuario["id"])
    return redirigir(ctx, f"/traslados/{traslado_id}", f"Enviado: la mercancía salió del inventario de {t['origen']}.")


@router.post("/traslados/{traslado_id}/recibir")
async def recibir(traslado_id: int, request: Request, ctx: Ctx = Depends(requiere("traslados"))):
    t = _traslado(ctx, traslado_id)
    if ctx.sede_id != t["destino_id"]:
        raise ErrorNegocio(f"La recepción se registra desde la sede {t['destino']}.")
    form = await request.form()
    recibidos = {int(k[9:]): numero(v) for k, v in form.items() if k.startswith("recibido_") and str(v).strip() != ""}
    with transaccion(ctx.con):
        faltantes = traslados.recibir(ctx.con, traslado_id, recibidos, ctx.usuario["id"])
    texto = "Recibido completo." if not faltantes else f"Recibido con faltantes: {', '.join(faltantes)}."
    return redirigir(ctx, f"/traslados/{traslado_id}", texto)


@router.post("/traslados/{traslado_id}/anular")
def anular(traslado_id: int, motivo: str = Form(...), ctx: Ctx = Depends(requiere("traslados"))):
    t = _traslado(ctx, traslado_id)
    if ctx.sede_id != t["origen_id"] and not ctx.puede("panel"):
        raise ErrorNegocio("Solo la sede de origen o la administración pueden anular el traslado.")
    with transaccion(ctx.con):
        traslados.anular(ctx.con, traslado_id, ctx.usuario["id"], motivo)
    return redirigir(ctx, f"/traslados/{traslado_id}", "Traslado anulado.")
