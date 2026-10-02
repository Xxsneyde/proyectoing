"""Indicadores del panel y reportes. sede_id=None significa todas las sedes.

La utilidad bruta es venta sin IVA menos costo: el IVA se le debe a la DIAN.
"""

from datetime import date, timedelta

from erp.db import ahora, hoy, todos, uno, valor


def _sede(sede_id, columna="sede_id"):
    return (f" AND {columna} = ?", (sede_id,)) if sede_id else ("", ())


def _rango(desde: str, hasta: str):
    """Fechas inclusivas 'YYYY-MM-DD' -> condición sobre una columna datetime."""
    fin = (date.fromisoformat(hasta) + timedelta(days=1)).isoformat()
    return desde, fin


def ventas_periodo(con, sede_id, desde: str, hasta: str, hasta_hora: str | None = None) -> dict:
    """Fechas inclusivas; con hasta_hora ('HH:MM:SS') el último día se corta a esa hora."""
    f, p = _sede(sede_id)
    d, h = _rango(desde, hasta)
    if hasta_hora:
        h = f"{hasta} {hasta_hora}"
    r = uno(con, f"""
        SELECT COUNT(*) AS n, COALESCE(SUM(total), 0) AS total, COALESCE(SUM(subtotal), 0) AS base,
               COALESCE(SUM(subtotal - costo_total), 0) AS utilidad
        FROM ventas WHERE estado <> 'anulada' AND fecha >= ? AND fecha < ? {f}""", d, h, *p)
    dev = valor(con, f"""SELECT SUM(d.total) FROM devoluciones d JOIN ventas v ON v.id = d.venta_id
                         WHERE d.fecha >= ? AND d.fecha < ? {f.replace('sede_id', 'v.sede_id')}""", d, h, *p, defecto=0)
    r["devoluciones"] = dev
    r["ticket"] = round(r["total"] / r["n"]) if r["n"] else 0
    r["margen"] = round(100 * r["utilidad"] / r["base"], 1) if r["base"] else None
    return r


