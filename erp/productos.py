"""Catálogo: alta de productos, cambio de precio y datos por sede (mínimo, máximo, ubicación)."""

from erp import inventario
from erp.db import ErrorNegocio, ahora, auditar, consecutivo, uno
from erp.formato import cant, pesos

UNIDADES = ("unidades", "metros", "kilos", "bultos", "galones", "cajas", "paquetes", "rollos")


def validar(con, codigo: str, nombre: str, unidad: str, precio: float, iva: int, producto_id=None):
    if not codigo.strip() or not nombre.strip():
        raise ErrorNegocio("El código y el nombre son obligatorios.")
    if unidad not in UNIDADES:
        raise ErrorNegocio("Unidad inválida.")
    if precio <= 0:
        raise ErrorNegocio("El precio debe ser mayor que cero.")
    if iva not in (0, 5, 19):
        raise ErrorNegocio("El IVA debe ser 0, 5 o 19 %.")
    otro = uno(con, "SELECT id, nombre FROM productos WHERE codigo = ? AND id IS NOT ?", codigo.strip(), producto_id)
    if otro:
        raise ErrorNegocio(f"El código {codigo} ya es de «{otro['nombre']}».")


def codigo_interno(con) -> str:
    """Para lo que no trae código de barras (arena, cable por metro). Dentro de una transacción."""
    return consecutivo(con, "INT")


def crear(con, *, codigo: str, nombre: str, precio: float, sede_id: int, usuario_id: int, unidad: str = "unidades",
          costo: float = 0, iva: int = 19, categoria_id: int | None = None, marca: str = "",
          proveedor_id: int | None = None, minimo: float = 0, maximo: float = 0, ubicacion: str = "",
          inicial: float = 0, origen: str = "") -> int:
    """Crea el producto con su fila de existencias en la sede. Debe llamarse dentro de una transacción."""
    precio = round(precio)
    validar(con, codigo, nombre, unidad, precio, iva)
    if costo < 0 or minimo < 0 or maximo < 0 or inicial < 0:
        raise ErrorNegocio("Costo, mínimo, máximo y existencia inicial no pueden ser negativos.")
    if maximo and maximo < minimo:
        raise ErrorNegocio("El máximo debe ser mayor o igual que el mínimo.")
    pid = con.execute(
        "INSERT INTO productos (codigo, nombre, categoria_id, marca, unidad, costo, precio, iva, proveedor_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (codigo.strip(), nombre.strip(), categoria_id, marca.strip(), unidad, costo, precio, iva, proveedor_id)).lastrowid
    con.execute("INSERT INTO existencias (producto_id, sede_id, minimo, maximo, ubicacion) VALUES (?, ?, ?, ?, ?)",
                (pid, sede_id, minimo, maximo, ubicacion.strip().lower()))
    if inicial > 0:
        inventario.mover(con, pid, sede_id, "inicial", inicial, usuario_id=usuario_id,
                         nota="Existencia inicial al crear el producto")
    auditar(con, usuario_id, sede_id, "producto.crear", f"{nombre.strip()} ({codigo.strip()}) a {pesos(precio)}{origen}")
    return pid


def cambiar_precio(con, producto_id: int, precio: float, usuario_id: int, sede_id: int, origen: str = "") -> dict:
    p = uno(con, "SELECT id, nombre, precio FROM productos WHERE id = ?", producto_id)
    if not p:
        raise ErrorNegocio("El producto no existe.")
    precio = round(precio)
    if precio <= 0:
        raise ErrorNegocio("El precio debe ser mayor que cero.")
    con.execute("UPDATE productos SET precio = ?, actualizado = ? WHERE id = ?", (precio, ahora(), producto_id))
    auditar(con, usuario_id, sede_id, "producto.editar", f"{p['nombre']}: precio {pesos(p['precio'])} → {pesos(precio)}{origen}")
    return {"antes": p["precio"], "despues": precio}


def guardar_existencia(con, producto_id: int, sede_id: int, *, minimo: float | None = None, maximo: float | None = None,
                       ubicacion: str | None = None, usuario_id: int | None = None, origen: str = ""):
    """Cambia solo lo que se pasa; lo demás queda como estaba."""
    con.execute("INSERT INTO existencias (producto_id, sede_id) VALUES (?, ?) ON CONFLICT DO NOTHING", (producto_id, sede_id))
    actual = uno(con, "SELECT minimo, maximo, ubicacion FROM existencias WHERE producto_id = ? AND sede_id = ?",
                 producto_id, sede_id)
    mn = actual["minimo"] if minimo is None else minimo
    mx = actual["maximo"] if maximo is None else maximo
    ub = actual["ubicacion"] if ubicacion is None else ubicacion.strip().lower()
    if mn < 0 or mx < 0 or (mx and mx < mn):
        raise ErrorNegocio("El máximo debe ser mayor o igual que el mínimo.")
    con.execute("UPDATE existencias SET minimo = ?, maximo = ?, ubicacion = ? WHERE producto_id = ? AND sede_id = ?",
                (mn, mx, ub, producto_id, sede_id))
    if usuario_id is not None:
        nombre = uno(con, "SELECT nombre FROM productos WHERE id = ?", producto_id)["nombre"]
        cambios = []
        if (mn, mx) != (actual["minimo"], actual["maximo"]):
            cambios.append(f"mínimo {cant(mn)}, máximo {cant(mx)}")
        if ub != actual["ubicacion"]:
            cambios.append(f"ubicación «{ub}»")
        auditar(con, usuario_id, sede_id, "producto.editar", f"{nombre}: {', '.join(cambios) or 'sin cambios'}{origen}")
