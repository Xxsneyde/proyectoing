"""Caja: apertura, movimientos, arqueo, cierre e historial."""

from fastapi import APIRouter, Depends, Form

from erp import caja as caja_mod
from erp.db import ErrorNegocio, todos, transaccion, uno
from erp.formato import pesos
from erp.rutas.base import Ctx, numero, redirigir, requiere

router = APIRouter()


@router.get("/caja")
def inicio(ctx: Ctx = Depends(requiere("caja"))):
    caja = caja_mod.caja_abierta(ctx.con, ctx.sede_id, ctx.usuario["id"])
    datos = {"caja": caja}
    if caja:
        datos["r"] = caja_mod.resumen(ctx.con, caja["id"])
        datos["movimientos"] = todos(ctx.con, "SELECT * FROM movimientos_caja WHERE caja_id = ? ORDER BY id DESC", caja["id"])
        datos["ventas"] = todos(ctx.con, """SELECT v.id, v.numero, v.fecha, v.total, v.medio_pago, v.estado, c.nombre AS cliente
                                            FROM ventas v LEFT JOIN clientes c ON c.id = v.cliente_id
                                            WHERE v.caja_id = ? ORDER BY v.id DESC LIMIT 15""", caja["id"])
    if ctx.puede("reportes"):
        filtro = "" if ctx.usuario["todas_las_sedes"] else "AND c.sede_id = ?"
        params = () if ctx.usuario["todas_las_sedes"] else (ctx.sede_id,)
        abiertas = todos(ctx.con, f"""SELECT c.*, u.nombre AS usuario, s.nombre AS sede FROM cajas c
                                      JOIN usuarios u ON u.id = c.usuario_id JOIN sedes s ON s.id = c.sede_id
                                      WHERE c.estado = 'abierta' {filtro} ORDER BY s.id""", *params)
        for a in abiertas:
            a["esperado"] = caja_mod.resumen(ctx.con, a["id"])["esperado"]
        datos["abiertas"] = abiertas
    return ctx.render("caja/inicio.html", **datos)


@router.post("/caja/abrir")
def abrir(base: str = Form("0"), ctx: Ctx = Depends(requiere("caja"))):
    with transaccion(ctx.con):
        caja_mod.abrir(ctx.con, ctx.sede_id, ctx.usuario["id"], round(numero(base)))
    return redirigir(ctx, "/caja", "Caja abierta. Ya puedes vender.")


@router.post("/caja/movimiento")
def movimiento(tipo: str = Form(...), valor: str = Form(...), concepto: str = Form(...), ctx: Ctx = Depends(requiere("caja"))):
    caja = caja_mod.exigir_caja(ctx.con, ctx.sede_id, ctx.usuario["id"])
    with transaccion(ctx.con):
        caja_mod.movimiento(ctx.con, caja["id"], tipo, round(numero(valor)), concepto, ctx.usuario["id"])
    return redirigir(ctx, "/caja", f"{'Ingreso' if tipo == 'ingreso' else 'Egreso'} registrado.")


@router.post("/caja/cerrar")
def cerrar(efectivo: str = Form(...), nota: str = Form(""), ctx: Ctx = Depends(requiere("caja"))):
    caja = caja_mod.exigir_caja(ctx.con, ctx.sede_id, ctx.usuario["id"])
    with transaccion(ctx.con):
        r = caja_mod.cerrar(ctx.con, caja["id"], round(numero(efectivo)), ctx.usuario["id"], nota)
    dif = r["diferencia"]
    texto = "Caja cerrada sin diferencias." if dif == 0 else f"Caja cerrada con {'sobrante' if dif > 0 else 'faltante'} de {pesos(abs(dif))}."
    return redirigir(ctx, f"/caja/{caja['id']}", texto)


@router.get("/caja/historial")
def historial(ctx: Ctx = Depends(requiere("caja"))):
    condiciones, params = ["c.estado = 'cerrada'"], []
    if ctx.usuario["rol"] == "cajero":
        condiciones.append("c.usuario_id = ?")
        params.append(ctx.usuario["id"])
    elif not ctx.usuario["todas_las_sedes"]:
        condiciones.append("c.sede_id = ?")
        params.append(ctx.sede_id)
    filas = todos(ctx.con, f"""SELECT c.*, u.nombre AS usuario, s.nombre AS sede FROM cajas c
                               JOIN usuarios u ON u.id = c.usuario_id JOIN sedes s ON s.id = c.sede_id
                               WHERE {' AND '.join(condiciones)} ORDER BY c.cerrada DESC LIMIT 120""", *params)
    por_usuario = {}
    for f in filas:
        u = por_usuario.setdefault(f["usuario"], {"usuario": f["usuario"], "cierres": 0, "descuadres": 0, "neto": 0})
        u["cierres"] += 1
        u["descuadres"] += 1 if f["diferencia"] else 0
        u["neto"] += f["diferencia"] or 0
    return ctx.render("caja/historial.html", filas=filas, por_usuario=sorted(por_usuario.values(), key=lambda u: u["neto"]))


@router.get("/caja/{caja_id}")
def detalle(caja_id: int, ctx: Ctx = Depends(requiere("caja"))):
    c = uno(ctx.con, """SELECT c.*, u.nombre AS usuario, s.nombre AS sede FROM cajas c JOIN usuarios u ON u.id = c.usuario_id
                        JOIN sedes s ON s.id = c.sede_id WHERE c.id = ?""", caja_id)
    if not c or (ctx.usuario["rol"] == "cajero" and c["usuario_id"] != ctx.usuario["id"]):
        raise ErrorNegocio("La caja no existe.")
    return ctx.render("caja/detalle.html", c=c, r=caja_mod.resumen(ctx.con, caja_id),
                      movimientos=todos(ctx.con, "SELECT * FROM movimientos_caja WHERE caja_id = ? ORDER BY id", caja_id))