def kpis(con, sede_id) -> dict:
    """Las comparaciones van hasta la misma hora: la mañana de hoy contra la mañana de hace una semana."""
    h = date.fromisoformat(hoy())
    hora = ahora()[11:19]
    inicio_mes = h.replace(day=1)
    mes_ant_fin = inicio_mes - timedelta(days=1)
    mes_ant_ini = mes_ant_fin.replace(day=1)
    mismo_dia_mes_ant = min(mes_ant_ini + timedelta(days=h.day - 1), mes_ant_fin)
    fs, ps = _sede(sede_id, "e.sede_id")

    k = {
        "hoy": ventas_periodo(con, sede_id, h.isoformat(), h.isoformat()),
        "semana_pasada": ventas_periodo(con, sede_id, (h - timedelta(days=7)).isoformat(), (h - timedelta(days=7)).isoformat(), hora),
        "semana_pasada_dia": ventas_periodo(con, sede_id, (h - timedelta(days=7)).isoformat(), (h - timedelta(days=7)).isoformat()),
        "mes": ventas_periodo(con, sede_id, inicio_mes.isoformat(), h.isoformat()),
        "mes_anterior": ventas_periodo(con, sede_id, mes_ant_ini.isoformat(), mismo_dia_mes_ant.isoformat(), hora),
    }
    k["inventario_valor"] = valor(con, f"""SELECT SUM(e.cantidad * p.costo) FROM existencias e
        JOIN productos p ON p.id = e.producto_id WHERE e.cantidad > 0 {fs}""", *ps, defecto=0)
    k["bajo_minimo"] = valor(con, f"""SELECT COUNT(*) FROM existencias e JOIN productos p ON p.id = e.producto_id
        AND p.activo = 1 WHERE e.minimo > 0 AND e.cantidad <= e.minimo {fs}""", *ps, defecto=0)
    k["agotados"] = valor(con, f"""SELECT COUNT(*) FROM existencias e JOIN productos p ON p.id = e.producto_id
        AND p.activo = 1 WHERE e.minimo > 0 AND e.cantidad <= 0 {fs}""", *ps, defecto=0)
    k["negativos"] = valor(con, f"SELECT COUNT(*) FROM existencias e WHERE e.cantidad < 0 {fs}", *ps, defecto=0)

    from erp.cartera import por_edades
    from erp.compras import cuentas_por_pagar
    cartera = por_edades(con, sede_id)
    k["cartera_total"], k["cartera_vencida"] = cartera["total"], cartera["vencido"]
    cxp = cuentas_por_pagar(con, sede_id)
    k["cxp_total"] = sum(c["total"] for c in cxp)
    k["cxp_7dias"] = sum(c["total"] for c in cxp if c["dias"] is not None and c["dias"] <= 7)
    k["cxp_vencidas"] = sum(1 for c in cxp if c["dias"] is not None and c["dias"] < 0)

    ft = " AND (origen_id = ? OR destino_id = ?)" if sede_id else ""
    k["traslados_transito"] = valor(con, f"SELECT COUNT(*) FROM traslados WHERE estado = 'en_transito' {ft}",
                                    *((sede_id, sede_id) if sede_id else ()), defecto=0)
    fc, pc = _sede(sede_id)
    k["conteos_abiertos"] = valor(con, f"SELECT COUNT(*) FROM conteos WHERE estado = 'abierto' {fc}", *pc, defecto=0)
    k["compras_por_recibir"] = valor(con, f"SELECT COUNT(*) FROM compras WHERE estado IN ('enviada', 'parcial') {fc}", *pc, defecto=0)

    precision = uno(con, f"""
        SELECT COUNT(*) AS n, SUM(CASE WHEN ABS(ci.contado - ci.sistema) < 1e-9 THEN 1 ELSE 0 END) AS exactos,
               COALESCE(SUM(CASE WHEN ci.contado < ci.sistema THEN (ci.sistema - ci.contado) * p.costo ELSE 0 END), 0)
               + COALESCE(SUM(ci.danados * p.costo), 0) AS merma
        FROM conteo_items ci JOIN conteos c ON c.id = ci.conteo_id JOIN productos p ON p.id = ci.producto_id
        WHERE c.estado = 'aprobado' AND c.aprobado >= datetime('now', 'localtime', '-30 days')
        {fc.replace('sede_id', 'c.sede_id')}""", *pc)
    k["exactitud"] = round(100 * precision["exactos"] / precision["n"], 1) if precision["n"] else None
    k["merma_30d"] = round(precision["merma"] or 0)

    k["cajas"] = todos(con, f"""SELECT c.id, c.abierta, c.base, u.nombre AS usuario, s.nombre AS sede
        FROM cajas c JOIN usuarios u ON u.id = c.usuario_id JOIN sedes s ON s.id = c.sede_id
        WHERE c.estado = 'abierta' {fc.replace('sede_id', 'c.sede_id')} ORDER BY c.abierta""", *pc)
    from erp.caja import resumen
    for c in k["cajas"]:
        c["esperado"] = resumen(con, c["id"])["esperado"]
    k["descuadres"] = todos(con, f"""SELECT c.id, c.cerrada, c.diferencia, u.nombre AS usuario, s.nombre AS sede
        FROM cajas c JOIN usuarios u ON u.id = c.usuario_id JOIN sedes s ON s.id = c.sede_id
        WHERE c.estado = 'cerrada' AND c.diferencia <> 0 AND c.cerrada >= datetime('now', 'localtime', '-7 days')
        {fc.replace('sede_id', 'c.sede_id')} ORDER BY c.cerrada DESC LIMIT 5""", *pc)
    return k


def ventas_diarias(con, sede_id, dias: int = 30) -> list[dict]:
    """Los últimos `dias` días hasta hoy."""
    h = date.fromisoformat(hoy())
    return ventas_por_dia(con, sede_id, (h - timedelta(days=dias - 1)).isoformat(), h.isoformat())


def ventas_por_dia(con, sede_id, desde: str, hasta: str) -> list[dict]:
    """Una fila por día del rango (incluye días sin ventas) con el total de cada sede."""
    d0, d1 = date.fromisoformat(desde), date.fromisoformat(hasta)
    f, p = _sede(sede_id, "v.sede_id")
    filas = todos(con, f"""
        SELECT date(v.fecha) AS dia, s.nombre AS sede, SUM(v.total) AS total, SUM(v.subtotal - v.costo_total) AS utilidad
        FROM ventas v JOIN sedes s ON s.id = v.sede_id
        WHERE v.estado <> 'anulada' AND v.fecha >= ? AND v.fecha < ? {f}
        GROUP BY dia, s.nombre ORDER BY dia""", *_rango(desde, hasta), *p)
    sedes = [r["nombre"] for r in todos(con, "SELECT nombre FROM sedes ORDER BY id") if any(x["sede"] == r["nombre"] for x in filas)]
    dias = (d1 - d0).days + 1
    por_dia = {(d0 + timedelta(days=i)).isoformat(): {s: 0 for s in sedes} for i in range(dias)}
    utilidad = {d: 0 for d in por_dia}
    for fila in filas:
        por_dia[fila["dia"]][fila["sede"]] = fila["total"]
        utilidad[fila["dia"]] += fila["utilidad"]
    return [{"dia": d, "sedes": v, "total": sum(v.values()), "utilidad": utilidad[d]} for d, v in por_dia.items()]


