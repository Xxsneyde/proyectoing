"""Inventario: cada cambio de existencias es un movimiento con usuario, fecha y motivo."""

from erp.db import ErrorNegocio, ahora, auditar, todos, uno, valor
from erp.formato import cant as fmt_cant


def _redondear(x: float) -> float:
    return round(float(x), 3) + 0.0  # + 0.0 evita "-0.0"


def existencia(con, producto_id: int, sede_id: int) -> float:
    return valor(con, "SELECT cantidad FROM existencias WHERE producto_id = ? AND sede_id = ?",
                 producto_id, sede_id, defecto=0.0)


def mover(con, producto_id: int, sede_id: int, tipo: str, cantidad: float, *,
          usuario_id: int | None = None, referencia: str = "", nota: str = "",
          costo_unitario: float | None = None, fecha: str | None = None,
          permitir_negativo: bool = False) -> float:
    """Registra un movimiento y actualiza la existencia. Devuelve el saldo.

    Debe llamarse dentro de una transacción: el movimiento y el saldo se
    guardan juntos o no se guardan.
    """
    cantidad = _redondear(cantidad)
    if cantidad == 0:
        return existencia(con, producto_id, sede_id)
    prod = uno(con, "SELECT nombre, unidad, costo FROM productos WHERE id = ?", producto_id)
    if not prod:
        raise ErrorNegocio("El producto no existe.")
    actual = uno(con, "SELECT cantidad FROM existencias WHERE producto_id = ? AND sede_id = ?",
                 producto_id, sede_id)
    if actual is None:
        con.execute("INSERT INTO existencias (producto_id, sede_id) VALUES (?, ?)", (producto_id, sede_id))
        actual = {"cantidad": 0.0}
    saldo = _redondear(actual["cantidad"] + cantidad)
    if saldo < 0 and not permitir_negativo:
        sede = valor(con, "SELECT nombre FROM sedes WHERE id = ?", sede_id, defecto="la sede")
        raise ErrorNegocio(
            f"No hay suficiente «{prod['nombre']}» en {sede}: hay {fmt_cant(actual['cantidad'])} "
            f"{prod['unidad']} y se necesitan {fmt_cant(-cantidad)}."
        )
    con.execute("UPDATE existencias SET cantidad = ? WHERE producto_id = ? AND sede_id = ?",
                (saldo, producto_id, sede_id))
    con.execute(
        "INSERT INTO movimientos (fecha, producto_id, sede_id, tipo, cantidad, saldo, costo_unitario, "
        "referencia, usuario_id, nota) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (fecha or ahora(), producto_id, sede_id, tipo, cantidad, saldo,
         prod["costo"] if costo_unitario is None else costo_unitario, referencia, usuario_id, nota),
    )
    return saldo


def actualizar_costo_promedio(con, producto_id: int, cantidad_entra: float, costo_entra: float):
    """Costo promedio ponderado con la existencia total de todas las sedes."""
    prod = uno(con, "SELECT costo FROM productos WHERE id = ?", producto_id)
    total = max(0.0, valor(con, "SELECT SUM(cantidad) FROM existencias WHERE producto_id = ?",
                           producto_id, defecto=0.0))
    if total + cantidad_entra <= 0:
        nuevo = costo_entra
    else:
        nuevo = (total * prod["costo"] + cantidad_entra * costo_entra) / (total + cantidad_entra)
    con.execute("UPDATE productos SET costo = ?, actualizado = ? WHERE id = ?",
                (round(nuevo, 2), ahora(), producto_id))


def ajustar(con, producto_id: int, sede_id: int, nueva_cantidad: float, motivo: str, usuario_id: int) -> float:
    """Ajuste manual: deja la existencia en `nueva_cantidad` y registra el porqué."""
    if not motivo.strip():
        raise ErrorNegocio("Escribe el motivo del ajuste.")
    if nueva_cantidad < 0:
        raise ErrorNegocio("La cantidad no puede ser negativa.")
    delta = nueva_cantidad - existencia(con, producto_id, sede_id)
    saldo = mover(con, producto_id, sede_id, "ajuste", delta, usuario_id=usuario_id, nota=motivo.strip())
    nombre = valor(con, "SELECT nombre FROM productos WHERE id = ?", producto_id)
    auditar(con, usuario_id, sede_id, "inventario.ajuste",
            f"{nombre}: {fmt_cant(delta, signo=True)} → {fmt_cant(saldo)}. Motivo: {motivo.strip()}")
    return saldo


def registrar_danado(con, producto_id: int, sede_id: int, cantidad: float, motivo: str, usuario_id: int):
    if cantidad <= 0:
        raise ErrorNegocio("La cantidad dañada debe ser mayor que cero.")
    mover(con, producto_id, sede_id, "danado", -cantidad, usuario_id=usuario_id, nota=motivo)
    nombre = valor(con, "SELECT nombre FROM productos WHERE id = ?", producto_id)
    auditar(con, usuario_id, sede_id, "inventario.danado", f"{nombre}: {fmt_cant(cantidad)} dañados. {motivo}")


def stock_por_sede(con, producto_id: int) -> list[dict]:
    return todos(con, """
        SELECT s.id AS sede_id, s.nombre AS sede, COALESCE(e.cantidad, 0) AS cantidad,
               COALESCE(e.minimo, 0) AS minimo, COALESCE(e.maximo, 0) AS maximo,
               COALESCE(e.ubicacion, '') AS ubicacion, e.ultimo_conteo
        FROM sedes s LEFT JOIN existencias e ON e.sede_id = s.id AND e.producto_id = ?
        WHERE s.activa = 1 ORDER BY s.id""", producto_id)


def bajo_minimo(con, sede_id: int | None = None, limite: int = 500) -> list[dict]:
    """Productos en o por debajo del mínimo, con la cantidad sugerida para llegar al máximo."""
    filtro = "AND e.sede_id = ?" if sede_id else ""
    params = (sede_id,) if sede_id else ()
    return todos(con, f"""
        SELECT p.id, p.codigo, p.nombre, p.unidad, p.costo, p.proveedor_id,
               pr.nombre AS proveedor, s.id AS sede_id, s.nombre AS sede,
               e.cantidad, e.minimo, e.maximo, e.ubicacion,
               MAX(e.maximo - e.cantidad, 1) AS sugerido
        FROM existencias e
        JOIN productos p ON p.id = e.producto_id AND p.activo = 1
        JOIN sedes s ON s.id = e.sede_id
        LEFT JOIN proveedores pr ON pr.id = p.proveedor_id
        WHERE e.minimo > 0 AND e.cantidad <= e.minimo {filtro}
        ORDER BY (e.cantidad <= 0) DESC, e.cantidad / e.minimo, p.nombre
        LIMIT {int(limite)}""", *params)
