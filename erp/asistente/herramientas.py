"""Lo que el asistente sabe hacer. Cada herramienta consulta la base y redacta la respuesta.

Las cifras salen siempre de aquí, nunca del modelo de lenguaje. Las herramientas que
cambian datos solo arman una propuesta; se aplica cuando la persona confirma.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from erp import auth, caja as caja_mod, cartera, compras, inventario, productos, reportes
from erp.asistente.buscar import buscar_nombre, nombre_catalogo, resolver_producto
from erp.db import ErrorNegocio, ahora, auditar, hoy, todos, uno, valor
from erp.formato import cant, fecha, pct, pesos
from erp.asistente.hablado import pesos_voz
from voz.comandos import normalizar, normalizar_ubicacion

SINGULAR = {"unidades": "unidad", "metros": "metro", "kilos": "kilo", "bultos": "bulto", "galones": "galón",
            "cajas": "caja", "paquetes": "paquete", "rollos": "rollo"}
TIPOS_MOV = {"venta": "Venta", "compra": "Compra", "traslado_salida": "Traslado enviado", "traslado_entrada": "Traslado recibido",
             "ajuste": "Ajuste", "conteo": "Ajuste por conteo", "danado": "Dañado", "devolucion": "Devolución",
             "anulacion_venta": "Venta anulada", "inicial": "Existencia inicial"}
MESES_LARGOS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
                "noviembre", "diciembre")
ORIGEN = " (con el asistente)"


@dataclass
class Sesion:
    con: object
    usuario: dict
    sede_id: int

    def puede(self, permiso: str) -> bool:
        return auth.puede(self.usuario, permiso)

    @property
    def sede_nombre(self) -> str:
        return valor(self.con, "SELECT nombre FROM sedes WHERE id = ?", self.sede_id, defecto="")

    @property
    def gestion(self) -> bool:
        return self.puede("panel")


def respuesta(texto: str, voz: str | None = None, **extra) -> dict:
    return {"texto": texto, "voz": voz if voz is not None else texto, **extra}


def fila(titulo: str, valor_: str = "", detalle: str = "", url: str | None = None, estado: str | None = None) -> dict:
    return {"titulo": titulo, "valor": valor_, "detalle": detalle, "url": url, "estado": estado}


FEMENINAS = {"unidades", "cajas"}


def con_unidad(cantidad: float, unidad: str) -> str:
    if cantidad == 1:
        return f"{'una' if unidad in FEMENINAS else 'un'} {SINGULAR.get(unidad, unidad)}"
    return f"{cant(cantidad)} {SINGULAR.get(unidad, unidad) if abs(cantidad) == 1 else unidad}"


def lista_y(nombres: list[str]) -> str:
    return nombres[0] if len(nombres) == 1 else ", ".join(nombres[:-1]) + " y " + nombres[-1]


# --- Sedes y periodos ------------------------------------------------------------

def sedes_activas(con) -> list[dict]:
    return todos(con, "SELECT id, nombre FROM sedes WHERE activa = 1 ORDER BY id")


def sede_pedida(s: Sesion, nombre: str | None, reporte: bool = False) -> tuple[int | None, str]:
    """(sede_id o None para todas, cómo nombrarla). En reportes, cada quien ve solo lo que su rol permite."""
    todas_permitido = s.usuario["todas_las_sedes"] or not reporte
    if not nombre:
        if reporte and s.usuario["todas_las_sedes"]:
            return None, "todas las sedes"
        return s.sede_id, f"la sede {s.sede_nombre}"
    if nombre == "todas":
        return (None, "todas las sedes") if todas_permitido else (s.sede_id, f"la sede {s.sede_nombre}")
    for sede in sedes_activas(s.con):
        if normalizar(sede["nombre"]) == normalizar(nombre):
            if reporte and not s.usuario["todas_las_sedes"] and sede["id"] != s.sede_id:
                raise ErrorNegocio(f"Tu usuario solo ve los reportes de la sede {s.sede_nombre}.")
            return sede["id"], f"la sede {sede['nombre']}"
    raise ErrorNegocio(f"No conozco la sede «{nombre}».")


def rango(periodo: str | None, defecto: str = "hoy") -> tuple[str, str, str]:
    """(desde, hasta, etiqueta) con fechas inclusivas."""
    h = date.fromisoformat(hoy())
    p = periodo or defecto
    if p == "hoy":
        return h.isoformat(), h.isoformat(), "Hoy"
    if p == "ayer":
        a = h - timedelta(days=1)
        return a.isoformat(), a.isoformat(), "Ayer"
    if p == "semana":
        return (h - timedelta(days=6)).isoformat(), h.isoformat(), "En los últimos 7 días"
    if p == "mes":
        return h.replace(day=1).isoformat(), h.isoformat(), f"En lo que va de {MESES_LARGOS[h.month - 1]}"
    if p == "mes_pasado":
        fin = h.replace(day=1) - timedelta(days=1)
        return fin.replace(day=1).isoformat(), fin.isoformat(), f"En {MESES_LARGOS[fin.month - 1]}"
    return (h - timedelta(days=29)).isoformat(), h.isoformat(), "En los últimos 30 días"


# --- Productos ---------------------------------------------------------------------

def producto_completo(con, producto_id: int) -> dict:
    return uno(con, """SELECT p.*, c.nombre AS categoria, pr.nombre AS proveedor FROM productos p
                       LEFT JOIN categorias c ON c.id = p.categoria_id LEFT JOIN proveedores pr ON pr.id = p.proveedor_id
                       WHERE p.id = ?""", producto_id)


def _existencia_sede(con, producto_id: int, sede_id: int) -> dict:
    return uno(con, """SELECT COALESCE(e.cantidad, 0) AS cantidad, COALESCE(e.minimo, 0) AS minimo,
                              COALESCE(e.maximo, 0) AS maximo, COALESCE(e.ubicacion, '') AS ubicacion
                       FROM sedes s LEFT JOIN existencias e ON e.sede_id = s.id AND e.producto_id = ? WHERE s.id = ?""",
               producto_id, sede_id)


def no_encontrado(s: Sesion, texto: str) -> dict:
    extra = " Si es nuevo, dime «registra un producto nuevo» con el nombre y el precio." if s.puede("productos.editar") else ""
    return respuesta(f"No encontré «{texto}» en el catálogo.{extra}", tipo="aviso",
                     enlaces=[{"texto": "Buscar en productos", "url": f"/productos?q={texto}"}])


def existencia(s: Sesion, i: dict, prods: list[dict]) -> dict:
    sede_id, sede_txt = sede_pedida(s, i.get("sede"))
    if len(prods) > 1:
        filas = []
        for p in prods[:8]:
            if sede_id:
                e = _existencia_sede(s.con, p["id"], sede_id)
                filas.append(fila(p["nombre"], con_unidad(e["cantidad"], p["unidad"]), e["ubicacion"], f"/productos/{p['id']}",
                                  "critico" if e["minimo"] and e["cantidad"] <= 0 else "aviso" if e["minimo"] and e["cantidad"] <= e["minimo"] else None))
            else:
                total = valor(s.con, "SELECT SUM(cantidad) FROM existencias WHERE producto_id = ?", p["id"], defecto=0)
                filas.append(fila(p["nombre"], con_unidad(total, p["unidad"]), "todas las sedes", f"/productos/{p['id']}"))
        voz = ". ".join(f"{f['titulo']}: {f['valor']}" for f in filas[:4])
        return respuesta(f"Encontré {len(prods)} productos parecidos en {sede_txt}:", f"Encontré {len(prods)}. {voz}.", filas=filas)
    p = producto_completo(s.con, prods[0]["id"])
    por_sede = inventario.stock_por_sede(s.con, p["id"])
    if sede_id is None:
        total = sum(x["cantidad"] for x in por_sede)
        filas = [fila(f"Sede {x['sede']}", con_unidad(x["cantidad"], p["unidad"]), x["ubicacion"], None,
                      "aviso" if x["minimo"] and x["cantidad"] <= x["minimo"] else None) for x in por_sede]
        partes = ", ".join(f"{x['sede']} {cant(x['cantidad'])}" for x in por_sede)
        return respuesta(f"{p['nombre']}: {con_unidad(total, p['unidad'])} entre todas las sedes. Precio {pesos(p['precio'])}.",
                         f"Hay {con_unidad(total, p['unidad'])} de {p['nombre']} en total: {partes}.", filas=filas,
                         foco=p["id"], enlaces=[{"texto": "Ver producto", "url": f"/productos/{p['id']}"}])
    aqui = next((x for x in por_sede if x["sede_id"] == sede_id), {"cantidad": 0, "minimo": 0, "ubicacion": ""})
    donde = f" (ubicación: {aqui['ubicacion']})" if aqui["ubicacion"] else ""
    texto = f"{p['nombre']}: hay {con_unidad(aqui['cantidad'], p['unidad'])} en {sede_txt}{donde}. Precio {pesos(p['precio'])}."
    voz = f"Hay {con_unidad(aqui['cantidad'], p['unidad'])} de {p['nombre']} en {sede_txt}"
    voz += f", en {aqui['ubicacion']}." if aqui["ubicacion"] else "."
    if aqui["minimo"] and aqui["cantidad"] <= aqui["minimo"]:
        alerta = "Está agotado." if aqui["cantidad"] <= 0 else f"Está bajo el mínimo ({cant(aqui['minimo'])})."
        texto += " " + alerta
        voz += " " + alerta
    otras = [x for x in por_sede if x["sede_id"] != sede_id]
    if otras:
        texto += " En otras sedes: " + ", ".join(f"{x['sede']} {cant(x['cantidad'])}" for x in otras) + "."
        if aqui["cantidad"] <= 0 and any(x["cantidad"] > 0 for x in otras):
            con_stock = [x for x in otras if x["cantidad"] > 0]
            voz += f" En {lista_y([x['sede'] for x in con_stock])} sí hay."
    return respuesta(texto, voz, foco=p["id"], enlaces=[{"texto": "Ver producto", "url": f"/productos/{p['id']}"}])


def info_producto(s: Sesion, i: dict, prods: list[dict]) -> dict:
    if len(prods) > 1:
        filas = [fila(p["nombre"], pesos(valor(s.con, "SELECT precio FROM productos WHERE id = ?", p["id"])), p["codigo"],
                      f"/productos/{p['id']}") for p in prods[:8]]
        voz = ". ".join(f"{f['titulo']}: {f['valor']}" for f in filas[:4])
        return respuesta(f"Encontré {len(prods)} productos parecidos:", f"Encontré {len(prods)}. {voz}.", filas=filas)
    p = producto_completo(s.con, prods[0]["id"])
    e = _existencia_sede(s.con, p["id"], s.sede_id)
    vendido = valor(s.con, """SELECT COALESCE(SUM(-cantidad), 0) FROM movimientos WHERE producto_id = ? AND tipo = 'venta'
                              AND fecha >= datetime('now', 'localtime', '-30 days')""", p["id"], defecto=0)
    lineas = [f"Precio de venta: {pesos(p['precio'])} (IVA {p['iva']} % incluido)", f"Código: {p['codigo']}",
              f"Categoría: {p['categoria'] or 'sin categoría'} · Proveedor: {p['proveedor'] or 'sin proveedor'}",
              f"Existencia en {s.sede_nombre}: {con_unidad(e['cantidad'], p['unidad'])}"
              + (f" · ubicación {e['ubicacion']}" if e["ubicacion"] else ""),
              f"Vendido en 30 días (todas las sedes): {con_unidad(vendido, p['unidad'])}"]
    voz = f"{p['nombre']} vale {pesos(p['precio'])}. Hay {con_unidad(e['cantidad'], p['unidad'])} en {s.sede_nombre}."
    if s.puede("productos.editar"):
        base = p["precio"] / (1 + p["iva"] / 100)
        margen = 100 * (base - p["costo"]) / base if base and p["costo"] else None
        lineas.insert(1, f"Costo promedio: {pesos(p['costo'])}" + (f" · margen {pct(margen, 1)}" if margen is not None else ""))
        if p["proveedor"]:
            voz += f" Lo surte {p['proveedor']}."
    return respuesta(f"{p['nombre']}:\n" + "\n".join(lineas), voz, foco=p["id"],
                     enlaces=[{"texto": "Ver producto", "url": f"/productos/{p['id']}"}])


def bajo_minimo(s: Sesion, i: dict, solo_agotados: bool = False) -> dict:
    sede_id, sede_txt = sede_pedida(s, i.get("sede"))
    filas_bd = inventario.bajo_minimo(s.con, sede_id)
    agotados = [f for f in filas_bd if f["cantidad"] <= 0]
    lista = agotados if solo_agotados else filas_bd
    if not lista:
        texto = f"No hay productos agotados en {sede_txt}." if solo_agotados else f"Todo está por encima del mínimo en {sede_txt}."
        return respuesta(texto, tipo="ok")
    filas = [fila(f["nombre"], f"{cant(f['cantidad'])} de mín. {cant(f['minimo'])}",
                  (f"{f['sede']} · " if sede_id is None else "") + f"pedir {con_unidad(f['sugerido'], f['unidad'])}"
                  + (f" · {f['proveedor']}" if f["proveedor"] else ""), f"/productos/{f['id']}",
                  "critico" if f["cantidad"] <= 0 else "aviso") for f in lista[:12]]
    nombres = [f["nombre"] for f in lista[:3]]
    if solo_agotados:
        texto = f"En {sede_txt} hay {len(agotados)} productos agotados."
        voz = f"Hay {len(agotados)} productos agotados en {sede_txt}: {lista_y(nombres)}."
    else:
        texto = f"En {sede_txt} hay {len(filas_bd)} productos en o bajo el mínimo; {len(agotados)} están agotados."
        voz = f"Hay {len(filas_bd)} productos bajo el mínimo en {sede_txt}. Los más urgentes: {lista_y(nombres)}."
    if len(lista) > 12:
        texto += " Te muestro los 12 más urgentes."
    enlaces = [{"texto": "Ver todos", "url": "/inventario?filtro=" + ("agotados" if solo_agotados else "bajo")}]
    if s.puede("compras.gestionar"):
        enlaces.append({"texto": "Armar pedido", "url": "/compras/nueva"})
    return respuesta(texto, voz, filas=filas, enlaces=enlaces)


def pedido(s: Sesion, i: dict) -> dict:
    grupos = [g for g in compras.sugerido_por_proveedor(s.con, s.sede_id)]
    if not grupos:
        return respuesta(f"No hay nada bajo el mínimo en {s.sede_nombre}; no hace falta pedir.", tipo="ok")
    enlaces = [{"texto": "Armar la orden", "url": "/compras/nueva"}] if s.puede("compras.gestionar") else []
    if i.get("proveedor"):
        encontrados = buscar_nombre(grupos, i["proveedor"], "proveedor")
        if not encontrados:
            nombres = [g["proveedor"] for g in grupos]
            return respuesta(f"No hay pedido sugerido para «{i['proveedor']}». Sí hay para: {', '.join(nombres)}.",
                             f"No hay nada pendiente con ese proveedor. Sí hay para {lista_y(nombres[:3])}.", tipo="aviso")
        g = encontrados[0]
        filas = [fila(p["nombre"], f"pedir {con_unidad(p['sugerido'], p['unidad'])}",
                      f"hay {cant(p['cantidad'])}, mín. {cant(p['minimo'])}", f"/productos/{p['id']}",
                      "critico" if p["cantidad"] <= 0 else "aviso") for p in g["items"]]
        return respuesta(f"Pedido sugerido para {g['proveedor']} ({s.sede_nombre}): {len(g['items'])} productos, unos {pesos(g['total'])} al costo.",
                         f"Para {g['proveedor']} sugiero pedir {len(g['items'])} productos, unos {pesos_voz(g['total'])} al costo.",
                         filas=filas, enlaces=enlaces)
    filas = [fila(g["proveedor"], pesos(g["total"]), f"{len(g['items'])} productos") for g in grupos]
    total = sum(g["total"] for g in grupos)
    return respuesta(f"Pedido sugerido en {s.sede_nombre}: {sum(len(g['items']) for g in grupos)} productos de {len(grupos)} proveedores, "
                     f"unos {pesos(total)} al costo. Pregúntame por un proveedor para ver el detalle.",
                     f"Hay que pedirle a {len(grupos)} proveedores, unos {pesos_voz(total)} en total. El más grande es {grupos[0]['proveedor']}.",
                     filas=filas, enlaces=enlaces)


def ventas(s: Sesion, i: dict) -> dict:
    sede_id, sede_txt = sede_pedida(s, i.get("sede"), reporte=True)
    desde, hasta, etiqueta = rango(i.get("periodo"))
    r = reportes.ventas_periodo(s.con, sede_id, desde, hasta)
    verbo = "van" if hasta == hoy() else "se vendieron"
    texto = f"{etiqueta} {verbo} {pesos(r['total'])} en {r['n']} ventas ({sede_txt})."
    if r["n"]:
        texto += f" Ticket promedio {pesos(r['ticket'])}; utilidad bruta {pesos(r['utilidad'])}" + (
            f", margen {pct(r['margen'], 1)}." if r["margen"] is not None else ".")
    voz = f"{etiqueta} {verbo} {pesos_voz(r['total'])} en {r['n']} ventas."
    if (i.get("periodo") or "hoy") == "hoy":
        hace = (date.fromisoformat(hoy()) - timedelta(days=7)).isoformat()
        antes = reportes.ventas_periodo(s.con, sede_id, hace, hace, ahora()[11:19])["total"]
        if antes:
            cambio = 100 * (r["total"] - antes) / antes
            texto += f" El mismo día de la semana pasada a esta hora: {pesos(antes)} ({'+' if cambio >= 0 else ''}{pct(cambio, 0)})."
            voz += f" {'Más' if cambio >= 0 else 'Menos'} que la semana pasada a esta hora."
    filas = []
    if sede_id is None and r["n"]:
        filas = [fila(f"Sede {x['nombre']}", pesos(x["total"]), f"{x['n']} ventas") for x in reportes.ventas_por(s.con, "sede", None, desde, hasta)]
    return respuesta(texto, voz, filas=filas, enlaces=[{"texto": "Ver reporte", "url": f"/reportes?desde={desde}&hasta={hasta}"}])


def mas_vendidos(s: Sesion, i: dict) -> dict:
    sede_id, sede_txt = sede_pedida(s, i.get("sede"), reporte=True)
    desde, hasta, etiqueta = rango(i.get("periodo"), defecto="30_dias")
    top = reportes.top_productos(s.con, sede_id, desde, hasta, 10)
    if not top:
        return respuesta(f"{etiqueta} no hay ventas en {sede_txt}.", tipo="aviso")
    filas = [fila(t["nombre"], pesos(t["total"]), con_unidad(t["cantidad"], t["unidad"]), f"/productos/{t['id']}") for t in top]
    return respuesta(f"{etiqueta}, lo más vendido en {sede_txt} (por valor):",
                     f"{etiqueta}, lo que más se vende es {lista_y([t['nombre'] for t in top[:3]])}.", filas=filas,
                     enlaces=[{"texto": "Ver reporte", "url": f"/reportes?vista=productos&desde={desde}&hasta={hasta}"}])


def quietos(s: Sesion, i: dict) -> dict:
    sede_id, sede_txt = sede_pedida(s, None, reporte=True)
    filas_bd = reportes.sin_movimiento(s.con, sede_id, 60)
    if not filas_bd:
        return respuesta(f"Todo lo que hay en {sede_txt} se ha vendido en los últimos 60 días.", tipo="ok")
    total = sum(f["valor"] or 0 for f in filas_bd)
    filas = [fila(f["nombre"], pesos(f["valor"]), f"{f['sede']} · hay {cant(f['cantidad'])} · última venta "
                  + (fecha(f["ultima_venta"], hora=False) if f["ultima_venta"] else "nunca"), f"/productos/{f['id']}") for f in filas_bd[:10]]
    return respuesta(f"{len(filas_bd)} productos llevan 60 días sin venderse en {sede_txt}: {pesos(total)} quietos al costo. Los que más pesan:",
                     f"Hay {len(filas_bd)} productos sin ventas en dos meses, unos {pesos_voz(total)} quietos. El que más pesa es {filas_bd[0]['nombre']}.",
                     filas=filas, enlaces=[{"texto": "Ver reporte", "url": "/reportes?vista=inventario"}])


def cartera_(s: Sesion, i: dict) -> dict:
    if i.get("cliente"):
        clientes = todos(s.con, "SELECT id, nombre, documento, telefono, cupo_credito FROM clientes WHERE activo = 1")
        encontrados = buscar_nombre(clientes, i["cliente"])
        if not encontrados:
            return respuesta(f"No encontré al cliente «{i['cliente']}».", tipo="aviso",
                             enlaces=[{"texto": "Buscar clientes", "url": f"/clientes?q={i['cliente']}"}])
        if len(encontrados) > 1:
            return respuesta("Encontré varios clientes con ese nombre:", "Encontré varios clientes con ese nombre; mira la lista.",
                             filas=[fila(c["nombre"], c["documento"], c["telefono"] or "", f"/clientes/{c['id']}") for c in encontrados[:6]])
        c = encontrados[0]
        cuentas = cartera.cuentas_por_cobrar(s.con, cliente_id=c["id"])
        if not cuentas:
            return respuesta(f"{c['nombre']} está al día: no debe nada.", tipo="ok",
                             enlaces=[{"texto": "Ver cliente", "url": f"/clientes/{c['id']}"}])
        saldo = sum(x["saldo"] for x in cuentas)
        vencido = sum(x["saldo"] for x in cuentas if x["dias_vencido"] > 0)
        peor = max(cuentas, key=lambda x: x["dias_vencido"])
        texto = f"{c['nombre']} debe {pesos(saldo)} en {len(cuentas)} facturas."
        voz = f"{c['nombre']} debe {pesos_voz(saldo)}."
        if vencido:
            texto += f" Vencido: {pesos(vencido)}; la más atrasada lleva {peor['dias_vencido']} días."
            voz += f" De eso, {pesos_voz(vencido)} está vencido."
        filas = [fila(x["numero"], pesos(x["saldo"]), f"vence {fecha(x['vence'], hora=False)}" + (f" · {x['dias_vencido']} días vencida" if x["dias_vencido"] > 0 else ""),
                      f"/ventas/{x['id']}", "critico" if x["dias_vencido"] > 60 else "aviso" if x["dias_vencido"] > 0 else None) for x in cuentas[:10]]
        return respuesta(texto, voz, filas=filas, enlaces=[{"texto": "Ver cliente y abonar", "url": f"/clientes/{c['id']}"}])
    e = cartera.por_edades(s.con)
    if not e["total"]:
        return respuesta("Nadie debe nada: la cartera está en cero.", tipo="ok")
    por_cliente: dict = {}
    for x in e["cuentas"]:
        d = por_cliente.setdefault(x["cliente_id"], {"nombre": x["cliente"], "saldo": 0, "vencido": 0, "id": x["cliente_id"]})
        d["saldo"] += x["saldo"]
        d["vencido"] += x["saldo"] if x["dias_vencido"] > 0 else 0
    orden = sorted(por_cliente.values(), key=lambda d: (-d["vencido"], -d["saldo"]))
    filas = [fila(d["nombre"], pesos(d["saldo"]), f"vencido {pesos(d['vencido'])}" if d["vencido"] else "al día",
                  f"/clientes/{d['id']}", "aviso" if d["vencido"] else None) for d in orden[:8]]
    return respuesta(f"Los clientes deben {pesos(e['total'])}; vencido {pesos(e['vencido'])} ({len(por_cliente)} clientes).",
                     f"Te deben {pesos_voz(e['total'])}, y {pesos_voz(e['vencido'])} está vencido. El que más debe vencido es {orden[0]['nombre']}.",
                     filas=filas, enlaces=[{"texto": "Ver cartera", "url": "/clientes?vista=cartera"}])


def pagos(s: Sesion, i: dict) -> dict:
    sede_id = None if s.usuario["todas_las_sedes"] else s.sede_id
    cxp = compras.cuentas_por_pagar(s.con, sede_id)
    if not cxp:
        return respuesta("No hay facturas de proveedores pendientes de pago.", tipo="ok")
    total = sum(c["total"] for c in cxp)
    vencidas = [c for c in cxp if c["dias"] is not None and c["dias"] < 0]
    semana = [c for c in cxp if c["dias"] is not None and 0 <= c["dias"] <= 7]
    texto = f"Se les debe {pesos(total)} a proveedores en {len(cxp)} facturas."
    voz = f"Se les debe {pesos_voz(total)} a proveedores."
    if vencidas:
        texto += f" Vencidas: {len(vencidas)} por {pesos(sum(c['total'] for c in vencidas))}."
        voz += f" Hay {len(vencidas)} facturas vencidas."
    if semana:
        texto += f" Vencen esta semana: {pesos(sum(c['total'] for c in semana))}."
        voz += f" Esta semana vencen {pesos_voz(sum(c['total'] for c in semana))}."

    def detalle(c):
        if c["dias"] is None:
            return c["sede"]
        return f"{c['sede']} · " + (f"vencida hace {-c['dias']} días" if c["dias"] < 0 else "vence hoy" if c["dias"] == 0 else f"vence en {c['dias']} días")
    filas = [fila(f"{c['proveedor']} · {c['numero']}", pesos(c["total"]), detalle(c), f"/compras/{c['id']}",
                  "critico" if c["dias"] is not None and c["dias"] < 0 else "aviso" if c["dias"] is not None and c["dias"] <= 7 else None)
             for c in cxp[:10]]
    return respuesta(texto, voz, filas=filas, enlaces=[{"texto": "Ver cuentas por pagar", "url": "/compras?vista=por_pagar"}])


def movimientos(s: Sesion, i: dict, p: dict) -> dict:
    movs = todos(s.con, """SELECT m.*, u.nombre AS usuario FROM movimientos m LEFT JOIN usuarios u ON u.id = m.usuario_id
                           WHERE m.producto_id = ? AND m.sede_id = ? ORDER BY m.id DESC LIMIT 8""", p["id"], s.sede_id)
    if not movs:
        return respuesta(f"{p['nombre']} no tiene movimientos en {s.sede_nombre}.", foco=p["id"])
    filas = [fila(TIPOS_MOV.get(m["tipo"], m["tipo"]), cant(m["cantidad"], signo=True),
                  f"{fecha(m['fecha'])} · quedó en {cant(m['saldo'])}" + (f" · {m['usuario']}" if m["usuario"] else ""))
             for m in movs]
    ultimo = movs[0]
    return respuesta(f"Últimos movimientos de {p['nombre']} en {s.sede_nombre}:",
                     f"Lo último de {p['nombre']}: {TIPOS_MOV.get(ultimo['tipo'], ultimo['tipo']).lower()} de {cant(abs(ultimo['cantidad']))} "
                     f"el {fecha(ultimo['fecha'])}. Ahora hay {cant(ultimo['saldo'])}.",
                     filas=filas, foco=p["id"], enlaces=[{"texto": "Ver kárdex completo", "url": f"/productos/{p['id']}"}])


def pendientes(s: Sesion, i: dict) -> dict:
    lineas: list[tuple[str, str]] = []  # (texto, url)
    sede = s.sede_id
    if s.gestion:
        sede_rep = None if s.usuario["todas_las_sedes"] else s.sede_id
        k = reportes.kpis(s.con, sede_rep)
        donde = "todas las sedes" if sede_rep is None else f"la sede {s.sede_nombre}"
        lineas.append((f"Ventas de hoy en {donde}: {pesos(k['hoy']['total'])} en {k['hoy']['n']} ventas.", "/panel"))
        if k["agotados"]:
            lineas.append((f"{k['agotados']} productos agotados y {k['bajo_minimo']} bajo el mínimo.", "/inventario?filtro=bajo"))
        elif k["bajo_minimo"]:
            lineas.append((f"{k['bajo_minimo']} productos bajo el mínimo.", "/inventario?filtro=bajo"))
        if k["cartera_vencida"]:
            lineas.append((f"Cartera vencida: {pesos(k['cartera_vencida'])}.", "/clientes?vista=cartera"))
        if k["cxp_7dias"]:
            lineas.append((f"Pagos a proveedores en los próximos 7 días: {pesos(k['cxp_7dias'])}"
                           + (f" ({k['cxp_vencidas']} facturas ya vencidas)." if k["cxp_vencidas"] else "."), "/compras?vista=por_pagar"))
        if k["traslados_transito"]:
            lineas.append((f"{k['traslados_transito']} traslados en camino.", "/traslados"))
        if k["compras_por_recibir"]:
            lineas.append((f"{k['compras_por_recibir']} órdenes de compra por recibir.", "/compras?vista=por_recibir"))
        if k["conteos_abiertos"]:
            lineas.append((f"{k['conteos_abiertos']} conteos abiertos por terminar o aprobar.", "/conteos"))
        if k["descuadres"]:
            lineas.append((f"{len(k['descuadres'])} cierres de caja con diferencia esta semana.", "/caja/historial"))
    else:
        if s.puede("conteos"):
            n = valor(s.con, "SELECT COUNT(*) FROM conteos WHERE sede_id = ? AND estado = 'abierto'", sede, defecto=0)
            if n:
                lineas.append((f"{n} conteos abiertos en {s.sede_nombre}.", "/conteos"))
        if s.puede("traslados"):
            n = valor(s.con, "SELECT COUNT(*) FROM traslados WHERE destino_id = ? AND estado = 'en_transito'", sede, defecto=0)
            if n:
                lineas.append((f"{n} traslados por recibir.", "/traslados"))
        if s.puede("compras.recibir"):
            n = valor(s.con, "SELECT COUNT(*) FROM compras WHERE sede_id = ? AND estado IN ('enviada', 'parcial')", sede, defecto=0)
            if n:
                lineas.append((f"{n} órdenes de compra por recibir.", "/compras?vista=por_recibir"))
        if s.puede("caja"):
            c = caja_mod.caja_abierta(s.con, sede, s.usuario["id"])
            lineas.append(("Tu caja está abierta." if c else "No has abierto caja hoy.", "/caja"))
        agot = valor(s.con, """SELECT COUNT(*) FROM existencias e JOIN productos p ON p.id = e.producto_id AND p.activo = 1
                               WHERE e.sede_id = ? AND e.minimo > 0 AND e.cantidad <= 0""", sede, defecto=0)
        if agot:
            lineas.append((f"{agot} productos agotados en {s.sede_nombre}.", "/inventario?filtro=agotados"))
    if not lineas:
        return respuesta("No hay nada pendiente. Todo al día.", tipo="ok")
    filas = [fila(t, url=u) for t, u in lineas]
    return respuesta("Esto es lo que hay:", " ".join(t for t, _ in lineas[:3]), filas=filas)


def caja(s: Sesion, i: dict) -> dict:
    c = caja_mod.caja_abierta(s.con, s.sede_id, s.usuario["id"])
    filas = []
    if c:
        r = caja_mod.resumen(s.con, c["id"])
        pm = r["por_medio"]
        texto = (f"Tu caja (abierta desde {fecha(c['abierta'])}) debería tener {pesos(r['esperado'])} en efectivo. "
                 f"Ventas: {r['ventas']}; efectivo {pesos(pm['efectivo'])}, tarjeta {pesos(pm['tarjeta'])}, "
                 f"transferencia {pesos(pm['transferencia'])}, crédito {pesos(pm['credito'])}.")
        voz = f"En tu caja debería haber {pesos_voz(r['esperado'])} en efectivo, con {r['ventas']} ventas."
    else:
        texto = f"No tienes una caja abierta en {s.sede_nombre}."
        voz = texto
    if s.gestion:
        abiertas = todos(s.con, """SELECT c.id, u.nombre AS usuario FROM cajas c JOIN usuarios u ON u.id = c.usuario_id
                                   WHERE c.estado = 'abierta' AND c.sede_id = ? AND c.usuario_id <> ?""", s.sede_id, s.usuario["id"])
        for a in abiertas:
            filas.append(fila(f"Caja de {a['usuario']}", pesos(caja_mod.resumen(s.con, a["id"])["esperado"]), "efectivo esperado", f"/caja/{a['id']}"))
        if abiertas:
            texto += f" Hay {len(abiertas)} cajas más abiertas en {s.sede_nombre}."
    return respuesta(texto, voz, filas=filas, enlaces=[{"texto": "Ir a caja", "url": "/caja"}])


def ayuda(s: Sesion) -> dict:
    ejemplos = ["«¿Cuánto cemento gris hay?»", "«¿Dónde está el thinner?»", "«¿A cómo está el taladro percutor?»"]
    if s.puede("inventario.ver"):
        ejemplos.append("«¿Qué está bajo el mínimo?»")
    if s.puede("compras.ver"):
        ejemplos.append("«Arma el pedido para Eléctricos del Valle»")
    if s.puede("reportes"):
        ejemplos += ["«¿Cuánto vendimos hoy?»", "«¿Qué es lo que más se vende este mes?»"]
    if s.puede("clientes"):
        ejemplos.append("«¿Quién nos debe?»")
    if s.puede("inventario.ajustar"):
        ejemplos += ["«Llegaron 20 bultos de cemento gris»", "«Se rompieron dos bombillos de 9 vatios»"]
    if s.puede("productos.editar"):
        ejemplos += ["«Registra un producto nuevo: silicona negra, precio 15.500»", "«Sube el precio de la arena a 9.500»"]
    ejemplos.append("«Recuerda que el proveedor de cemento entrega los martes»")
    return respuesta("Te ayudo con el inventario y el negocio. Pregúntame cosas como:\n" + "\n".join(ejemplos)
                     + "\nAntes de cambiar cualquier dato te pido confirmación.",
                     "Pregúntame por existencias, precios, lo que falta, ventas, cartera o pedidos. También registro productos, "
                     "entradas y dañados, siempre con tu confirmación.")


# --- Cambios: propuestas que se aplican al confirmar ---------------------------------------------

ETIQUETA_FALTA = {"cantidad": "la cantidad", "precio": "el precio de venta", "ubicacion": "la ubicación",
                  "minimo": "el mínimo o el máximo", "nombre": "el nombre"}


def preguntar(campo: str, p: dict | None = None, accion: str = "") -> str:
    unidad = p["unidad"] if p else "unidades"
    return {
        "cantidad": {"entrada": f"¿Cuántas {unidad} llegaron?", "ajustar": f"¿En cuántas {unidad} lo dejo?",
                     "danado": f"¿Cuántas {unidad} se dañaron?"}.get(accion, "¿Cuántas?"),
        "precio": "¿A qué precio lo vas a vender?",
        "ubicacion": "¿Dónde queda? Por ejemplo, pasillo 3 estante B.",
        "minimo": "¿Qué mínimo y qué máximo le pongo?",
        "nombre": "¿Cómo se llama el producto?",
    }[campo]


def proponer(s: Sesion, accion: str, i: dict, p: dict | None, texto_usuario: str) -> dict:
    """Arma la propuesta (o pregunta lo que falta). No guarda nada."""
    if accion == "crear_producto":
        return proponer_producto(s, i, texto_usuario)
    sede = s.sede_nombre
    e = _existencia_sede(s.con, p["id"], s.sede_id)
    base = {"accion": accion, "producto_id": p["id"], "producto": p["nombre"], "unidad": p["unidad"], "sede_id": s.sede_id}
    avisos = []
    if accion in ("entrada", "ajustar", "danado"):
        n = i.get("cantidad")
        if n is None or n < 0 or (n == 0 and accion != "ajustar"):
            return {"falta": "cantidad", "pregunta": preguntar("cantidad", p, accion), "base": base}
        if accion == "entrada":
            titulo = f"Sumar {con_unidad(n, p['unidad'])} a {p['nombre']}"
            lineas = [f"Sede {sede}: {cant(e['cantidad'])} → {cant(e['cantidad'] + n)} {p['unidad']}"]
            motivo = i.get("motivo") or "Entrada de mercancía"
            avisos.append("Si llegó con una orden de compra, mejor recíbela en Compras para que actualice el costo.")
        elif accion == "ajustar":
            titulo = f"Dejar {p['nombre']} en {con_unidad(n, p['unidad'])}"
            lineas = [f"Sede {sede}: {cant(e['cantidad'])} → {cant(n)} ({cant(n - e['cantidad'], signo=True)})"]
            motivo = i.get("motivo") or f"Ajuste pedido al asistente: «{texto_usuario}»"
        else:
            if n > e["cantidad"]:
                raise ErrorNegocio(f"En {sede} solo hay {con_unidad(e['cantidad'], p['unidad'])} de {p['nombre']}; no puedo sacar {cant(n)}.")
            titulo = f"Sacar {con_unidad(n, p['unidad'])} de {p['nombre']} por daño"
            lineas = [f"Sede {sede}: {cant(e['cantidad'])} → {cant(e['cantidad'] - n)} {p['unidad']}"]
            motivo = i.get("motivo") or "Dañado"
        lineas.append(f"Motivo: {motivo}")
        return {**base, "cantidad": n, "motivo": motivo, "titulo": titulo, "lineas": lineas, "avisos": avisos}
    if accion == "precio":
        n = i.get("precio")
        if not n or n <= 0:
            return {"falta": "precio", "pregunta": "¿Cuál es el nuevo precio de venta?", "base": base}
        actual = valor(s.con, "SELECT precio FROM productos WHERE id = ?", p["id"])
        costo = valor(s.con, "SELECT costo FROM productos WHERE id = ?", p["id"], defecto=0)
        if costo and n < costo:
            avisos.append(f"Ojo: queda por debajo del costo ({pesos(costo)}).")
        if actual and (n / actual >= 3 or n / actual <= 1 / 3):
            avisos.append(f"Ojo: es un cambio muy grande frente al precio actual ({pesos(actual)}). Revisa los ceros.")
        return {**base, "precio": round(n), "titulo": f"Cambiar el precio de {p['nombre']}",
                "lineas": [f"{pesos(actual)} → {pesos(n)} (para todas las sedes)"], "avisos": avisos}
    if accion == "minimo":
        mn, mx = i.get("minimo"), i.get("maximo")
        if mn is None and mx is None:
            return {"falta": "minimo", "pregunta": preguntar("minimo"), "base": base}
        nuevo_mn = e["minimo"] if mn is None else mn
        nuevo_mx = e["maximo"] if mx is None else mx
        if nuevo_mx and nuevo_mx < nuevo_mn:
            raise ErrorNegocio(f"El máximo ({cant(nuevo_mx)}) no puede ser menor que el mínimo ({cant(nuevo_mn)}).")
        return {**base, "minimo": mn, "maximo": mx, "titulo": f"Mínimo y máximo de {p['nombre']} en {sede}",
                "lineas": [f"Mínimo {cant(e['minimo'])} → {cant(nuevo_mn)}", f"Máximo {cant(e['maximo'])} → {cant(nuevo_mx)}"], "avisos": []}
    if accion == "ubicar":
        ub = normalizar_ubicacion(i.get("ubicacion")) if i.get("ubicacion") else None
        if not ub:
            return {"falta": "ubicacion", "pregunta": preguntar("ubicacion"), "base": base}
        return {**base, "ubicacion": ub, "titulo": f"Ubicación de {p['nombre']} en {sede}",
                "lineas": [f"«{e['ubicacion'] or 'sin ubicación'}» → «{ub}»"], "avisos": []}
    raise ErrorNegocio("No sé hacer ese cambio.")


def _parecido_existente(con, nombre: str) -> dict | None:
    r = resolver_producto(con, nombre)
    return r["candidatos"][0] if r["candidatos"] and r["candidatos"][0]["puntaje"] >= 0.6 else None


def proponer_producto(s: Sesion, i: dict, texto_usuario: str, borrador: dict | None = None) -> dict:
    d = dict(borrador or {})
    for campo in ("nombre", "precio", "costo", "unidad", "categoria", "codigo", "cantidad", "minimo", "maximo", "iva"):
        if i.get(campo) not in (None, ""):
            d[campo] = i[campo]
    base = {"accion": "crear_producto", "borrador": d}
    if not d.get("nombre"):
        return {"falta": "nombre", "pregunta": preguntar("nombre"), "base": base}
    d["nombre"] = nombre_catalogo(d["nombre"])
    parecido = _parecido_existente(s.con, d["nombre"])
    categorias = todos(s.con, "SELECT id, nombre FROM categorias ORDER BY nombre")
    proveedores_ = todos(s.con, "SELECT id, nombre FROM proveedores WHERE activo = 1 ORDER BY nombre")
    if d.get("categoria") and not d.get("categoria_id"):
        cat = buscar_nombre(categorias, d["categoria"])
        if cat:
            d["categoria_id"] = cat[0]["id"]
    ref = producto_completo(s.con, parecido["id"]) if parecido else None
    if ref:
        if not d.get("categoria_id"):
            d["categoria_id"] = ref["categoria_id"]
        if not d.get("proveedor_id"):
            d["proveedor_id"] = ref["proveedor_id"]
        if not d.get("unidad"):
            d["unidad"] = ref["unidad"]
    d.setdefault("unidad", "unidades")
    if d.get("unidad") not in productos.UNIDADES:
        d["unidad"] = "unidades"
    if d.get("iva") not in (0, 5, 19):
        d["iva"] = 19
    avisos = []
    if ref and parecido["puntaje"] >= 0.99 and parecido["cobertura"] >= 0.99:
        avisos.append(f"Ya existe «{ref['nombre']}» (código {ref['codigo']}). Revisa que no sea el mismo.")
    if d.get("codigo"):
        otro = uno(s.con, "SELECT nombre FROM productos WHERE codigo = ?", str(d["codigo"]))
        if otro:
            avisos.append(f"El código {d['codigo']} ya es de «{otro['nombre']}».")
    if d.get("precio") and d.get("costo") and d["precio"] < d["costo"]:
        avisos.append("Ojo: el precio de venta es menor que el costo.")
    if d.get("precio") and ref and ref["precio"] and (d["precio"] / ref["precio"] >= 10 or d["precio"] / ref["precio"] <= 0.1):
        avisos.append(f"Ojo: el parecido «{ref['nombre']}» vale {pesos(ref['precio'])}. Revisa los ceros del precio.")
    propuesta = {
        **base, "titulo": f"Registrar producto nuevo: {d['nombre']}", "avisos": avisos,
        "campos": [
            {"clave": "nombre", "etiqueta": "Nombre", "valor": d["nombre"], "tipo": "texto"},
            {"clave": "precio", "etiqueta": "Precio de venta (con IVA)", "valor": d.get("precio"), "tipo": "pesos"},
            {"clave": "costo", "etiqueta": "Costo", "valor": d.get("costo"), "tipo": "pesos"},
            {"clave": "unidad", "etiqueta": "Unidad", "valor": d["unidad"], "tipo": "opciones",
             "opciones": [{"valor": u, "texto": u} for u in productos.UNIDADES]},
            {"clave": "categoria_id", "etiqueta": "Categoría", "valor": d.get("categoria_id"), "tipo": "opciones",
             "opciones": [{"valor": None, "texto": "Sin categoría"}] + [{"valor": c["id"], "texto": c["nombre"]} for c in categorias]},
            {"clave": "proveedor_id", "etiqueta": "Proveedor", "valor": d.get("proveedor_id"), "tipo": "opciones",
             "opciones": [{"valor": None, "texto": "Sin proveedor"}] + [{"valor": p["id"], "texto": p["nombre"]} for p in proveedores_]},
            {"clave": "codigo", "etiqueta": "Código de barras", "valor": d.get("codigo"), "tipo": "codigo",
             "ayuda": "Automático si se deja vacío"},
            {"clave": "iva", "etiqueta": "IVA", "valor": d["iva"], "tipo": "opciones",
             "opciones": [{"valor": 19, "texto": "19 %"}, {"valor": 5, "texto": "5 %"}, {"valor": 0, "texto": "Exento (0 %)"}]},
            {"clave": "cantidad", "etiqueta": f"Existencia inicial en {s.sede_nombre}", "valor": d.get("cantidad"), "tipo": "numero"},
            {"clave": "minimo", "etiqueta": "Mínimo", "valor": d.get("minimo"), "tipo": "numero"},
            {"clave": "maximo", "etiqueta": "Máximo", "valor": d.get("maximo"), "tipo": "numero"},
        ],
    }
    propuesta["lineas"] = [f"Precio {pesos(d['precio']) if d.get('precio') else 'sin definir'}"
                           + (f" · costo {pesos(d['costo'])}" if d.get("costo") else "")
                           + f" · {d['unidad']}" + (f" · llegan {cant(d['cantidad'])}" if d.get("cantidad") else "")]
    if not d.get("precio"):
        return {**propuesta, "falta": "precio", "pregunta": preguntar("precio")}
    return propuesta


def aplicar(s: Sesion, prop: dict, campos: dict | None = None) -> dict:
    """Ejecuta la propuesta confirmada. Debe llamarse dentro de una transacción."""
    a = prop["accion"]
    if a == "crear_producto":
        d = {**prop["borrador"], **{k: v for k, v in (campos or {}).items()}}
        if not str(d.get("nombre") or "").strip():
            raise ErrorNegocio("Falta el nombre del producto.")
        if not d.get("precio"):
            raise ErrorNegocio("Falta el precio de venta.")
        codigo = str(d.get("codigo") or "").strip() or productos.codigo_interno(s.con)
        pid = productos.crear(
            s.con, codigo=codigo, nombre=str(d["nombre"]), precio=float(d["precio"]), costo=float(d.get("costo") or 0),
            unidad=d.get("unidad") or "unidades", iva=int(d.get("iva") if d.get("iva") is not None else 19),
            categoria_id=int(d["categoria_id"]) if d.get("categoria_id") else None,
            proveedor_id=int(d["proveedor_id"]) if d.get("proveedor_id") else None,
            minimo=float(d.get("minimo") or 0), maximo=float(d.get("maximo") or 0), inicial=float(d.get("cantidad") or 0),
            sede_id=s.sede_id, usuario_id=s.usuario["id"], origen=ORIGEN)
        texto = f"Listo: registré «{d['nombre']}» con el código {codigo}, a {pesos(float(d['precio']))}."
        voz = f"Listo: registré {d['nombre']} a {pesos(float(d['precio']))}."
        if d.get("cantidad"):
            extra = f" Quedan {cant(float(d['cantidad']))} {d.get('unidad') or 'unidades'} en {s.sede_nombre}."
            texto, voz = texto + extra, voz + extra
        return respuesta(texto, voz, foco=pid, tipo="ok", enlaces=[{"texto": "Ver producto", "url": f"/productos/{pid}"}])
    pid, sede = prop["producto_id"], prop["sede_id"]
    if sede != s.sede_id:
        raise ErrorNegocio("Cambiaste de sede; vuelve a pedir el cambio.")
    nombre = prop["producto"]
    enlaces = [{"texto": "Ver producto", "url": f"/productos/{pid}"}]
    if a == "entrada":
        saldo = inventario.mover(s.con, pid, sede, "ajuste", prop["cantidad"], usuario_id=s.usuario["id"], nota=prop["motivo"] + ORIGEN)
        auditar(s.con, s.usuario["id"], sede, "inventario.ajuste",
                f"{nombre}: {cant(prop['cantidad'], signo=True)} → {cant(saldo)}. Motivo: {prop['motivo']}{ORIGEN}")
        return respuesta(f"Listo: sumé {con_unidad(prop['cantidad'], prop['unidad'])} a {nombre}. Ahora hay {cant(saldo)} en {s.sede_nombre}.",
                         foco=pid, tipo="ok", enlaces=enlaces)
    if a == "ajustar":
        saldo = inventario.ajustar(s.con, pid, sede, prop["cantidad"], prop["motivo"] + ORIGEN, s.usuario["id"])
        return respuesta(f"Listo: {nombre} quedó en {cant(saldo)} en {s.sede_nombre}.", foco=pid, tipo="ok", enlaces=enlaces)
    if a == "danado":
        inventario.registrar_danado(s.con, pid, sede, prop["cantidad"], prop["motivo"] + ORIGEN, s.usuario["id"])
        saldo = inventario.existencia(s.con, pid, sede)
        return respuesta(f"Listo: saqué {con_unidad(prop['cantidad'], prop['unidad'])} de {nombre} por daño. Quedan {cant(saldo)}.",
                         foco=pid, tipo="ok", enlaces=enlaces)
    if a == "precio":
        r = productos.cambiar_precio(s.con, pid, prop["precio"], s.usuario["id"], sede, ORIGEN)
        return respuesta(f"Listo: {nombre} ahora vale {pesos(r['despues'])} (antes {pesos(r['antes'])}).", foco=pid, tipo="ok", enlaces=enlaces)
    if a == "minimo":
        productos.guardar_existencia(s.con, pid, sede, minimo=prop["minimo"], maximo=prop["maximo"], usuario_id=s.usuario["id"], origen=ORIGEN)
        e = _existencia_sede(s.con, pid, sede)
        return respuesta(f"Listo: {nombre} en {s.sede_nombre} queda con mínimo {cant(e['minimo'])} y máximo {cant(e['maximo'])}.",
                         foco=pid, tipo="ok", enlaces=enlaces)
    if a == "ubicar":
        productos.guardar_existencia(s.con, pid, sede, ubicacion=prop["ubicacion"], usuario_id=s.usuario["id"], origen=ORIGEN)
        return respuesta(f"Listo: {nombre} queda en «{prop['ubicacion']}».", foco=pid, tipo="ok", enlaces=enlaces)
    raise ErrorNegocio("No sé aplicar ese cambio.")


# Quién puede pedir cada cosa (None = cualquiera con sesión)
PERMISOS = {
    "existencia": "inventario.ver", "info_producto": "productos.ver", "bajo_minimo": "inventario.ver", "agotados": "inventario.ver",
    "pedido": "compras.ver", "ventas": "reportes", "mas_vendidos": "reportes", "quietos": "reportes", "cartera": "clientes",
    "pagos": "compras.gestionar", "movimientos": "inventario.ver", "pendientes": None, "caja": "caja",
    "crear_producto": "productos.editar", "entrada": "inventario.ajustar", "ajustar": "inventario.ajustar",
    "danado": None, "precio": "productos.editar", "minimo": "inventario.ajustar", "ubicar": None,
}
SIN_PERMISO = {
    "ventas": "Las cifras de ventas las ve la administración.", "mas_vendidos": "Los reportes de ventas los ve la administración.",
    "quietos": "Ese reporte lo ve la administración.", "pagos": "Los pagos a proveedores los maneja la administración.",
    "entrada": "Las entradas sin orden de compra las registra la administración. Si llegó con orden, recíbela en Compras.",
    "ajustar": "Los ajustes de inventario los hace la administración. Si contaste distinto, abre un conteo.",
    "crear_producto": "Los productos nuevos los registra la administración.",
    "precio": "Los precios los cambia la administración.", "minimo": "Los mínimos y máximos los cambia la administración.",
    "caja": "Tu usuario no maneja caja.", "cartera": "La cartera de clientes la manejan caja y administración.",
    "pedido": "Los pedidos los maneja bodega y administración.",
}


def permitido(s: Sesion, accion: str) -> bool:
    if accion == "danado":
        return s.usuario["rol"] != "cajero"
    if accion == "ubicar":
        return s.puede("inventario.ajustar") or s.usuario["rol"] == "bodeguero"
    permiso = PERMISOS.get(accion)
    return permiso is None or s.puede(permiso)


def mensaje_sin_permiso(accion: str) -> str:
    return SIN_PERMISO.get(accion, "Tu usuario no tiene permiso para eso.")

