"""Órdenes de compra a proveedores: borrador → enviada → recibida (parcial o total) → pagada."""

from datetime import datetime, timedelta

from erp.db import ErrorNegocio, ahora, auditar, consecutivo, todos, uno, valor
from erp.formato import pesos
from erp.inventario import actualizar_costo_promedio, bajo_minimo, mover


def crear(con, proveedor_id: int, sede_id: int, items: list[dict], usuario_id: int, nota: str = "",
          fecha: str | None = None) -> int:
    """items: [{producto_id, cantidad, costo_unitario}]"""
    if not uno(con, "SELECT id FROM proveedores WHERE id = ? AND activo = 1", proveedor_id):
        raise ErrorNegocio("Selecciona un proveedor válido.")
    items = [i for i in items if float(i.get("cantidad") or 0) > 0]
    if not items:
        raise ErrorNegocio("La orden no tiene productos.")
    numero = consecutivo(con, "OC")
    cur = con.execute("INSERT INTO compras (numero, proveedor_id, sede_id, usuario_id, nota, creada) VALUES (?, ?, ?, ?, ?, ?)",
                      (numero, proveedor_id, sede_id, usuario_id, nota.strip(), fecha or ahora()))
    compra_id = cur.lastrowid
    total = 0
    for it in items:
        p = uno(con, "SELECT id, costo FROM productos WHERE id = ?", int(it["producto_id"]))
        if not p:
            raise ErrorNegocio("Uno de los productos no existe.")
        costo = float(it.get("costo_unitario") or p["costo"])
        if costo <= 0:
            raise ErrorNegocio("El costo debe ser mayor que cero.")
        cantidad = float(it["cantidad"])
        con.execute("INSERT INTO compra_items (compra_id, producto_id, cantidad, costo_unitario) VALUES (?, ?, ?, ?)",
                    (compra_id, p["id"], cantidad, costo))
        total += round(cantidad * costo)
    con.execute("UPDATE compras SET total = ? WHERE id = ?", (total, compra_id))
    auditar(con, usuario_id, sede_id, "compra.crear", f"{numero} por {pesos(total)}")
    return compra_id


def enviar(con, compra_id: int, usuario_id: int):
    c = _compra(con, compra_id)
    if c["estado"] != "borrador":
        raise ErrorNegocio("Solo se envían órdenes en borrador.")
    con.execute("UPDATE compras SET estado = 'enviada' WHERE id = ?", (compra_id,))
    auditar(con, usuario_id, c["sede_id"], "compra.enviar", c["numero"])


def recibir(con, compra_id: int, recibidos: dict[int, float], usuario_id: int, factura_proveedor: str = "",
            costos: dict[int, float] | None = None, fecha: str | None = None):
    """recibidos: {compra_item_id: cantidad recibida ahora}. Entra al inventario y actualiza el costo."""
    c = _compra(con, compra_id)
    if c["estado"] not in ("borrador", "enviada", "parcial"):
        raise ErrorNegocio("Esta orden ya fue recibida o está anulada.")
    fecha = fecha or ahora()
    costos = costos or {}
    hubo = False
    for item_id, cantidad in recibidos.items():
        if not cantidad or cantidad <= 0:
            continue
        it = uno(con, "SELECT ci.*, p.nombre FROM compra_items ci JOIN productos p ON p.id = ci.producto_id "
                      "WHERE ci.id = ? AND ci.compra_id = ?", item_id, compra_id)
        if not it:
            raise ErrorNegocio("Un producto recibido no pertenece a la orden.")
        if it["recibido"] + cantidad > it["cantidad"] * 1.5:
            raise ErrorNegocio(f"«{it['nombre']}»: se recibe mucho más de lo pedido; revisa la cantidad.")
        costo = float(costos.get(item_id) or it["costo_unitario"])
        actualizar_costo_promedio(con, it["producto_id"], cantidad, costo)
        mover(con, it["producto_id"], c["sede_id"], "compra", cantidad, usuario_id=usuario_id,
              referencia=f"compra:{compra_id}", costo_unitario=costo, fecha=fecha)
        con.execute("UPDATE compra_items SET recibido = recibido + ?, costo_unitario = ? WHERE id = ?",
                    (cantidad, costo, item_id))
        hubo = True
    if not hubo:
        raise ErrorNegocio("Indica las cantidades recibidas.")
    pendientes = valor(con, "SELECT COUNT(*) FROM compra_items WHERE compra_id = ? AND recibido < cantidad - 1e-9",
                       compra_id, defecto=0)
    total = valor(con, "SELECT SUM(ROUND(recibido * costo_unitario)) FROM compra_items WHERE compra_id = ?",
                  compra_id, defecto=0)
    plazo = valor(con, "SELECT plazo_dias FROM proveedores WHERE id = ?", c["proveedor_id"], defecto=30)
    vence = (datetime.strptime(fecha[:10], "%Y-%m-%d") + timedelta(days=plazo)).strftime("%Y-%m-%d")
    con.execute("UPDATE compras SET estado = ?, recibida = ?, total = ?, vence = COALESCE(vence, ?), "
                "factura_proveedor = CASE WHEN ? <> '' THEN ? ELSE factura_proveedor END WHERE id = ?",
                ("parcial" if pendientes else "recibida", fecha, int(total), vence,
                 factura_proveedor.strip(), factura_proveedor.strip(), compra_id))
    auditar(con, usuario_id, c["sede_id"], "compra.recibir",
            f"{c['numero']} {'parcial' if pendientes else 'completa'}; total recibido {pesos(total)}")


