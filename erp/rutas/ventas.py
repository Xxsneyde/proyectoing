"""Punto de venta, historial de ventas, anulaciones y devoluciones."""

import math
from datetime import date, timedelta

from fastapi import APIRouter, Body, Depends, Form, Request

from erp import caja as caja_mod, ventas
from erp.db import ErrorNegocio, hoy, todos, transaccion, uno
from erp.rutas.base import Ctx, numero, redirigir, requiere

router = APIRouter()
POR_PAGINA = 50


@router.get("/ventas/pos")
def pos(ctx: Ctx = Depends(requiere("ventas"))):
    return ctx.render("ventas/pos.html", caja=caja_mod.caja_abierta(ctx.con, ctx.sede_id, ctx.usuario["id"]))


@router.post("/api/ventas")
def api_vender(datos: dict = Body(...), ctx: Ctx = Depends(requiere("ventas"))):
    items = [{"producto_id": int(i["producto_id"]), "cantidad": float(i["cantidad"]), "descuento": int(i.get("descuento") or 0)}
             for i in datos.get("items", [])]
    with transaccion(ctx.con):
        v = ventas.vender(ctx.con, sede_id=ctx.sede_id, usuario_id=ctx.usuario["id"], items=items,
                          medio_pago=datos.get("medio_pago", "efectivo"), cliente_id=datos.get("cliente_id") or None,
                          recibido=int(datos.get("recibido") or 0))
    return {"venta": v}


@router.get("/api/clientes")
def api_clientes(q: str = "", ctx: Ctx = Depends(requiere("ventas"))):
    palabras = q.lower().split()
    if not palabras:
        return {"clientes": []}
    condiciones = " AND ".join(["(LOWER(nombre) LIKE ? OR documento LIKE ?)"] * len(palabras))
    params = [x for w in palabras for x in (f"%{w}%", f"{w}%")]
    filas = todos(ctx.con, f"SELECT id, documento, nombre, cupo_credito, plazo_dias FROM clientes WHERE activo = 1 AND {condiciones} "
                           f"ORDER BY nombre LIMIT 8", *params)
    for f in filas:
        f["disponible"] = max(0, f["cupo_credito"] - ventas.saldo_cliente(ctx.con, f["id"])) if f["cupo_credito"] else 0
    return {"clientes": filas}


@router.get("/ventas")
def lista(desde: str = "", hasta: str = "", medio: str = "", estado: str = "", q: str = "", pagina: int = 1,
          ctx: Ctx = Depends(requiere("ventas"))):
    sede = ctx.sede_filtro() if ctx.puede("reportes") else ctx.sede_id
    h = date.fromisoformat(hoy())
    desde = desde or h.isoformat()
    hasta = hasta or h.isoformat()
    condiciones, params = ["v.fecha >= ?", "v.fecha < date(?, '+1 day')"], [desde, hasta]
    if sede:
        condiciones.append("v.sede_id = ?")
        params.append(sede)
    if ctx.usuario["rol"] == "cajero":
        condiciones.append("v.usuario_id = ?")
        params.append(ctx.usuario["id"])
    if medio in ventas.MEDIOS:
        condiciones.append("v.medio_pago = ?")
        params.append(medio)
    if estado in ("pagada", "credito", "anulada"):
        condiciones.append("v.estado = ?")
        params.append(estado)
    if q.strip():
        condiciones.append("(v.numero LIKE ? OR LOWER(c.nombre) LIKE ? OR c.documento LIKE ?)")
        params += [f"%{q.strip()}%", f"%{q.strip().lower()}%", f"{q.strip()}%"]
    donde = " AND ".join(condiciones)
    base = f"FROM ventas v LEFT JOIN clientes c ON c.id = v.cliente_id JOIN sedes s ON s.id = v.sede_id LEFT JOIN usuarios u ON u.id = v.usuario_id WHERE {donde}"
    totales = uno(ctx.con, f"""SELECT COUNT(*) AS n, COALESCE(SUM(CASE WHEN v.estado <> 'anulada' THEN v.total END), 0) AS total,
        COALESCE(SUM(CASE WHEN v.estado <> 'anulada' THEN v.subtotal - v.costo_total END), 0) AS utilidad,
        SUM(v.estado = 'anulada') AS anuladas {base}""", *params)
    paginas = max(1, math.ceil(totales["n"] / POR_PAGINA))
    pagina = min(max(1, pagina), paginas)
    filas = todos(ctx.con, f"""SELECT v.*, c.nombre AS cliente, s.nombre AS sede, u.nombre AS vendedor,
        (SELECT COUNT(*) FROM venta_items WHERE venta_id = v.id) AS lineas {base} ORDER BY v.id DESC LIMIT ? OFFSET ?""",
                  *params, POR_PAGINA, (pagina - 1) * POR_PAGINA)
    return ctx.render("ventas/lista.html", filas=filas, totales=totales, desde=desde, hasta=hasta, medio=medio, estado=estado,
                      q=q, pagina=pagina, paginas=paginas, sede_lista=sede, medios=ventas.MEDIOS,
                      ayer=(h - timedelta(days=1)).isoformat(), semana=(h - timedelta(days=6)).isoformat(), hoy_=h.isoformat(),
                      mes=h.replace(day=1).isoformat())


