"""Reportes por periodo y sede, con exportación a CSV para Excel."""

import csv
import io
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from erp import cartera, graficos, reportes
from erp.db import hoy, todos
from erp.rutas.base import Ctx, requiere

router = APIRouter()
VISTAS = {"ventas": "Ventas", "productos": "Productos", "inventario": "Inventario", "merma": "Merma",
          "compras": "Compras"}
TIPOS_MERMA = {"conteo": "Faltante de conteo", "danado": "Dañado", "ajuste": "Ajuste manual"}


def _periodo(desde: str, hasta: str) -> tuple[str, str]:
    h = date.fromisoformat(hoy())
    try:
        d1 = date.fromisoformat(hasta) if hasta else h
        d0 = date.fromisoformat(desde) if desde else d1 - timedelta(days=29)
    except ValueError:
        d0, d1 = h - timedelta(days=29), h
    if d0 > d1:
        d0, d1 = d1, d0
    return d0.isoformat(), min(d1, d0 + timedelta(days=366)).isoformat()


def _presets() -> list[tuple[str, str, str]]:
    h = date.fromisoformat(hoy())
    inicio_mes = h.replace(day=1)
    fin_ant = inicio_mes - timedelta(days=1)
    return [("Hoy", h.isoformat(), h.isoformat()),
            ("7 días", (h - timedelta(days=6)).isoformat(), h.isoformat()),
            ("30 días", (h - timedelta(days=29)).isoformat(), h.isoformat()),
            ("Este mes", inicio_mes.isoformat(), h.isoformat()),
            ("Mes anterior", fin_ant.replace(day=1).isoformat(), fin_ant.isoformat()),
            ("90 días", (h - timedelta(days=89)).isoformat(), h.isoformat())]


def _datos(ctx: Ctx, vista: str, desde: str, hasta: str) -> dict:
    sede = ctx.sede_filtro()
    d: dict = {"vista": vista, "desde": desde, "hasta": hasta, "sede_rep": sede}
    if vista == "ventas":
        d["resumen"] = reportes.ventas_periodo(ctx.con, sede, desde, hasta)
        dias = reportes.ventas_por_dia(ctx.con, sede, desde, hasta)
        series = list(dias[0]["sedes"]) if dias else []
        d["dias"], d["series"] = dias, series
        d["grafico"] = graficos.columnas_apiladas(dias, series, "Ventas por día", ancho=1120) if len(dias) > 1 and series else None
        for dim in ("categoria", "vendedor", "medio", "sede"):
            d[dim] = reportes.ventas_por(ctx.con, dim, sede, desde, hasta)
    elif vista == "productos":
        d["filas"] = reportes.top_productos(ctx.con, sede, desde, hasta, 100)
    elif vista == "inventario":
        d["categorias"] = reportes.inventario_por_categoria(ctx.con, sede)
        d["quietos"] = reportes.sin_movimiento(ctx.con, sede, 60)
        d["total"] = sum(c["valor"] or 0 for c in d["categorias"])
    elif vista == "merma":
        d["filas"] = reportes.merma(ctx.con, sede, desde, hasta)
        d["por_tipo"] = {t: sum(f["valor"] for f in d["filas"] if f["tipo"] == t) for t in TIPOS_MERMA}
        d["total"] = sum(d["por_tipo"].values())
    elif vista == "compras":
        d["filas"] = reportes.compras_por_proveedor(ctx.con, sede, desde, hasta)
    return d


@router.get("/reportes")
def inicio(vista: str = "ventas", desde: str = "", hasta: str = "", ctx: Ctx = Depends(requiere("reportes"))):
    vista = vista if vista in VISTAS else "ventas"
    desde, hasta = _periodo(desde, hasta)
    return ctx.render("reportes/inicio.html", vistas=VISTAS, presets=_presets(), tipos_merma=TIPOS_MERMA,
                      sedes_lista=todos(ctx.con, "SELECT id, nombre FROM sedes WHERE activa = 1 ORDER BY id"),
                      **_datos(ctx, vista, desde, hasta))


