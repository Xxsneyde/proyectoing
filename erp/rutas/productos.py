"""Catálogo de productos: listado, ficha con existencias y kárdex, alta y edición."""

import math

from fastapi import APIRouter, Depends, Form

from erp import inventario, productos
from erp.db import ErrorNegocio, ahora, auditar, todos, transaccion, uno, valor
from erp.formato import pesos
from erp.productos import UNIDADES
from erp.rutas.base import Ctx, numero, redirigir, requiere

router = APIRouter()
POR_PAGINA = 50


@router.get("/productos")
def lista(q: str = "", categoria: int | None = None, filtro: str = "", pagina: int = 1,
          ctx: Ctx = Depends(requiere("productos.ver"))):
    condiciones, params = ["p.activo = ?"], [0 if filtro == "inactivos" else 1]
    for palabra in q.lower().split():
        condiciones.append("(LOWER(p.nombre) LIKE ? OR p.codigo LIKE ?)")
        params += [f"%{palabra}%", f"{palabra}%"]
    if categoria:
        condiciones.append("p.categoria_id = ?")
        params.append(categoria)
    donde = " AND ".join(condiciones)
    total = valor(ctx.con, f"SELECT COUNT(*) FROM productos p WHERE {donde}", *params, defecto=0)
    paginas = max(1, math.ceil(total / POR_PAGINA))
    pagina = min(max(1, pagina), paginas)
    productos = todos(ctx.con, f"""
        SELECT p.*, c.nombre AS categoria, COALESCE(e.cantidad, 0) AS existencia, COALESCE(e.minimo, 0) AS minimo,
               COALESCE(e.ubicacion, '') AS ubicacion,
               (SELECT SUM(cantidad) FROM existencias WHERE producto_id = p.id) AS total_sedes
        FROM productos p LEFT JOIN categorias c ON c.id = p.categoria_id
        LEFT JOIN existencias e ON e.producto_id = p.id AND e.sede_id = ?
        WHERE {donde} ORDER BY p.nombre LIMIT ? OFFSET ?""", ctx.sede_id, *params, POR_PAGINA, (pagina - 1) * POR_PAGINA)
    datos = {"productos": productos, "q": q, "categoria": categoria, "filtro": filtro, "pagina": pagina,
             "paginas": paginas, "total": total,
             "categorias": todos(ctx.con, "SELECT * FROM categorias ORDER BY nombre")}
    if ctx.es_htmx and ctx.request.headers.get("HX-Target") == "tabla":
        return ctx.render("productos/_tabla.html", **datos)
    return ctx.render("productos/lista.html", **datos)


def _form(ctx: Ctx, producto: dict | None):
    return ctx.render("productos/form.html", producto=producto, unidades=UNIDADES,
                      categorias=todos(ctx.con, "SELECT * FROM categorias ORDER BY nombre"),
                      proveedores=todos(ctx.con, "SELECT id, nombre FROM proveedores WHERE activo = 1 ORDER BY nombre"))


@router.get("/productos/nuevo")
def nuevo(codigo: str = "", ctx: Ctx = Depends(requiere("productos.editar"))):
    return _form(ctx, {"codigo": codigo, "iva": 19, "unidad": "unidades"} if codigo else None)


@router.post("/productos/nuevo")
def crear(codigo: str = Form(...), nombre: str = Form(...), categoria_id: str = Form(""), marca: str = Form(""),
          unidad: str = Form("unidades"), precio: str = Form(...), costo: str = Form("0"), iva: int = Form(19),
          proveedor_id: str = Form(""), minimo: str = Form("0"), maximo: str = Form("0"), ubicacion: str = Form(""),
          inicial: str = Form("0"), ctx: Ctx = Depends(requiere("productos.editar"))):
    with transaccion(ctx.con):
        pid = productos.crear(
            ctx.con, codigo=codigo, nombre=nombre, precio=numero(precio), costo=numero(costo), iva=iva, unidad=unidad,
            categoria_id=int(categoria_id) if categoria_id else None, marca=marca,
            proveedor_id=int(proveedor_id) if proveedor_id else None, minimo=numero(minimo), maximo=numero(maximo),
            ubicacion=ubicacion, inicial=numero(inicial), sede_id=ctx.sede_id, usuario_id=ctx.usuario["id"])
    return redirigir(ctx, f"/productos/{pid}", "Producto creado.")