def pagar(con, compra_id: int, usuario_id: int, fecha: str | None = None):
    c = _compra(con, compra_id)
    if c["estado"] not in ("parcial", "recibida"):
        raise ErrorNegocio("Solo se pagan órdenes recibidas.")
    if c["pagada"]:
        raise ErrorNegocio("Esta orden ya está pagada.")
    con.execute("UPDATE compras SET pagada = ? WHERE id = ?", (fecha or ahora(), compra_id))
    auditar(con, usuario_id, c["sede_id"], "compra.pagar", f"{c['numero']} por {pesos(c['total'])}")


def anular(con, compra_id: int, usuario_id: int, motivo: str):
    c = _compra(con, compra_id)
    if c["estado"] not in ("borrador", "enviada"):
        raise ErrorNegocio("Solo se anulan órdenes sin mercancía recibida.")
    con.execute("UPDATE compras SET estado = 'anulada', nota = TRIM(nota || ' Anulada: ' || ?) WHERE id = ?",
                (motivo.strip(), compra_id))
    auditar(con, usuario_id, c["sede_id"], "compra.anular", f"{c['numero']}: {motivo.strip()}")


def sugerido_por_proveedor(con, sede_id: int) -> list[dict]:
    """Lo que está bajo el mínimo en la sede, agrupado por proveedor, listo para pedir."""
    grupos: dict = {}
    for p in bajo_minimo(con, sede_id):
        clave = p["proveedor_id"] or 0
        g = grupos.setdefault(clave, {"proveedor_id": p["proveedor_id"], "proveedor": p["proveedor"] or "Sin proveedor",
                                      "items": [], "total": 0})
        p["sugerido"] = max(1, round(p["sugerido"]))
        g["items"].append(p)
        g["total"] += round(p["sugerido"] * p["costo"])
    return sorted(grupos.values(), key=lambda g: -g["total"])


def cuentas_por_pagar(con, sede_id: int | None = None) -> list[dict]:
    filtro = "AND c.sede_id = ?" if sede_id else ""
    params = (sede_id,) if sede_id else ()
    return todos(con, f"""
        SELECT c.*, pr.nombre AS proveedor, s.nombre AS sede,
               CAST(julianday(c.vence) - julianday(date('now', 'localtime')) AS INTEGER) AS dias
        FROM compras c JOIN proveedores pr ON pr.id = c.proveedor_id JOIN sedes s ON s.id = c.sede_id
        WHERE c.estado IN ('parcial', 'recibida') AND c.pagada IS NULL {filtro}
        ORDER BY c.vence""", *params)


def _compra(con, compra_id: int) -> dict:
    c = uno(con, "SELECT * FROM compras WHERE id = ?", compra_id)
    if not c:
        raise ErrorNegocio("La orden de compra no existe.")
    return c