@router.get("/ventas/{venta_id}")
def detalle(venta_id: int, ctx: Ctx = Depends(requiere("ventas"))):
    v = uno(ctx.con, """SELECT v.*, c.nombre AS cliente, c.documento, s.nombre AS sede, u.nombre AS vendedor, a.nombre AS anulo
                        FROM ventas v LEFT JOIN clientes c ON c.id = v.cliente_id JOIN sedes s ON s.id = v.sede_id
                        LEFT JOIN usuarios u ON u.id = v.usuario_id LEFT JOIN usuarios a ON a.id = v.anulada_por
                        WHERE v.id = ?""", venta_id)
    if not v or (ctx.usuario["rol"] == "cajero" and v["usuario_id"] != ctx.usuario["id"]):
        raise ErrorNegocio("La venta no existe.")
    items = todos(ctx.con, """SELECT vi.*, p.nombre, p.codigo, p.unidad FROM venta_items vi JOIN productos p ON p.id = vi.producto_id
                              WHERE vi.venta_id = ? ORDER BY vi.id""", venta_id)
    return ctx.render("ventas/detalle.html", v=v, items=items, saldo=ventas.saldo_venta(ctx.con, venta_id),
                      abonos=todos(ctx.con, "SELECT a.*, u.nombre AS usuario FROM abonos a LEFT JOIN usuarios u ON u.id = a.usuario_id WHERE venta_id = ? ORDER BY a.id", venta_id),
                      devoluciones=todos(ctx.con, "SELECT d.*, u.nombre AS usuario FROM devoluciones d LEFT JOIN usuarios u ON u.id = d.usuario_id WHERE venta_id = ? ORDER BY d.id", venta_id))


@router.post("/ventas/{venta_id}/anular")
def anular(venta_id: int, motivo: str = Form(...), ctx: Ctx = Depends(requiere("ventas.anular"))):
    with transaccion(ctx.con):
        r = ventas.anular(ctx.con, venta_id, ctx.usuario["id"], ctx.sede_id, motivo)
    extra = f" Devuelve ${r['reembolso']:,} al cliente.".replace(",", ".") if r["reembolso"] else ""
    return redirigir(ctx, f"/ventas/{venta_id}", f"Venta {r['numero']} anulada; la mercancía volvió al inventario.{extra}")


@router.post("/ventas/{venta_id}/devolver")
async def devolver(venta_id: int, request: Request, ctx: Ctx = Depends(requiere("ventas.anular"))):
    form = await request.form()
    cantidades = {int(k[5:]): numero(v) for k, v in form.items() if k.startswith("item_") and str(v).strip()}
    with transaccion(ctx.con):
        r = ventas.devolver(ctx.con, venta_id, cantidades, ctx.usuario["id"], ctx.sede_id, str(form.get("motivo", "")))
    return redirigir(ctx, f"/ventas/{venta_id}", f"Devolución registrada por ${r['total']:,}.".replace(",", "."))