@router.get("/productos/{producto_id}")
def detalle(producto_id: int, sede: int | None = None, ctx: Ctx = Depends(requiere("productos.ver"))):
    p = uno(ctx.con, """SELECT p.*, c.nombre AS categoria, pr.nombre AS proveedor FROM productos p
                        LEFT JOIN categorias c ON c.id = p.categoria_id LEFT JOIN proveedores pr ON pr.id = p.proveedor_id
                        WHERE p.id = ?""", producto_id)
    if not p:
        raise ErrorNegocio("El producto no existe.")
    sede_kardex = sede or ctx.sede_id
    kardex = todos(ctx.con, """
        SELECT m.*, u.nombre AS usuario FROM movimientos m LEFT JOIN usuarios u ON u.id = m.usuario_id
        WHERE m.producto_id = ? AND m.sede_id = ? ORDER BY m.id DESC LIMIT 60""", producto_id, sede_kardex)
    ventas_30 = uno(ctx.con, """
        SELECT COALESCE(SUM(-cantidad), 0) AS cantidad, COUNT(*) AS n FROM movimientos
        WHERE producto_id = ? AND tipo = 'venta' AND fecha >= datetime('now', 'localtime', '-30 days')""", producto_id)
    margen = round(100 * (p["precio"] / (1 + p["iva"] / 100) - p["costo"]) / (p["precio"] / (1 + p["iva"] / 100)), 1) if p["precio"] else None
    return ctx.render("productos/detalle.html", p=p, stock=inventario.stock_por_sede(ctx.con, producto_id),
                      kardex=kardex, sede_kardex=sede_kardex, ventas_30=ventas_30, margen=margen,
                      sedes_lista=todos(ctx.con, "SELECT id, nombre FROM sedes WHERE activa = 1 ORDER BY id"))


@router.get("/productos/{producto_id}/editar")
def editar_form(producto_id: int, ctx: Ctx = Depends(requiere("productos.editar"))):
    return _form(ctx, uno(ctx.con, "SELECT * FROM productos WHERE id = ?", producto_id))


@router.post("/productos/{producto_id}/editar")
def editar(producto_id: int, codigo: str = Form(...), nombre: str = Form(...), categoria_id: str = Form(""),
           marca: str = Form(""), unidad: str = Form("unidades"), precio: str = Form(...), costo: str = Form("0"),
           iva: int = Form(19), proveedor_id: str = Form(""), activo: str = Form(""),
           ctx: Ctx = Depends(requiere("productos.editar"))):
    antes = uno(ctx.con, "SELECT * FROM productos WHERE id = ?", producto_id)
    if not antes:
        raise ErrorNegocio("El producto no existe.")
    precio_n = round(numero(precio))
    productos.validar(ctx.con, codigo, nombre, unidad, precio_n, iva, producto_id)
    with transaccion(ctx.con):
        ctx.con.execute("""UPDATE productos SET codigo = ?, nombre = ?, categoria_id = ?, marca = ?, unidad = ?, precio = ?,
                           costo = ?, iva = ?, proveedor_id = ?, activo = ?, actualizado = ? WHERE id = ?""",
                        (codigo.strip(), nombre.strip(), int(categoria_id) if categoria_id else None, marca.strip(), unidad,
                         precio_n, numero(costo), iva, int(proveedor_id) if proveedor_id else None, 1 if activo else 0,
                         ahora(), producto_id))
        cambios = []
        if antes["precio"] != precio_n:
            cambios.append(f"precio {pesos(antes['precio'])} → {pesos(precio_n)}")
        if antes["activo"] and not activo:
            cambios.append("inactivado")
        auditar(ctx.con, ctx.usuario["id"], ctx.sede_id, "producto.editar", f"{nombre.strip()}: {', '.join(cambios) or 'datos actualizados'}")
    return redirigir(ctx, f"/productos/{producto_id}", "Cambios guardados.")


@router.post("/productos/{producto_id}/existencia")
def guardar_existencia(producto_id: int, sede_id: int = Form(...), minimo: str = Form("0"), maximo: str = Form("0"),
                       ubicacion: str = Form(""), ctx: Ctx = Depends(requiere("inventario.ajustar"))):
    if not ctx.usuario["todas_las_sedes"] and sede_id != ctx.usuario["sede_fija"]:
        raise ErrorNegocio("Solo puedes cambiar los datos de tu sede.")
    with transaccion(ctx.con):
        productos.guardar_existencia(ctx.con, producto_id, sede_id, minimo=numero(minimo), maximo=numero(maximo),
                                     ubicacion=ubicacion)
    return redirigir(ctx, f"/productos/{producto_id}", "Mínimo, máximo y ubicación guardados.")


@router.post("/productos/{producto_id}/ajuste")
def ajuste(producto_id: int, cantidad: str = Form(...), motivo: str = Form(...),
           ctx: Ctx = Depends(requiere("inventario.ajustar"))):
    with transaccion(ctx.con):
        saldo = inventario.ajustar(ctx.con, producto_id, ctx.sede_id, numero(cantidad), motivo, ctx.usuario["id"])
    return redirigir(ctx, f"/productos/{producto_id}", f"Existencia ajustada a {saldo:g}.".replace(".", ","))


@router.post("/productos/{producto_id}/danado")
def danado(producto_id: int, cantidad: str = Form(...), motivo: str = Form(""),
           ctx: Ctx = Depends(requiere("inventario.ver"))):
    if ctx.usuario["rol"] == "cajero":
        raise ErrorNegocio("Los dañados los registra bodega o la administración.")
    with transaccion(ctx.con):
        inventario.registrar_danado(ctx.con, producto_id, ctx.sede_id, numero(cantidad), motivo.strip() or "Dañado", ctx.usuario["id"])
    return redirigir(ctx, f"/productos/{producto_id}", "Dañados registrados y retirados del inventario.")