def top_productos(con, sede_id, desde: str, hasta: str, n: int = 10) -> list[dict]:
    f, p = _sede(sede_id, "v.sede_id")
    d, h = _rango(desde, hasta)
    return todos(con, f"""
        SELECT pr.id, pr.codigo, pr.nombre, pr.unidad, c.nombre AS categoria,
               SUM(vi.cantidad - vi.devuelto) AS cantidad, SUM(vi.total) AS total,
               ROUND(SUM(vi.total / (1 + vi.iva_pct / 100.0) - vi.costo_unitario * vi.cantidad)) AS utilidad
        FROM venta_items vi JOIN ventas v ON v.id = vi.venta_id AND v.estado <> 'anulada'
        JOIN productos pr ON pr.id = vi.producto_id LEFT JOIN categorias c ON c.id = pr.categoria_id
        WHERE v.fecha >= ? AND v.fecha < ? {f}
        GROUP BY pr.id ORDER BY total DESC LIMIT ?""", d, h, *p, n)


def ventas_por(con, dimension: str, sede_id, desde: str, hasta: str) -> list[dict]:
    """dimension: categoria | vendedor | medio | sede."""
    f, p = _sede(sede_id, "v.sede_id")
    d, h = _rango(desde, hasta)
    if dimension == "categoria":
        return todos(con, f"""
            SELECT COALESCE(c.nombre, 'Sin categoría') AS nombre, SUM(vi.total) AS total,
                   ROUND(SUM(vi.total / (1 + vi.iva_pct / 100.0) - vi.costo_unitario * vi.cantidad)) AS utilidad
            FROM venta_items vi JOIN ventas v ON v.id = vi.venta_id AND v.estado <> 'anulada'
            JOIN productos pr ON pr.id = vi.producto_id LEFT JOIN categorias c ON c.id = pr.categoria_id
            WHERE v.fecha >= ? AND v.fecha < ? {f} GROUP BY 1 ORDER BY total DESC""", d, h, *p)
    columna = {"vendedor": "u.nombre", "medio": "v.medio_pago", "sede": "s.nombre"}[dimension]
    return todos(con, f"""
        SELECT {columna} AS nombre, COUNT(*) AS n, SUM(v.total) AS total, SUM(v.subtotal - v.costo_total) AS utilidad
        FROM ventas v JOIN usuarios u ON u.id = v.usuario_id JOIN sedes s ON s.id = v.sede_id
        WHERE v.estado <> 'anulada' AND v.fecha >= ? AND v.fecha < ? {f}
        GROUP BY 1 ORDER BY total DESC""", d, h, *p)


def inventario_por_categoria(con, sede_id) -> list[dict]:
    f, p = _sede(sede_id, "e.sede_id")
    return todos(con, f"""
        SELECT COALESCE(c.nombre, 'Sin categoría') AS nombre, COUNT(DISTINCT pr.id) AS productos,
               SUM(CASE WHEN e.cantidad > 0 THEN e.cantidad * pr.costo ELSE 0 END) AS valor
        FROM existencias e JOIN productos pr ON pr.id = e.producto_id AND pr.activo = 1
        LEFT JOIN categorias c ON c.id = pr.categoria_id
        WHERE 1 = 1 {f} GROUP BY 1 ORDER BY valor DESC""", *p)


