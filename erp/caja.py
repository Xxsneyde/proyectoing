"""Turnos de caja: apertura con base, ingresos/egresos, arqueo y cierre."""

from erp.db import ErrorNegocio, ahora, auditar, uno, valor
from erp.formato import pesos


def caja_abierta(con, sede_id: int, usuario_id: int) -> dict | None:
    return uno(con, "SELECT * FROM cajas WHERE sede_id = ? AND usuario_id = ? AND estado = 'abierta'",
               sede_id, usuario_id)


def exigir_caja(con, sede_id: int, usuario_id: int) -> dict:
    caja = caja_abierta(con, sede_id, usuario_id)
    if not caja:
        raise ErrorNegocio("Abre tu caja antes de recibir dinero (menú Caja → Abrir caja).")
    return caja


def abrir(con, sede_id: int, usuario_id: int, base: int, fecha: str | None = None) -> int:
    if base < 0:
        raise ErrorNegocio("La base no puede ser negativa.")
    if caja_abierta(con, sede_id, usuario_id):
        raise ErrorNegocio("Ya tienes una caja abierta en esta sede.")
    cur = con.execute("INSERT INTO cajas (sede_id, usuario_id, base, abierta) VALUES (?, ?, ?, ?)",
                      (sede_id, usuario_id, int(base), fecha or ahora()))
    auditar(con, usuario_id, sede_id, "caja.abrir", f"Caja #{cur.lastrowid} con base {pesos(base)}")
    return cur.lastrowid


def movimiento(con, caja_id: int, tipo: str, valor_: int, concepto: str, usuario_id: int,
               fecha: str | None = None):
    caja = uno(con, "SELECT * FROM cajas WHERE id = ?", caja_id)
    if not caja or caja["estado"] != "abierta":
        raise ErrorNegocio("La caja no está abierta.")
    if tipo not in ("ingreso", "egreso"):
        raise ErrorNegocio("Tipo de movimiento inválido.")
    if valor_ <= 0:
        raise ErrorNegocio("El valor debe ser mayor que cero.")
    if not concepto.strip():
        raise ErrorNegocio("Escribe el concepto.")
    if tipo == "egreso" and valor_ > resumen(con, caja_id)["esperado"]:
        raise ErrorNegocio("No hay suficiente efectivo en la caja para ese egreso.")
    con.execute("INSERT INTO movimientos_caja (caja_id, tipo, valor, concepto, usuario_id, fecha) "
                "VALUES (?, ?, ?, ?, ?, ?)", (caja_id, tipo, int(valor_), concepto.strip(), usuario_id, fecha or ahora()))
    auditar(con, usuario_id, caja["sede_id"], f"caja.{tipo}", f"{pesos(valor_)} — {concepto.strip()}")


def resumen(con, caja_id: int) -> dict:
    """Lo que debería haber en efectivo y lo vendido por cada medio de pago."""
    caja = uno(con, "SELECT * FROM cajas WHERE id = ?", caja_id)
    por_medio = {m: 0 for m in ("efectivo", "tarjeta", "transferencia", "credito")}
    for fila in con.execute("SELECT medio_pago, SUM(total) FROM ventas WHERE caja_id = ? GROUP BY medio_pago", (caja_id,)):
        por_medio[fila[0]] = fila[1] or 0
    anuladas = valor(con, "SELECT COUNT(*) FROM ventas WHERE caja_id = ? AND estado = 'anulada'", caja_id, defecto=0)
    n_ventas = valor(con, "SELECT COUNT(*) FROM ventas WHERE caja_id = ? AND estado <> 'anulada'", caja_id, defecto=0)
    abonos = valor(con, "SELECT SUM(valor) FROM abonos WHERE caja_id = ? AND medio = 'efectivo'", caja_id, defecto=0)
    ingresos = valor(con, "SELECT SUM(valor) FROM movimientos_caja WHERE caja_id = ? AND tipo = 'ingreso'", caja_id, defecto=0)
    egresos = valor(con, "SELECT SUM(valor) FROM movimientos_caja WHERE caja_id = ? AND tipo = 'egreso'", caja_id, defecto=0)
    # Las anulaciones y devoluciones en efectivo salen como egreso, así que las
    # ventas en efectivo se suman completas aunque luego se hayan anulado.
    esperado = caja["base"] + por_medio["efectivo"] + abonos + ingresos - egresos
    return {
        "caja": caja, "por_medio": por_medio, "abonos_efectivo": abonos, "ingresos": ingresos,
        "egresos": egresos, "esperado": esperado, "ventas": n_ventas, "anuladas": anuladas,
    }


def cerrar(con, caja_id: int, efectivo_contado: int, usuario_id: int, nota: str = "",
           fecha: str | None = None) -> dict:
    caja = uno(con, "SELECT * FROM cajas WHERE id = ?", caja_id)
    if not caja or caja["estado"] != "abierta":
        raise ErrorNegocio("La caja ya está cerrada.")
    if efectivo_contado < 0:
        raise ErrorNegocio("El efectivo contado no puede ser negativo.")
    r = resumen(con, caja_id)
    diferencia = int(efectivo_contado) - r["esperado"]
    con.execute("UPDATE cajas SET estado = 'cerrada', cerrada = ?, efectivo_contado = ?, diferencia = ?, nota = ? "
                "WHERE id = ?", (fecha or ahora(), int(efectivo_contado), diferencia, nota.strip(), caja_id))
    auditar(con, usuario_id, caja["sede_id"], "caja.cerrar",
            f"Caja #{caja_id}: esperado {pesos(r['esperado'])}, contado {pesos(efectivo_contado)}, "
            f"diferencia {pesos(diferencia)}")
    return {**r, "diferencia": diferencia}
