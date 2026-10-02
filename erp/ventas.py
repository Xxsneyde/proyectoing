"""Ventas de mostrador: registrar, anular y devolver."""

from datetime import datetime, timedelta

from erp import caja as caja_mod
from erp.db import ErrorNegocio, ahora, auditar, consecutivo, todos, uno, valor
from erp.formato import cant as fmt_cant, pesos
from erp.inventario import mover

MEDIOS = ("efectivo", "tarjeta", "transferencia", "credito")


def calcular_linea(precio: int, cantidad: float, descuento: int, iva_pct: int) -> dict:
    """El precio incluye IVA; se separa la base para la factura."""
    bruto = round(precio * cantidad)
    if descuento < 0 or descuento > bruto:
        raise ErrorNegocio("El descuento no puede ser mayor que el valor de la línea.")
    total = bruto - descuento
    base = round(total / (1 + iva_pct / 100))
    return {"bruto": bruto, "total": total, "base": base, "iva": total - base}


def saldo_venta(con, venta_id: int) -> int:
    v = uno(con, "SELECT total, estado FROM ventas WHERE id = ?", venta_id)
    if not v or v["estado"] == "anulada":
        return 0
    abonado = valor(con, "SELECT SUM(valor) FROM abonos WHERE venta_id = ?", venta_id, defecto=0)
    devuelto = valor(con, "SELECT SUM(total) FROM devoluciones WHERE venta_id = ?", venta_id, defecto=0)
    return max(0, v["total"] - abonado - devuelto)


def saldo_cliente(con, cliente_id: int) -> int:
    ids = [r[0] for r in con.execute(
        "SELECT id FROM ventas WHERE cliente_id = ? AND medio_pago = 'credito' AND estado <> 'anulada'", (cliente_id,))]
    return sum(saldo_venta(con, i) for i in ids)


def vender(con, *, sede_id: int, usuario_id: int, items: list[dict], medio_pago: str,
           cliente_id: int | None = None, recibido: int = 0, fecha: str | None = None) -> dict:
    """items: [{producto_id, cantidad, descuento?}]. Debe correr en una transacción."""
    if medio_pago not in MEDIOS:
        raise ErrorNegocio("Medio de pago inválido.")
    if not items:
        raise ErrorNegocio("La venta no tiene productos.")
    caja = caja_mod.exigir_caja(con, sede_id, usuario_id)
    fecha = fecha or ahora()

    lineas, total, base, iva, costo, descuento = [], 0, 0, 0, 0.0, 0
    for it in items:
        cantidad = float(it.get("cantidad") or 0)
        if cantidad <= 0:
            raise ErrorNegocio("Las cantidades deben ser mayores que cero.")
        p = uno(con, "SELECT * FROM productos WHERE id = ? AND activo = 1", int(it["producto_id"]))
        if not p:
            raise ErrorNegocio("Uno de los productos no existe o está inactivo.")
        desc = int(it.get("descuento") or 0)
        c = calcular_linea(p["precio"], cantidad, desc, p["iva"])
        lineas.append((p, cantidad, desc, c))
        total += c["total"]
        base += c["base"]
        iva += c["iva"]
        descuento += desc
        costo += p["costo"] * cantidad

    vence = None
    if medio_pago == "credito":
        if not cliente_id:
            raise ErrorNegocio("Para vender a crédito selecciona el cliente.")
        cli = uno(con, "SELECT * FROM clientes WHERE id = ? AND activo = 1", cliente_id)
        if not cli:
            raise ErrorNegocio("El cliente no existe.")
        disponible = cli["cupo_credito"] - saldo_cliente(con, cliente_id)
        if total > disponible:
            raise ErrorNegocio(f"El cliente no tiene cupo suficiente: disponible {pesos(max(disponible, 0))}.")
        vence = (datetime.strptime(fecha[:10], "%Y-%m-%d") + timedelta(days=cli["plazo_dias"])).strftime("%Y-%m-%d")
    if medio_pago == "efectivo":
        recibido = int(recibido or total)
        if recibido < total:
            raise ErrorNegocio("El efectivo recibido es menor que el total.")
    else:
        recibido = 0

    numero = consecutivo(con, f"V{sede_id}")
    cur = con.execute(
        "INSERT INTO ventas (numero, sede_id, fecha, usuario_id, cliente_id, caja_id, subtotal, descuento, iva, "
        "total, costo_total, medio_pago, recibido, estado, vence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (numero, sede_id, fecha, usuario_id, cliente_id, caja["id"], base, descuento, iva, total, round(costo),
         medio_pago, recibido, "credito" if medio_pago == "credito" else "pagada", vence),
    )
    venta_id = cur.lastrowid
    for p, cantidad, desc, c in lineas:
        con.execute(
            "INSERT INTO venta_items (venta_id, producto_id, cantidad, precio_unitario, descuento, iva_pct, "
            "costo_unitario, total) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (venta_id, p["id"], cantidad, p["precio"], desc, p["iva"], p["costo"], c["total"]),
        )
        mover(con, p["id"], sede_id, "venta", -cantidad, usuario_id=usuario_id,
              referencia=f"venta:{venta_id}", costo_unitario=p["costo"], fecha=fecha)
    auditar(con, usuario_id, sede_id, "venta.crear",
            f"{numero} por {pesos(total)} ({medio_pago})")
    return {"id": venta_id, "numero": numero, "total": total, "cambio": recibido - total if medio_pago == "efectivo" else 0}