def _csv(nombre: str, encabezado: list[str], filas: list[list]) -> Response:
    """CSV para Excel en español: separador punto y coma y BOM para que abra bien las tildes."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(encabezado)
    for f in filas:
        w.writerow([(int(x) if x.is_integer() else str(x).replace(".", ",")) if isinstance(x, float) else x for x in f])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}.csv"'})


@router.get("/reportes/{vista}.csv")
def exportar(vista: str, desde: str = "", hasta: str = "", ctx: Ctx = Depends(requiere("reportes"))):
    desde, hasta = _periodo(desde, hasta)
    sede = ctx.sede_filtro()
    sufijo = f"{desde}_{hasta}"
    if vista == "ventas":
        filas = todos(ctx.con, f"""
            SELECT v.numero, v.fecha, s.nombre AS sede, u.nombre AS vendedor, COALESCE(c.nombre, 'Consumidor final') AS cliente,
                   v.medio_pago, v.estado,
                   v.subtotal, v.iva, v.total, v.costo_total, v.subtotal - v.costo_total
            FROM ventas v JOIN sedes s ON s.id = v.sede_id LEFT JOIN usuarios u ON u.id = v.usuario_id
            LEFT JOIN clientes c ON c.id = v.cliente_id
            WHERE v.fecha >= ? AND v.fecha < date(?, '+1 day') {'AND v.sede_id = ?' if sede else ''} ORDER BY v.fecha""",
                      desde, hasta, *([sede] if sede else []))
        return _csv(f"ventas_{sufijo}", ["Número", "Fecha", "Sede", "Vendedor", "Cliente", "Medio de pago", "Estado",
                                         "Base sin IVA", "IVA", "Total", "Costo", "Utilidad bruta"], [list(f.values()) for f in filas])
    if vista == "productos":
        filas = reportes.top_productos(ctx.con, sede, desde, hasta, 10000)
        return _csv(f"productos_vendidos_{sufijo}", ["Código", "Producto", "Categoría", "Unidad", "Cantidad", "Total vendido", "Utilidad"],
                    [[f["codigo"], f["nombre"], f["categoria"], f["unidad"], f["cantidad"], f["total"], f["utilidad"]] for f in filas])
    if vista == "inventario":
        filas = todos(ctx.con, f"""
            SELECT p.codigo, p.nombre, c.nombre AS categoria, s.nombre AS sede, e.ubicacion, e.cantidad, p.unidad,
                   e.minimo, e.maximo, p.costo, ROUND(MAX(e.cantidad, 0) * p.costo) AS valor, e.ultimo_conteo
            FROM existencias e JOIN productos p ON p.id = e.producto_id AND p.activo = 1 JOIN sedes s ON s.id = e.sede_id
            LEFT JOIN categorias c ON c.id = p.categoria_id {'WHERE e.sede_id = ?' if sede else ''} ORDER BY s.id, p.nombre""",
                      *([sede] if sede else []))
        return _csv(f"inventario_{hoy()}", ["Código", "Producto", "Categoría", "Sede", "Ubicación", "Existencia", "Unidad", "Mínimo",
                                            "Máximo", "Costo promedio", "Valor", "Último conteo"], [list(f.values()) for f in filas])
    if vista == "merma":
        filas = reportes.merma(ctx.con, sede, desde, hasta)
        return _csv(f"merma_{sufijo}", ["Fecha", "Tipo", "Producto", "Sede", "Cantidad", "Unidad", "Valor", "Usuario", "Nota"],
                    [[f["fecha"], TIPOS_MERMA.get(f["tipo"], f["tipo"]), f["nombre"], f["sede"], f["cantidad"], f["unidad"],
                      f["valor"], f["usuario"], f["nota"]] for f in filas])
    if vista == "compras":
        filas = reportes.compras_por_proveedor(ctx.con, sede, desde, hasta)
        return _csv(f"compras_{sufijo}", ["Proveedor", "Órdenes", "Total", "Pendiente de pago"],
                    [[f["nombre"], f["ordenes"], f["total"], f["pendiente"]] for f in filas])
    if vista == "cartera":
        cuentas = cartera.cuentas_por_cobrar(ctx.con)
        return _csv(f"cartera_{hoy()}", ["Cliente", "Documento", "Teléfono", "Factura", "Sede", "Fecha", "Vence", "Días vencida", "Total", "Saldo"],
                    [[c["cliente"], c["documento"], c["telefono"], c["numero"], c["sede"], c["fecha"], c["vence"],
                      max(0, c["dias_vencido"]), c["total"], c["saldo"]] for c in cuentas])
    return Response("Reporte no encontrado", status_code=404)

