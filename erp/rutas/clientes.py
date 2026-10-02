"""Clientes, cupos de crédito, cartera por edades y abonos."""

from fastapi import APIRouter, Depends, Form

from erp import cartera
from erp.db import ErrorNegocio, auditar, todos, transaccion, uno
from erp.formato import pesos
from erp.rutas.base import Ctx, numero, redirigir, requiere

router = APIRouter()


def _saldos_por_cliente(con) -> dict:
    saldos: dict = {}
    for c in cartera.cuentas_por_cobrar(con):
        s = saldos.setdefault(c["cliente_id"], {"saldo": 0, "vencido": 0, "facturas": 0, "max_dias": 0})
        s["saldo"] += c["saldo"]
        s["facturas"] += 1
        if c["dias_vencido"] > 0:
            s["vencido"] += c["saldo"]
            s["max_dias"] = max(s["max_dias"], c["dias_vencido"])
    return saldos


@router.get("/clientes")
def lista(q: str = "", vista: str = "clientes", ctx: Ctx = Depends(requiere("clientes"))):
    if vista == "cartera":
        edades = cartera.por_edades(ctx.con)
        por_cliente: dict = {}
        for c in edades["cuentas"]:
            fila = por_cliente.setdefault(c["cliente_id"], {"id": c["cliente_id"], "nombre": c["cliente"], "telefono": c["telefono"],
                                                            "total": 0, "vencido": 0, "facturas": 0, **{k: 0 for k, _ in cartera.RANGOS}})
            fila[c["rango"]] += c["saldo"]
            fila["total"] += c["saldo"]
            fila["facturas"] += 1
            fila["vencido"] += c["saldo"] if c["rango"] != "al_dia" else 0
        clientes = sorted(por_cliente.values(), key=lambda f: (-f["mas_90"], -f["61_90"], -f["vencido"], -f["total"]))
        return ctx.render("clientes/cartera.html", e=edades, clientes=clientes, rangos=cartera.RANGOS, vista=vista)
    condiciones, params = ["activo = 1"], []
    for palabra in q.lower().split():
        condiciones.append("(LOWER(nombre) LIKE ? OR documento LIKE ? OR telefono LIKE ?)")
        params += [f"%{palabra}%", f"{palabra}%", f"%{palabra}%"]
    clientes = todos(ctx.con, f"SELECT * FROM clientes WHERE {' AND '.join(condiciones)} ORDER BY nombre LIMIT 200", *params)
    saldos = _saldos_por_cliente(ctx.con)
    for c in clientes:
        c.update(saldos.get(c["id"], {"saldo": 0, "vencido": 0, "facturas": 0, "max_dias": 0}))
    clientes.sort(key=lambda c: (-c["vencido"], -c["saldo"], c["nombre"]))
    return ctx.render("clientes/lista.html", clientes=clientes, q=q, vista=vista)


def _validar(con, documento: str, nombre: str, cliente_id=None):
    if not documento.strip() or not nombre.strip():
        raise ErrorNegocio("El documento y el nombre son obligatorios.")
    otro = uno(con, "SELECT nombre FROM clientes WHERE documento = ? AND id IS NOT ?", documento.strip(), cliente_id)
    if otro:
        raise ErrorNegocio(f"El documento {documento} ya es de {otro['nombre']}.")


@router.get("/clientes/nuevo")
def nuevo(ctx: Ctx = Depends(requiere("clientes"))):
    return ctx.render("clientes/form.html", c=None)


@router.post("/clientes/nuevo")
def crear(documento: str = Form(...), nombre: str = Form(...), telefono: str = Form(""), email: str = Form(""),
          direccion: str = Form(""), cupo_credito: str = Form("0"), plazo_dias: str = Form("30"),
          ctx: Ctx = Depends(requiere("clientes"))):
    _validar(ctx.con, documento, nombre)
    gestion = ctx.puede("panel")
    cupo = round(numero(cupo_credito)) if gestion else 0
    with transaccion(ctx.con):
        cid = ctx.con.execute(
            "INSERT INTO clientes (documento, nombre, telefono, email, direccion, cupo_credito, plazo_dias) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (documento.strip(), nombre.strip(), telefono.strip(), email.strip(), direccion.strip(), cupo,
             int(numero(plazo_dias, 30)) if gestion else 30)).lastrowid
        auditar(ctx.con, ctx.usuario["id"], ctx.sede_id, "cliente.crear", f"{nombre.strip()} ({documento.strip()}), cupo {pesos(cupo)}")
    return redirigir(ctx, f"/clientes/{cid}", "Cliente creado.")