def anular(con, venta_id: int, usuario_id: int, sede_id: int, motivo: str) -> dict:
    """Devuelve al inventario lo que no se había devuelto y reintegra el dinero."""
    v = uno(con, "SELECT * FROM ventas WHERE id = ?", venta_id)
    if not v:
        raise ErrorNegocio("La venta no existe.")
    if v["estado"] == "anulada":
        raise ErrorNegocio("La venta ya está anulada.")
    if not motivo.strip():
        raise ErrorNegocio("Escribe el motivo de la anulación.")
    if valor(con, "SELECT COUNT(*) FROM abonos WHERE venta_id = ?", venta_id, defecto=0):
        raise ErrorNegocio("La venta tiene abonos registrados; haz una devolución en su lugar.")
    reembolso = v["total"] - valor(con, "SELECT SUM(total) FROM devoluciones WHERE venta_id = ?", venta_id, defecto=0)
    if v["medio_pago"] == "efectivo" and reembolso > 0:
        caja = caja_mod.exigir_caja(con, sede_id, usuario_id)
        caja_mod.movimiento(con, caja["id"], "egreso", reembolso, f"Anulación {v['numero']}", usuario_id)
    for it in todos(con, "SELECT * FROM venta_items WHERE venta_id = ?", venta_id):
        pendiente = it["cantidad"] - it["devuelto"]
        if pendiente > 0:
            mover(con, it["producto_id"], v["sede_id"], "anulacion_venta", pendiente, usuario_id=usuario_id,
                  referencia=f"venta:{venta_id}", costo_unitario=it["costo_unitario"])
    con.execute("UPDATE ventas SET estado = 'anulada', anulada_motivo = ?, anulada_por = ? WHERE id = ?",
                (motivo.strip(), usuario_id, venta_id))
    auditar(con, usuario_id, v["sede_id"], "venta.anular", f"{v['numero']}: {motivo.strip()}")
    return {"numero": v["numero"], "reembolso": reembolso if v["medio_pago"] != "credito" else 0}


def devolver(con, venta_id: int, cantidades: dict[int, float], usuario_id: int, sede_id: int, motivo: str) -> dict:
    """Devolución parcial: {venta_item_id: cantidad}. El dinero vuelve por el mismo medio."""
    v = uno(con, "SELECT * FROM ventas WHERE id = ?", venta_id)
    if not v or v["estado"] == "anulada":
        raise ErrorNegocio("La venta no existe o está anulada.")
    if not motivo.strip():
        raise ErrorNegocio("Escribe el motivo de la devolución.")
    total, detalle = 0, []
    for item_id, cantidad in cantidades.items():
        if not cantidad or cantidad <= 0:
            continue
        it = uno(con, "SELECT vi.*, p.nombre FROM venta_items vi JOIN productos p ON p.id = vi.producto_id "
                      "WHERE vi.id = ? AND vi.venta_id = ?", item_id, venta_id)
        if not it:
            raise ErrorNegocio("Un producto de la devolución no pertenece a la venta.")
        if cantidad > it["cantidad"] - it["devuelto"] + 1e-9:
            raise ErrorNegocio(f"No se puede devolver más de lo vendido de «{it['nombre']}».")
        valor_linea = round(it["total"] * cantidad / it["cantidad"])
        total += valor_linea
        con.execute("UPDATE venta_items SET devuelto = devuelto + ? WHERE id = ?", (cantidad, item_id))
        mover(con, it["producto_id"], v["sede_id"], "devolucion", cantidad, usuario_id=usuario_id,
              referencia=f"venta:{venta_id}", costo_unitario=it["costo_unitario"], nota=motivo.strip())
        detalle.append(f"{fmt_cant(cantidad)} {it['nombre']}")
    if total == 0:
        raise ErrorNegocio("Indica qué cantidades se devuelven.")
    caja = None
    if v["medio_pago"] == "efectivo":
        caja = caja_mod.exigir_caja(con, sede_id, usuario_id)
        caja_mod.movimiento(con, caja["id"], "egreso", total, f"Devolución {v['numero']}", usuario_id)
    elif v["medio_pago"] == "credito":
        total = min(total, saldo_venta(con, venta_id))  # en crédito, la devolución baja la deuda
    con.execute("INSERT INTO devoluciones (venta_id, fecha, usuario_id, caja_id, total, motivo) VALUES (?, ?, ?, ?, ?, ?)",
                (venta_id, ahora(), usuario_id, caja["id"] if caja else None, total, motivo.strip()))
    if v["medio_pago"] == "credito" and saldo_venta(con, venta_id) == 0:
        con.execute("UPDATE ventas SET estado = 'pagada' WHERE id = ?", (venta_id,))
    auditar(con, usuario_id, v["sede_id"], "venta.devolucion",
            f"{v['numero']}: {', '.join(detalle)} ({pesos(total)}). {motivo.strip()}")
    return {"total": total}
