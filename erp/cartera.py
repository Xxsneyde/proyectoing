"""Cartera: ventas a crédito, abonos y vencimientos."""

from datetime import date

from erp import caja as caja_mod
from erp.db import ErrorNegocio, ahora, auditar, hoy, todos, uno
from erp.formato import pesos
from erp.ventas import saldo_venta

RANGOS = (("al_dia", "Al día"), ("1_30", "1–30 días"), ("31_60", "31–60 días"),
          ("61_90", "61–90 días"), ("mas_90", "Más de 90 días"))


def _rango(dias_vencido: int) -> str:
    if dias_vencido <= 0:
        return "al_dia"
    if dias_vencido <= 30:
        return "1_30"
    if dias_vencido <= 60:
        return "31_60"
    if dias_vencido <= 90:
        return "61_90"
    return "mas_90"


def cuentas_por_cobrar(con, sede_id: int | None = None, cliente_id: int | None = None) -> list[dict]:
    filtros, params = ["v.medio_pago = 'credito'", "v.estado = 'credito'"], []
    if sede_id:
        filtros.append("v.sede_id = ?")
        params.append(sede_id)
    if cliente_id:
        filtros.append("v.cliente_id = ?")
        params.append(cliente_id)
    filas = todos(con, f"""
        SELECT v.id, v.numero, v.fecha, v.vence, v.total, v.sede_id, s.nombre AS sede,
               c.id AS cliente_id, c.nombre AS cliente, c.documento, c.telefono
        FROM ventas v JOIN clientes c ON c.id = v.cliente_id JOIN sedes s ON s.id = v.sede_id
        WHERE {' AND '.join(filtros)} ORDER BY v.vence""", *params)
    hoy_ = date.fromisoformat(hoy())
    salida = []
    for f in filas:
        f["saldo"] = saldo_venta(con, f["id"])
        if f["saldo"] <= 0:
            continue
        f["dias_vencido"] = (hoy_ - date.fromisoformat(f["vence"])).days if f["vence"] else 0
        f["rango"] = _rango(f["dias_vencido"])
        salida.append(f)
    return salida


def por_edades(con, sede_id: int | None = None) -> dict:
    cuentas = cuentas_por_cobrar(con, sede_id)
    totales = {k: 0 for k, _ in RANGOS}
    for c in cuentas:
        totales[c["rango"]] += c["saldo"]
    total = sum(totales.values())
    return {"rangos": [{"clave": k, "nombre": n, "valor": totales[k]} for k, n in RANGOS],
            "total": total, "vencido": total - totales["al_dia"], "cuentas": cuentas}


def abonar(con, venta_id: int, valor_: int, medio: str, usuario_id: int, sede_id: int,
           fecha: str | None = None) -> int:
    """Registra el abono y devuelve el saldo que queda."""
    v = uno(con, "SELECT * FROM ventas WHERE id = ?", venta_id)
    if not v or v["medio_pago"] != "credito" or v["estado"] == "anulada":
        raise ErrorNegocio("La venta no es a crédito o está anulada.")
    if medio not in ("efectivo", "tarjeta", "transferencia"):
        raise ErrorNegocio("Medio de pago inválido.")
    saldo = saldo_venta(con, venta_id)
    if valor_ <= 0:
        raise ErrorNegocio("El abono debe ser mayor que cero.")
    if valor_ > saldo:
        raise ErrorNegocio(f"El abono supera el saldo de la factura ({pesos(saldo)}).")
    caja = caja_mod.exigir_caja(con, sede_id, usuario_id) if medio == "efectivo" else caja_mod.caja_abierta(con, sede_id, usuario_id)
    con.execute("INSERT INTO abonos (venta_id, valor, medio, usuario_id, caja_id, fecha) VALUES (?, ?, ?, ?, ?, ?)",
                (venta_id, int(valor_), medio, usuario_id, caja["id"] if caja else None, fecha or ahora()))
    nuevo = saldo - int(valor_)
    if nuevo == 0:
        con.execute("UPDATE ventas SET estado = 'pagada' WHERE id = ?", (venta_id,))
    auditar(con, usuario_id, sede_id, "cartera.abono", f"{v['numero']}: {pesos(valor_)} ({medio}); saldo {pesos(nuevo)}")
    return nuevo
