"""Compras a proveedores (pedido sugerido, envío, recepción, pago) y proveedores."""

import json

from fastapi import APIRouter, Body, Depends, Form, Request

from erp import compras
from erp.db import ErrorNegocio, auditar, todos, transaccion, uno, valor
from erp.rutas.base import Ctx, numero, redirigir, requiere

router = APIRouter()
VISTAS = {"": "Todas", "por_recibir": "Por recibir", "por_pagar": "Por pagar", "borradores": "Borradores"}


@router.get("/compras")
def lista(vista: str = "", ctx: Ctx = Depends(requiere("compras.ver"))):
    sede = ctx.sede_filtro() if ctx.usuario["todas_las_sedes"] else ctx.sede_id
    condiciones, params = [], []
    if sede:
        condiciones.append("c.sede_id = ?")
        params.append(sede)
    if vista == "por_recibir":
        condiciones.append("c.estado IN ('enviada', 'parcial')")
    elif vista == "por_pagar":
        condiciones.append("c.estado IN ('parcial', 'recibida') AND c.pagada IS NULL")
    elif vista == "borradores":
        condiciones.append("c.estado = 'borrador'")
    donde = ("WHERE " + " AND ".join(condiciones)) if condiciones else ""
    orden = "c.vence" if vista == "por_pagar" else "c.id DESC"
    filas = todos(ctx.con, f"""
        SELECT c.*, p.nombre AS proveedor, s.nombre AS sede, u.nombre AS usuario,
               CAST(julianday(c.vence) - julianday(date('now', 'localtime')) AS INTEGER) AS dias,
               (SELECT COUNT(*) FROM compra_items WHERE compra_id = c.id) AS lineas
        FROM compras c JOIN proveedores p ON p.id = c.proveedor_id JOIN sedes s ON s.id = c.sede_id
        LEFT JOIN usuarios u ON u.id = c.usuario_id {donde} ORDER BY {orden} LIMIT 150""", *params)
    cxp = compras.cuentas_por_pagar(ctx.con, sede)
    resumen = {
        "por_recibir": valor(ctx.con, f"SELECT COUNT(*) FROM compras WHERE estado IN ('enviada', 'parcial'){' AND sede_id = ?' if sede else ''}",
                             *([sede] if sede else []), defecto=0),
        "por_pagar": sum(c["total"] for c in cxp),
        "vencidas": sum(c["total"] for c in cxp if c["dias"] is not None and c["dias"] < 0),
        "semana": sum(c["total"] for c in cxp if c["dias"] is not None and 0 <= c["dias"] <= 7),
    }
    return ctx.render("compras/lista.html", filas=filas, vista=vista, vistas=VISTAS, resumen=resumen,
                      sede_lista=sede, sedes_todas=ctx.usuario["todas_las_sedes"])


@router.get("/compras/nueva")
def nueva(ctx: Ctx = Depends(requiere("compras.gestionar"))):
    sugerido = compras.sugerido_por_proveedor(ctx.con, ctx.sede_id)
    proveedores = todos(ctx.con, "SELECT id, nombre, plazo_dias FROM proveedores WHERE activo = 1 ORDER BY nombre")
    datos = {g["proveedor_id"]: [{"producto_id": p["id"], "nombre": p["nombre"], "codigo": p["codigo"], "unidad": p["unidad"],
                                  "cantidad": p["sugerido"], "costo": round(p["costo"]), "existencia": p["cantidad"],
                                  "minimo": p["minimo"], "maximo": p["maximo"]} for p in g["items"]]
             for g in sugerido if g["proveedor_id"]}
    return ctx.render("compras/nueva.html", sugerido=sugerido, proveedores=proveedores, sugerido_json=json.dumps(datos))


@router.post("/api/compras")
def api_crear(datos: dict = Body(...), ctx: Ctx = Depends(requiere("compras.gestionar"))):
    items = [{"producto_id": int(i["producto_id"]), "cantidad": float(i["cantidad"]), "costo_unitario": float(i["costo"])}
             for i in datos.get("items", []) if float(i.get("cantidad") or 0) > 0]
    with transaccion(ctx.con):
        cid = compras.crear(ctx.con, int(datos.get("proveedor_id") or 0), ctx.sede_id, items, ctx.usuario["id"],
                            str(datos.get("nota", "")))
        if datos.get("enviar"):
            compras.enviar(ctx.con, cid, ctx.usuario["id"])
    return {"id": cid}