@router.get("/clientes/{cliente_id}")
def detalle(cliente_id: int, ctx: Ctx = Depends(requiere("clientes"))):
    c = uno(ctx.con, "SELECT * FROM clientes WHERE id = ?", cliente_id)
    if not c:
        raise ErrorNegocio("El cliente no existe.")
    cuentas = cartera.cuentas_por_cobrar(ctx.con, cliente_id=cliente_id)
    saldo = sum(x["saldo"] for x in cuentas)
    compras = todos(ctx.con, """SELECT v.id, v.numero, v.fecha, v.total, v.medio_pago, v.estado, s.nombre AS sede
                                FROM ventas v JOIN sedes s ON s.id = v.sede_id WHERE v.cliente_id = ? ORDER BY v.id DESC LIMIT 25""", cliente_id)
    abonos = todos(ctx.con, """SELECT a.*, v.numero, u.nombre AS usuario FROM abonos a JOIN ventas v ON v.id = a.venta_id
                               LEFT JOIN usuarios u ON u.id = a.usuario_id WHERE v.cliente_id = ? ORDER BY a.id DESC LIMIT 25""", cliente_id)
    total_comprado = sum(x["total"] for x in compras if x["estado"] != "anulada")
    return ctx.render("clientes/detalle.html", c=c, cuentas=cuentas, saldo=saldo, compras=compras, abonos=abonos,
                      disponible=max(0, c["cupo_credito"] - saldo), total_comprado=total_comprado,
                      vencido=sum(x["saldo"] for x in cuentas if x["dias_vencido"] > 0))


@router.get("/clientes/{cliente_id}/editar")
def editar_form(cliente_id: int, ctx: Ctx = Depends(requiere("clientes"))):
    return ctx.render("clientes/form.html", c=uno(ctx.con, "SELECT * FROM clientes WHERE id = ?", cliente_id))


@router.post("/clientes/{cliente_id}/editar")
def editar(cliente_id: int, documento: str = Form(...), nombre: str = Form(...), telefono: str = Form(""), email: str = Form(""),
           direccion: str = Form(""), cupo_credito: str = Form("0"), plazo_dias: str = Form("30"), activo: str = Form(""),
           ctx: Ctx = Depends(requiere("clientes"))):
    antes = uno(ctx.con, "SELECT * FROM clientes WHERE id = ?", cliente_id)
    if not antes:
        raise ErrorNegocio("El cliente no existe.")
    _validar(ctx.con, documento, nombre, cliente_id)
    gestion = ctx.puede("panel")
    cupo = round(numero(cupo_credito)) if gestion else antes["cupo_credito"]
    plazo = int(numero(plazo_dias, 30)) if gestion else antes["plazo_dias"]
    with transaccion(ctx.con):
        ctx.con.execute("UPDATE clientes SET documento = ?, nombre = ?, telefono = ?, email = ?, direccion = ?, cupo_credito = ?, "
                        "plazo_dias = ?, activo = ? WHERE id = ?",
                        (documento.strip(), nombre.strip(), telefono.strip(), email.strip(), direccion.strip(), cupo, plazo,
                         1 if activo or not gestion else 0, cliente_id))
        cambio = f"cupo {pesos(antes['cupo_credito'])} → {pesos(cupo)}" if cupo != antes["cupo_credito"] else "datos actualizados"
        auditar(ctx.con, ctx.usuario["id"], ctx.sede_id, "cliente.editar", f"{nombre.strip()}: {cambio}")
    return redirigir(ctx, f"/clientes/{cliente_id}", "Cliente actualizado.")


@router.post("/cartera/{venta_id}/abono")
def abonar(venta_id: int, valor: str = Form(...), medio: str = Form("efectivo"), ctx: Ctx = Depends(requiere("clientes"))):
    v = uno(ctx.con, "SELECT cliente_id, numero FROM ventas WHERE id = ?", venta_id)
    if not v:
        raise ErrorNegocio("La factura no existe.")
    with transaccion(ctx.con):
        saldo = cartera.abonar(ctx.con, venta_id, round(numero(valor)), medio, ctx.usuario["id"], ctx.sede_id)
    texto = f"Abono registrado a {v['numero']}." + (" Factura pagada por completo." if saldo == 0 else f" Queda {pesos(saldo)}.")
    return redirigir(ctx, f"/clientes/{v['cliente_id']}", texto)