def sin_movimiento(con, sede_id, dias: int = 60) -> list[dict]:
    """Capital quieto: existencias sin ventas en los últimos `dias`."""
    f, p = _sede(sede_id, "e.sede_id")
    return todos(con, f"""
        SELECT pr.id, pr.codigo, pr.nombre, pr.unidad, s.nombre AS sede, e.cantidad,
               ROUND(e.cantidad * pr.costo) AS valor,
               (SELECT MAX(m.fecha) FROM movimientos m WHERE m.producto_id = pr.id AND m.sede_id = e.sede_id
                AND m.tipo = 'venta') AS ultima_venta
        FROM existencias e JOIN productos pr ON pr.id = e.producto_id AND pr.activo = 1
        JOIN sedes s ON s.id = e.sede_id
        WHERE e.cantidad > 0 {f} AND NOT EXISTS (
            SELECT 1 FROM movimientos m WHERE m.producto_id = pr.id AND m.sede_id = e.sede_id AND m.tipo = 'venta'
              AND m.fecha >= datetime('now', 'localtime', ?))
        ORDER BY valor DESC LIMIT 100""", *p, f"-{int(dias)} days")


def merma(con, sede_id, desde: str, hasta: str) -> list[dict]:
    """Faltantes de conteo, dañados y ajustes negativos en el periodo."""
    f, p = _sede(sede_id, "m.sede_id")
    d, h = _rango(desde, hasta)
    return todos(con, f"""
        SELECT m.fecha, m.tipo, pr.nombre, pr.unidad, s.nombre AS sede, m.cantidad,
               ROUND(m.cantidad * m.costo_unitario) AS valor, u.nombre AS usuario, m.nota, m.referencia
        FROM movimientos m JOIN productos pr ON pr.id = m.producto_id JOIN sedes s ON s.id = m.sede_id
        LEFT JOIN usuarios u ON u.id = m.usuario_id
        WHERE m.tipo IN ('conteo', 'danado', 'ajuste') AND m.cantidad < 0 AND m.fecha >= ? AND m.fecha < ? {f}
        ORDER BY valor LIMIT 300""", d, h, *p)


def compras_por_proveedor(con, sede_id, desde: str, hasta: str) -> list[dict]:
    f, p = _sede(sede_id, "c.sede_id")
    d, h = _rango(desde, hasta)
    return todos(con, f"""
        SELECT pr.nombre, COUNT(*) AS ordenes, SUM(c.total) AS total,
               SUM(CASE WHEN c.pagada IS NULL THEN c.total ELSE 0 END) AS pendiente
        FROM compras c JOIN proveedores pr ON pr.id = c.proveedor_id
        WHERE c.estado IN ('parcial', 'recibida') AND c.recibida >= ? AND c.recibida < ? {f}
        GROUP BY pr.id ORDER BY total DESC""", d, h, *p)


ACCIONES = {
    "venta.crear": "Venta", "venta.anular": "Venta anulada", "venta.devolucion": "Devolución",
    "caja.abrir": "Apertura de caja", "caja.cerrar": "Cierre de caja", "caja.ingreso": "Ingreso de caja",
    "caja.egreso": "Egreso de caja", "cartera.abono": "Abono de cliente", "compra.crear": "Orden de compra",
    "compra.enviar": "Orden enviada", "compra.recibir": "Mercancía recibida", "compra.pagar": "Pago a proveedor",
    "compra.anular": "Orden anulada", "traslado.crear": "Traslado creado", "traslado.enviar": "Traslado enviado",
    "traslado.recibir": "Traslado recibido", "traslado.anular": "Traslado anulado", "conteo.crear": "Conteo iniciado",
    "conteo.aprobar": "Conteo aprobado", "conteo.anular": "Conteo anulado", "inventario.ajuste": "Ajuste de inventario",
    "inventario.danado": "Producto dañado", "producto.crear": "Producto creado", "producto.editar": "Producto editado",
    "usuario.crear": "Usuario creado", "usuario.editar": "Usuario editado", "cliente.crear": "Cliente creado",
    "cliente.editar": "Cliente editado", "proveedor.crear": "Proveedor creado", "proveedor.editar": "Proveedor editado",
    "sede.crear": "Sede creada", "sede.editar": "Sede editada", "config.editar": "Configuración",
}


def actividad(con, sede_id, n: int = 15) -> list[dict]:
    f, p = _sede(sede_id, "a.sede_id")
    filas = todos(con, f"""
        SELECT a.fecha, a.accion, a.detalle, u.nombre AS usuario, s.nombre AS sede
        FROM auditoria a LEFT JOIN usuarios u ON u.id = a.usuario_id LEFT JOIN sedes s ON s.id = a.sede_id
        WHERE 1 = 1 {f} ORDER BY a.id DESC LIMIT ?""", *p, n)
    for fila in filas:
        fila["accion_texto"] = ACCIONES.get(fila["accion"], fila["accion"])
    return filas