def _compra(ctx: Ctx, compra_id: int) -> dict:
    c = uno(ctx.con, """SELECT c.*, p.nombre AS proveedor, p.nit, p.contacto, p.telefono, p.email, s.nombre AS sede, u.nombre AS usuario
                        FROM compras c JOIN proveedores p ON p.id = c.proveedor_id JOIN sedes s ON s.id = c.sede_id
                        LEFT JOIN usuarios u ON u.id = c.usuario_id WHERE c.id = ?""", compra_id)
    if not c or (not ctx.usuario["todas_las_sedes"] and c["sede_id"] != ctx.sede_id):
        raise ErrorNegocio("La orden de compra no existe en esta sede.")
    return c


@router.get("/compras/{compra_id}")
def detalle(compra_id: int, ctx: Ctx = Depends(requiere("compras.ver"))):
    c = _compra(ctx, compra_id)
    items = todos(ctx.con, """SELECT ci.*, p.nombre, p.codigo, p.unidad, ROUND(ci.cantidad * ci.costo_unitario) AS subtotal
                              FROM compra_items ci JOIN productos p ON p.id = ci.producto_id WHERE ci.compra_id = ? ORDER BY p.nombre""", compra_id)
    pedido = sum(i["subtotal"] for i in items)
    return ctx.render("compras/detalle.html", c=c, items=items, pedido=pedido,
                      pendientes=sum(1 for i in items if i["recibido"] < i["cantidad"] - 1e-9))


@router.post("/compras/{compra_id}/enviar")
def enviar(compra_id: int, ctx: Ctx = Depends(requiere("compras.gestionar"))):
    _compra(ctx, compra_id)
    with transaccion(ctx.con):
        compras.enviar(ctx.con, compra_id, ctx.usuario["id"])
    return redirigir(ctx, f"/compras/{compra_id}", "Orden marcada como enviada al proveedor.")


@router.post("/compras/{compra_id}/recibir")
async def recibir(compra_id: int, request: Request, ctx: Ctx = Depends(requiere("compras.recibir"))):
    _compra(ctx, compra_id)
    form = await request.form()
    recibidos = {int(k[9:]): numero(v) for k, v in form.items() if k.startswith("recibido_") and str(v).strip()}
    costos = {int(k[6:]): numero(v) for k, v in form.items() if k.startswith("costo_") and str(v).strip()}
    with transaccion(ctx.con):
        compras.recibir(ctx.con, compra_id, recibidos, ctx.usuario["id"], str(form.get("factura", "")), costos)
    estado = valor(ctx.con, "SELECT estado FROM compras WHERE id = ?", compra_id)
    texto = "Mercancía recibida y sumada al inventario." + (" Quedan productos pendientes." if estado == "parcial" else "")
    return redirigir(ctx, f"/compras/{compra_id}", texto)


@router.post("/compras/{compra_id}/pagar")
def pagar(compra_id: int, ctx: Ctx = Depends(requiere("compras.gestionar"))):
    _compra(ctx, compra_id)
    with transaccion(ctx.con):
        compras.pagar(ctx.con, compra_id, ctx.usuario["id"])
    return redirigir(ctx, f"/compras/{compra_id}", "Pago al proveedor registrado.")


@router.post("/compras/{compra_id}/anular")
def anular(compra_id: int, motivo: str = Form(...), ctx: Ctx = Depends(requiere("compras.gestionar"))):
    _compra(ctx, compra_id)
    with transaccion(ctx.con):
        compras.anular(ctx.con, compra_id, ctx.usuario["id"], motivo)
    return redirigir(ctx, f"/compras/{compra_id}", "Orden anulada.")


# --- Proveedores -----------------------------------------------------------

