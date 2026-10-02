"""Toma de inventario: conteos por sede (general, por categoría, por ubicación o cíclicos).

Cada producto guarda la existencia del sistema en el momento en que se cuenta;
al aprobar, se ajusta solo la diferencia. Así las ventas que ocurren mientras
se cuenta no se pierden ni se duplican.
"""

import re

from erp.db import ErrorNegocio, ahora, auditar, todos, uno, valor
from erp.formato import cant, pesos
from erp.inventario import existencia, mover
from voz.comandos import normalizar

TIPOS = {"general": "Toda la sede", "categoria": "Una categoría", "ubicacion": "Una ubicación", "ciclico": "Cíclico sugerido"}


def crear(con, sede_id: int, nombre: str, tipo: str, filtro: str, ciego: bool, usuario_id: int,
          fecha: str | None = None) -> int:
    if tipo not in TIPOS:
        raise ErrorNegocio("Tipo de conteo inválido.")
    if tipo in ("categoria", "ubicacion") and not filtro.strip():
        raise ErrorNegocio("Indica la categoría o la ubicación a contar.")
    if tipo == "ciclico" and not filtro:
        filtro = ",".join(str(p["id"]) for p in sugeridos_ciclicos(con, sede_id))
        if not filtro:
            raise ErrorNegocio("No hay productos sugeridos para contar en esta sede.")
    nombre = nombre.strip() or f"{TIPOS[tipo]} {ahora()[:10]}"
    cur = con.execute("INSERT INTO conteos (sede_id, nombre, tipo, filtro, ciego, usuario_id, creado) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?)", (sede_id, nombre, tipo, filtro.strip(), int(ciego), usuario_id, fecha or ahora()))
    auditar(con, usuario_id, sede_id, "conteo.crear", f"#{cur.lastrowid} {nombre} ({TIPOS[tipo].lower()})")
    return cur.lastrowid


def alcance(con, conteo: dict) -> list[dict]:
    """Productos que se espera contar."""
    base = """SELECT p.id, p.codigo, p.nombre, p.unidad, p.costo, c.nombre AS categoria,
                     COALESCE(e.cantidad, 0) AS existencia, COALESCE(e.ubicacion, '') AS ubicacion
              FROM productos p
              LEFT JOIN categorias c ON c.id = p.categoria_id
              LEFT JOIN existencias e ON e.producto_id = p.id AND e.sede_id = ?
              WHERE p.activo = 1"""
    t, f = conteo["tipo"], conteo["filtro"]
    if t == "categoria":
        return todos(con, base + " AND p.categoria_id = ? ORDER BY ubicacion, p.nombre", conteo["sede_id"], int(f))
    if t == "ubicacion":
        return todos(con, base + " AND (LOWER(e.ubicacion) = ? OR LOWER(e.ubicacion) LIKE ?) ORDER BY ubicacion, p.nombre",
                     conteo["sede_id"], f.lower(), f.lower() + " %")
    if t == "ciclico":
        ids = [int(x) for x in f.split(",") if x.strip().isdigit()]
        marcas = ",".join("?" * len(ids)) or "NULL"
        return todos(con, base + f" AND p.id IN ({marcas}) ORDER BY ubicacion, p.nombre", conteo["sede_id"], *ids)
    return todos(con, base + " ORDER BY ubicacion, p.nombre", conteo["sede_id"])


def _abierto(con, conteo_id: int) -> dict:
    c = uno(con, "SELECT * FROM conteos WHERE id = ?", conteo_id)
    if not c:
        raise ErrorNegocio("El conteo no existe.")
    if c["estado"] != "abierto":
        raise ErrorNegocio("Este conteo ya está cerrado.")
    return c


def item(con, conteo_id: int, producto_id: int) -> dict | None:
    return uno(con, "SELECT * FROM conteo_items WHERE conteo_id = ? AND producto_id = ?", conteo_id, producto_id)


