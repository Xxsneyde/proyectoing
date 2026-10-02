"""Traslados entre sedes: borrador → en tránsito (sale del origen) → recibido (entra al destino)."""

from erp.db import ErrorNegocio, ahora, auditar, consecutivo, todos, uno
from erp.formato import cant
from erp.inventario import mover


def crear(con, origen_id: int, destino_id: int, items: list[dict], usuario_id: int, nota: str = "",
          fecha: str | None = None) -> int:
    if origen_id == destino_id:
        raise ErrorNegocio("El origen y el destino deben ser sedes distintas.")
    items = [i for i in items if float(i.get("cantidad") or 0) > 0]
    if not items:
        raise ErrorNegocio("El traslado no tiene productos.")
    numero = consecutivo(con, "TR")
    cur = con.execute("INSERT INTO traslados (numero, origen_id, destino_id, usuario_envia, nota, creado) "
                      "VALUES (?, ?, ?, ?, ?, ?)", (numero, origen_id, destino_id, usuario_id, nota.strip(), fecha or ahora()))
    for it in items:
        con.execute("INSERT INTO traslado_items (traslado_id, producto_id, cantidad) VALUES (?, ?, ?)",
                    (cur.lastrowid, int(it["producto_id"]), float(it["cantidad"])))
    auditar(con, usuario_id, origen_id, "traslado.crear", f"{numero}: {len(items)} productos")
    return cur.lastrowid


def enviar(con, traslado_id: int, usuario_id: int, fecha: str | None = None):
    t = _traslado(con, traslado_id)
    if t["estado"] != "borrador":
        raise ErrorNegocio("Este traslado ya fue enviado.")
    fecha = fecha or ahora()
    for it in todos(con, "SELECT * FROM traslado_items WHERE traslado_id = ?", traslado_id):
        mover(con, it["producto_id"], t["origen_id"], "traslado_salida", -it["cantidad"], usuario_id=usuario_id,
              referencia=f"traslado:{traslado_id}", fecha=fecha)
    con.execute("UPDATE traslados SET estado = 'en_transito', enviado = ?, usuario_envia = ? WHERE id = ?",
                (fecha, usuario_id, traslado_id))
    auditar(con, usuario_id, t["origen_id"], "traslado.enviar", t["numero"])


def recibir(con, traslado_id: int, recibidos: dict[int, float], usuario_id: int, fecha: str | None = None) -> list[str]:
    """recibidos: {traslado_item_id: cantidad}. Devuelve los faltantes para dejarlos a la vista."""
    t = _traslado(con, traslado_id)
    if t["estado"] != "en_transito":
        raise ErrorNegocio("Solo se reciben traslados en tránsito.")
    fecha = fecha or ahora()
    faltantes = []
    for it in todos(con, "SELECT ti.*, p.nombre FROM traslado_items ti JOIN productos p ON p.id = ti.producto_id "
                         "WHERE ti.traslado_id = ?", traslado_id):
        llegado = float(recibidos.get(it["id"], it["cantidad"]))
        if llegado < 0 or llegado > it["cantidad"]:
            raise ErrorNegocio(f"«{it['nombre']}»: lo recibido debe estar entre 0 y {cant(it['cantidad'])}.")
        if llegado > 0:
            mover(con, it["producto_id"], t["destino_id"], "traslado_entrada", llegado, usuario_id=usuario_id,
                  referencia=f"traslado:{traslado_id}", fecha=fecha)
        con.execute("UPDATE traslado_items SET recibido = ? WHERE id = ?", (llegado, it["id"]))
        if llegado < it["cantidad"]:
            faltantes.append(f"{cant(it['cantidad'] - llegado)} {it['nombre']}")
    con.execute("UPDATE traslados SET estado = 'recibido', recibido = ?, usuario_recibe = ? WHERE id = ?",
                (fecha, usuario_id, traslado_id))
    detalle = t["numero"] + (f"; faltaron: {', '.join(faltantes)}" if faltantes else "; completo")
    auditar(con, usuario_id, t["destino_id"], "traslado.recibir", detalle)
    return faltantes


def anular(con, traslado_id: int, usuario_id: int, motivo: str):
    """En borrador solo se marca; en tránsito, la mercancía vuelve al origen."""
    t = _traslado(con, traslado_id)
    if t["estado"] not in ("borrador", "en_transito"):
        raise ErrorNegocio("Un traslado recibido no se puede anular.")
    if not motivo.strip():
        raise ErrorNegocio("Escribe el motivo.")
    if t["estado"] == "en_transito":
        for it in todos(con, "SELECT * FROM traslado_items WHERE traslado_id = ?", traslado_id):
            mover(con, it["producto_id"], t["origen_id"], "traslado_entrada", it["cantidad"], usuario_id=usuario_id,
                  referencia=f"traslado:{traslado_id}", nota="Anulación: regresa al origen")
    con.execute("UPDATE traslados SET estado = 'anulado', nota = TRIM(nota || ' Anulado: ' || ?) WHERE id = ?",
                (motivo.strip(), traslado_id))
    auditar(con, usuario_id, t["origen_id"], "traslado.anular", f"{t['numero']}: {motivo.strip()}")


def _traslado(con, traslado_id: int) -> dict:
    t = uno(con, "SELECT * FROM traslados WHERE id = ?", traslado_id)
    if not t:
        raise ErrorNegocio("El traslado no existe.")
    return t