@router.get("/proveedores")
def proveedores(ctx: Ctx = Depends(requiere("compras.gestionar"))):
    filas = todos(ctx.con, """
        SELECT p.*,
               (SELECT COUNT(*) FROM productos WHERE proveedor_id = p.id AND activo = 1) AS productos,
               (SELECT COALESCE(SUM(total), 0) FROM compras WHERE proveedor_id = p.id AND estado IN ('parcial', 'recibida')
                  AND recibida >= datetime('now', 'localtime', '-90 days')) AS comprado_90,
               (SELECT COALESCE(SUM(total), 0) FROM compras WHERE proveedor_id = p.id AND estado IN ('parcial', 'recibida')
                  AND pagada IS NULL) AS por_pagar,
               (SELECT MIN(vence) FROM compras WHERE proveedor_id = p.id AND estado IN ('parcial', 'recibida') AND pagada IS NULL) AS proximo
        FROM proveedores p ORDER BY p.activo DESC, por_pagar DESC, p.nombre""")
    return ctx.render("compras/proveedores.html", filas=filas)


@router.get("/proveedores/nuevo")
def proveedor_nuevo(ctx: Ctx = Depends(requiere("compras.gestionar"))):
    return ctx.render("compras/proveedor_form.html", p=None)


def _validar_proveedor(con, nit: str, nombre: str, proveedor_id=None):
    if not nit.strip() or not nombre.strip():
        raise ErrorNegocio("El NIT y el nombre son obligatorios.")
    if uno(con, "SELECT id FROM proveedores WHERE nit = ? AND id IS NOT ?", nit.strip(), proveedor_id):
        raise ErrorNegocio(f"Ya existe un proveedor con el NIT {nit}.")


@router.post("/proveedores/nuevo")
def proveedor_crear(nit: str = Form(...), nombre: str = Form(...), contacto: str = Form(""), telefono: str = Form(""),
                    email: str = Form(""), plazo_dias: str = Form("30"), ctx: Ctx = Depends(requiere("compras.gestionar"))):
    _validar_proveedor(ctx.con, nit, nombre)
    with transaccion(ctx.con):
        pid = ctx.con.execute("INSERT INTO proveedores (nit, nombre, contacto, telefono, email, plazo_dias) VALUES (?, ?, ?, ?, ?, ?)",
                              (nit.strip(), nombre.strip(), contacto.strip(), telefono.strip(), email.strip(),
                               int(numero(plazo_dias, 30)))).lastrowid
        auditar(ctx.con, ctx.usuario["id"], ctx.sede_id, "proveedor.crear", nombre.strip())
    return redirigir(ctx, f"/proveedores/{pid}", "Proveedor creado.")


@router.get("/proveedores/{proveedor_id}")
def proveedor(proveedor_id: int, ctx: Ctx = Depends(requiere("compras.gestionar"))):
    p = uno(ctx.con, "SELECT * FROM proveedores WHERE id = ?", proveedor_id)
    if not p:
        raise ErrorNegocio("El proveedor no existe.")
    ordenes = todos(ctx.con, """SELECT c.*, s.nombre AS sede FROM compras c JOIN sedes s ON s.id = c.sede_id
                                WHERE c.proveedor_id = ? ORDER BY c.id DESC LIMIT 30""", proveedor_id)
    productos = todos(ctx.con, "SELECT id, nombre, codigo, costo FROM productos WHERE proveedor_id = ? AND activo = 1 ORDER BY nombre", proveedor_id)
    return ctx.render("compras/proveedor.html", p=p, ordenes=ordenes, productos=productos)


@router.post("/proveedores/{proveedor_id}/editar")
def proveedor_editar(proveedor_id: int, nit: str = Form(...), nombre: str = Form(...), contacto: str = Form(""),
                     telefono: str = Form(""), email: str = Form(""), plazo_dias: str = Form("30"), activo: str = Form(""),
                     ctx: Ctx = Depends(requiere("compras.gestionar"))):
    _validar_proveedor(ctx.con, nit, nombre, proveedor_id)
    with transaccion(ctx.con):
        ctx.con.execute("UPDATE proveedores SET nit = ?, nombre = ?, contacto = ?, telefono = ?, email = ?, plazo_dias = ?, activo = ? "
                        "WHERE id = ?", (nit.strip(), nombre.strip(), contacto.strip(), telefono.strip(), email.strip(),
                                         int(numero(plazo_dias, 30)), 1 if activo else 0, proveedor_id))
        auditar(ctx.con, ctx.usuario["id"], ctx.sede_id, "proveedor.editar", nombre.strip())
    return redirigir(ctx, f"/proveedores/{proveedor_id}", "Proveedor actualizado.")