def registrar(con, conteo_id: int, producto_id: int, cantidad: float, usuario_id: int,
              metodo: str = "manual", modo: str = "reemplazar", fecha: str | None = None) -> dict:
    """modo 'reemplazar' fija el conteo; 'sumar' agrega (mismo producto en varios sitios)."""
    c = _abierto(con, conteo_id)
    if not uno(con, "SELECT id FROM productos WHERE id = ?", producto_id):
        raise ErrorNegocio("El producto no existe.")
    actual = item(con, conteo_id, producto_id)
    if actual is None:
        nuevo = float(cantidad)
        if nuevo < 0:
            raise ErrorNegocio("El conteo no puede quedar negativo.")
        con.execute("INSERT INTO conteo_items (conteo_id, producto_id, sistema, contado, metodo, usuario_id, fecha) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (conteo_id, producto_id, existencia(con, producto_id, c["sede_id"]), nuevo, metodo, usuario_id, fecha or ahora()))
    else:
        nuevo = actual["contado"] + float(cantidad) if modo == "sumar" else float(cantidad)
        if nuevo < 0:
            raise ErrorNegocio("El conteo no puede quedar negativo.")
        con.execute("UPDATE conteo_items SET contado_anterior = contado, contado = ?, metodo = ?, usuario_id = ?, fecha = ?, "
                    "ultima_accion = 'contado' WHERE id = ?", (round(nuevo, 3), metodo, usuario_id, fecha or ahora(), actual["id"]))
    return item(con, conteo_id, producto_id)


def marcar_danados(con, conteo_id: int, producto_id: int, cantidad: float, usuario_id: int) -> dict:
    _abierto(con, conteo_id)
    it = item(con, conteo_id, producto_id)
    if not it:
        raise ErrorNegocio("Primero registra cuántas unidades hay; luego marca las dañadas.")
    if it["danados"] + cantidad > it["contado"]:
        raise ErrorNegocio("Las dañadas no pueden superar lo contado.")
    con.execute("UPDATE conteo_items SET danados_anterior = danados, danados = danados + ?, usuario_id = ?, "
                "ultima_accion = 'danados' WHERE id = ?", (cantidad, usuario_id, it["id"]))
    return item(con, conteo_id, producto_id)


def deshacer(con, conteo_id: int, producto_id: int) -> str:
    _abierto(con, conteo_id)
    it = item(con, conteo_id, producto_id)
    if not it:
        return "No hay nada para deshacer en este producto."
    if it["ultima_accion"] == "danados" and it["danados_anterior"] is not None:
        con.execute("UPDATE conteo_items SET danados = danados_anterior, danados_anterior = NULL, ultima_accion = 'contado' "
                    "WHERE id = ?", (it["id"],))
        return f"Listo, quité los dañados; quedan {cant(it['danados_anterior'])}."
    if it["contado_anterior"] is not None:
        con.execute("UPDATE conteo_items SET contado = contado_anterior, contado_anterior = NULL, danados = MIN(danados, contado_anterior) "
                    "WHERE id = ?", (it["id"],))
        return f"Listo, volví a {cant(it['contado_anterior'])}."
    con.execute("DELETE FROM conteo_items WHERE id = ?", (it["id"],))
    return "Listo, borré el conteo de este producto."


def resumen(con, conteo_id: int) -> dict:
    c = uno(con, """SELECT co.*, s.nombre AS sede, u.nombre AS usuario FROM conteos co
                    JOIN sedes s ON s.id = co.sede_id LEFT JOIN usuarios u ON u.id = co.usuario_id
                    WHERE co.id = ?""", conteo_id)
    if not c:
        raise ErrorNegocio("El conteo no existe.")
    items = todos(con, """
        SELECT ci.*, p.codigo, p.nombre, p.unidad, p.costo, u.nombre AS usuario,
               ci.contado - ci.sistema AS diferencia,
               ROUND((ci.contado - ci.sistema) * p.costo) AS valor_diferencia
        FROM conteo_items ci JOIN productos p ON p.id = ci.producto_id
        LEFT JOIN usuarios u ON u.id = ci.usuario_id
        WHERE ci.conteo_id = ? ORDER BY ABS((ci.contado - ci.sistema) * p.costo) DESC, p.nombre""", conteo_id)
    esperados = alcance(con, c)
    contados_ids = {i["producto_id"] for i in items}
    pendientes = [p for p in esperados if p["id"] not in contados_ids]
    exactos = sum(1 for i in items if abs(i["diferencia"]) < 1e-9)
    return {
        "conteo": c, "items": items, "pendientes": pendientes,
        "total_alcance": len(esperados), "contados": len(items),
        "avance": round(100 * len(contados_ids & {p["id"] for p in esperados}) / len(esperados)) if esperados else 100,
        "exactitud": round(100 * exactos / len(items), 1) if items else None,
        "faltante": sum(i["valor_diferencia"] for i in items if i["valor_diferencia"] < 0),
        "sobrante": sum(i["valor_diferencia"] for i in items if i["valor_diferencia"] > 0),
        "danados": sum(i["danados"] for i in items),
        "con_diferencia": len(items) - exactos,
    }


def aprobar(con, conteo_id: int, usuario_id: int, fecha: str | None = None) -> dict:
    """Aplica las diferencias al inventario. Los productos sin contar no se tocan."""
    c = _abierto(con, conteo_id)
    r = resumen(con, conteo_id)
    if not r["items"]:
        raise ErrorNegocio("No hay productos contados para aprobar.")
    fecha = fecha or ahora()
    for it in r["items"]:
        nota = f"Conteo #{conteo_id} {c['nombre']}"
        if abs(it["diferencia"]) > 1e-9:
            mover(con, it["producto_id"], c["sede_id"], "conteo", it["diferencia"], usuario_id=usuario_id,
                  referencia=f"conteo:{conteo_id}", nota=nota, fecha=fecha, permitir_negativo=True)
        if it["danados"] > 0:
            mover(con, it["producto_id"], c["sede_id"], "danado", -it["danados"], usuario_id=usuario_id,
                  referencia=f"conteo:{conteo_id}", nota=nota, fecha=fecha, permitir_negativo=True)
        con.execute("INSERT INTO existencias (producto_id, sede_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
                    (it["producto_id"], c["sede_id"]))
        con.execute("UPDATE existencias SET ultimo_conteo = ? WHERE producto_id = ? AND sede_id = ?",
                    (fecha, it["producto_id"], c["sede_id"]))
    con.execute("UPDATE conteos SET estado = 'aprobado', aprobado = ?, aprobado_por = ? WHERE id = ?",
                (fecha, usuario_id, conteo_id))
    auditar(con, usuario_id, c["sede_id"], "conteo.aprobar",
            f"#{conteo_id} {c['nombre']}: {r['contados']} productos, {r['con_diferencia']} con diferencia, "
            f"faltante {pesos(r['faltante'])}, sobrante {pesos(r['sobrante'])}")
    return r


def anular(con, conteo_id: int, usuario_id: int):
    c = _abierto(con, conteo_id)
    con.execute("UPDATE conteos SET estado = 'anulado' WHERE id = ?", (conteo_id,))
    auditar(con, usuario_id, c["sede_id"], "conteo.anular", f"#{conteo_id} {c['nombre']}")


def sugeridos_ciclicos(con, sede_id: int, n: int = 25) -> list[dict]:
    """Conteo cíclico: primero lo que más se vende y hace más tiempo no se cuenta."""
    return todos(con, """
        SELECT p.id, p.nombre, e.cantidad, e.ultimo_conteo,
               COALESCE((SELECT SUM(-m.cantidad * m.costo_unitario) FROM movimientos m
                         WHERE m.producto_id = p.id AND m.sede_id = e.sede_id AND m.tipo = 'venta'
                           AND m.fecha >= datetime('now', 'localtime', '-30 days')), 0) AS rotacion,
               julianday('now', 'localtime') - julianday(COALESCE(e.ultimo_conteo, '2000-01-01')) AS dias
        FROM existencias e JOIN productos p ON p.id = e.producto_id AND p.activo = 1
        WHERE e.sede_id = ? AND (e.cantidad > 0 OR e.ultimo_conteo IS NULL)
        ORDER BY (rotacion + 1000) * MIN(dias, 365) DESC
        LIMIT ?""", sede_id, n)


# --- Órdenes por voz -------------------------------------------------------

def otros_productos_mencionados(con, texto: str, producto_id: int) -> list[str]:
    """Nombres de otros productos del catálogo que aparecen en la frase."""
    palabras = {w.rstrip("s") for w in normalizar(texto).split() if len(w) >= 4}
    if not palabras:
        return []
    actual = {w.rstrip("s") for w in normalizar(valor(con, "SELECT nombre FROM productos WHERE id = ?",
                                                        producto_id, defecto="")).split()}
    encontrados = []
    for p in todos(con, "SELECT id, nombre FROM productos WHERE activo = 1 AND id <> ?", producto_id):
        primera = normalizar(p["nombre"]).split()[0].rstrip("s")
        if len(primera) >= 4 and primera in palabras and primera not in actual:
            encontrados.append(primera)
    return sorted(set(encontrados))


def muy_lejos(it: dict) -> bool:
    """La cantidad contada difiere del sistema en al menos 5 unidades y en la mitad o más."""
    dif = abs(it["contado"] - it["sistema"])
    return dif >= 5 and dif >= 0.5 * max(it["sistema"], 1)


def aplicar_voz(con, conteo_id: int, producto_id: int, orden: dict, usuario_id: int, texto: str) -> str:
    """Aplica una orden ya interpretada y devuelve la respuesta que se dirá en voz alta."""
    c = _abierto(con, conteo_id)
    acc, n = orden.get("accion"), orden.get("cantidad")
    p = uno(con, "SELECT nombre, unidad FROM productos WHERE id = ?", producto_id)

    def con_diferencia(it: dict) -> str:
        dif = it["contado"] - it["sistema"]
        if c["ciego"]:
            # Sin revelar el número: un "cincuenta" mal oído no debe pasar en silencio
            return " Ojo: está muy lejos de lo esperado; si no es así, di deshacer." if muy_lejos(it) else ""
        return "" if abs(dif) < 1e-9 else f" Diferencia de {cant(dif, signo=True)} contra el sistema."

    if acc in ("conteo", "corregir") and n is not None:
        it = registrar(con, conteo_id, producto_id, n, usuario_id, "voz")
        verbo = "Corregido a" if acc == "corregir" else "Registradas"
        return f"{verbo} {cant(it['contado'])}.{con_diferencia(it)}"
    if acc in ("sumar", "restar") and n is not None:
        it = registrar(con, conteo_id, producto_id, n if acc == "sumar" else -n, usuario_id, "voz", modo="sumar")
        return f"Total {cant(it['contado'])}.{con_diferencia(it)}"
    if acc == "completo":
        it = registrar(con, conteo_id, producto_id, existencia(con, producto_id, c["sede_id"]), usuario_id, "voz")
        return f"Completo: {cant(it['contado'])}, igual al sistema." if not c["ciego"] else "Completo, igual al sistema."
    if acc == "danado":
        it = marcar_danados(con, conteo_id, producto_id, n or 1, usuario_id)
        return f"{cant(n or 1)} marcado como dañado. Van {cant(it['danados'])} dañados."
    if acc == "ubicacion" and orden.get("ubicacion"):
        con.execute("INSERT INTO existencias (producto_id, sede_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
                    (producto_id, c["sede_id"]))
        con.execute("UPDATE existencias SET ubicacion = ? WHERE producto_id = ? AND sede_id = ?",
                    (orden["ubicacion"], producto_id, c["sede_id"]))
        return f"Ubicación guardada: {orden['ubicacion']}."
    if acc == "deshacer":
        return deshacer(con, conteo_id, producto_id)
    if acc == "nota":
        if not item(con, conteo_id, producto_id):
            return "Primero cuenta el producto; luego agrega la nota."
        con.execute("UPDATE conteo_items SET nota = TRIM(nota || ' ' || ?) WHERE conteo_id = ? AND producto_id = ?",
                    (texto, conteo_id, producto_id))
        return "Nota guardada."
    if acc == "consulta":
        sede = orden.get("sede")
        if sede:
            fila = uno(con, """SELECT s.nombre, COALESCE(e.cantidad, 0) AS cantidad FROM sedes s
                               LEFT JOIN existencias e ON e.sede_id = s.id AND e.producto_id = ?
                               WHERE LOWER(s.nombre) LIKE ?""", producto_id, f"%{sede.lower()}%")
            if fila:
                return f"En la sede {fila['nombre']} hay {cant(fila['cantidad'])} {p['unidad']}."
            return f"No encontré la sede {sede}."
        if c["ciego"]:
            return "Es un conteo ciego: no te digo lo que dice el sistema."
        it = item(con, conteo_id, producto_id)
        contado = f"has contado {cant(it['contado'])}" if it else "aún no lo has contado"
        return f"El sistema dice {cant(existencia(con, producto_id, c['sede_id']))} y {contado}."
    otros = otros_productos_mencionados(con, texto, producto_id)
    if otros:
        return f"Mencionaste otro producto ({', '.join(otros)}). Escanea cada producto y dime solo su cantidad."
    if not texto.strip():
        return "No te escuché, repite por favor."
    if re.search(r"\b(agrega|crea|nuevo producto)\b", normalizar(texto)):
        return "Por voz solo registro conteos. Los productos nuevos se crean en el menú Productos."
    return "No entendí, repite por favor."
