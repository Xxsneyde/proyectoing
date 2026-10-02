"""Cerebro del asistente: el LLM local decide QUÉ quiere el empleado; nunca da cifras.

La salida está forzada a un JSON con una acción y sus datos. El sistema ejecuta la
acción contra la base y redacta la respuesta con los números reales, así el modelo
no puede inventar existencias, precios ni ventas.
"""

import json
import time

import requests

UNIDADES = ("unidades", "metros", "kilos", "bultos", "galones", "cajas", "paquetes", "rollos")
PERIODOS = ("hoy", "ayer", "semana", "mes", "mes_pasado")

# Datos que lleva cada acción. El orden importa poco; todos se piden siempre (null si no aplica)
CAMPOS = {
    "existencia": ["producto", "sede"],
    "info_producto": ["producto"],
    "bajo_minimo": ["sede"],
    "agotados": ["sede"],
    "pedido": ["proveedor"],
    "ventas": ["periodo", "sede"],
    "mas_vendidos": ["periodo", "sede"],
    "quietos": [],
    "cartera": ["cliente"],
    "pagos": [],
    "movimientos": ["producto"],
    "pendientes": [],
    "caja": [],
    "crear_producto": ["nombre", "precio", "costo", "unidad", "categoria", "codigo", "cantidad", "minimo", "maximo", "iva"],
    "entrada": ["producto", "cantidad", "motivo"],
    "ajustar": ["producto", "cantidad", "motivo"],
    "danado": ["producto", "cantidad", "motivo"],
    "precio": ["producto", "precio"],
    "minimo": ["producto", "minimo", "maximo"],
    "ubicar": ["producto", "ubicacion"],
    "recordar": ["texto"],
    "confirmar": [],
    "cancelar": [],
    "elegir": ["numero"],
    "responder": ["texto"],
}

# Sin comillas, llaves ni dos puntos dentro de los textos: los modelos pequeños a veces intentan
# "cerrar" el texto con ' y seguir escribiendo JSON falso adentro ("cemento','precio':33500}...")
_TEXTO = {"anyOf": [{"type": "string", "pattern": "^[^'{}\\[\\]:\"\\\\]{1,80}$"}, {"type": "null"}]}
_NUMERO = {"anyOf": [{"type": "number"}, {"type": "null"}]}


def _tipo(campo: str, sedes: list[str]) -> dict:
    if campo in ("cantidad", "precio", "costo", "minimo", "maximo"):
        return _NUMERO
    if campo == "numero":
        return {"type": "integer"}
    if campo == "iva":
        return {"anyOf": [{"enum": [0, 5, 19]}, {"type": "null"}]}
    if campo == "unidad":
        return {"anyOf": [{"enum": list(UNIDADES)}, {"type": "null"}]}
    if campo == "periodo":
        return {"anyOf": [{"enum": list(PERIODOS)}, {"type": "null"}]}
    if campo == "sede":
        return {"anyOf": [{"enum": sedes + ["todas"]}, {"type": "null"}]}
    if campo == "texto":
        return {"type": "string", "pattern": "^[^{}\\[\\]\"\\\\]{1,300}$"}
    return _TEXTO


def esquema(sedes: list[str]) -> dict:
    ramas = []
    for accion, campos in CAMPOS.items():
        props = {"accion": {"const": accion}, **{c: _tipo(c, sedes) for c in campos}}
        ramas.append({"type": "object", "properties": props, "required": ["accion", *campos]})
    return {"anyOf": ramas}


# Los ejemplos usan productos y nombres que no están en las frases de prueba, para no
# premiar que el modelo las copie
SISTEMA = """Eres el asistente de voz de una ferretería en Colombia. Recibes lo que dice un empleado y eliges UNA acción en JSON. El sistema ejecuta la acción con los datos reales y le contesta al empleado: tú nunca das cifras.

Consultas:
- existencia: cuánto hay de un producto, si hay o dónde está. producto = como lo nombró ("silicona", "broca de un cuarto"). sede si nombra una; "todas" si pide todas las sedes.
- info_producto: precio ("a cómo está", "cuánto vale"), costo, código, margen o quién lo vende (proveedor) de un producto.
- bajo_minimo: qué falta, qué hay que pedir, qué se está acabando.
- agotados: qué productos no tienen existencias.
- pedido: pedido sugerido a un proveedor (proveedor como lo dijo, o null).
- ventas: cuánto se vendió. periodo: hoy, ayer, semana, mes o mes_pasado.
- mas_vendidos: qué es lo que más se vende.
- quietos: qué no se vende, mercancía quieta.
- cartera: quién debe, o cuánto debe un cliente (cliente como lo dijo, o null).
- pagos: qué se les debe a los proveedores, qué hay que pagar.
- movimientos: historial o últimos movimientos de un producto.
- pendientes: resumen del día, cómo vamos, qué hay pendiente.
- caja: cómo va la caja, cuánta plata hay en caja.

Cambios (el sistema pide confirmación antes de guardar):
- crear_producto: registrar un producto nuevo. Llena lo que diga: nombre (solo el nombre, sin precio ni cantidad), precio (de venta), costo, unidad, categoria, codigo (de barras), cantidad (existencia inicial), minimo, maximo, iva (0, 5 o 19). Lo que no diga, null.
- entrada: llegó mercancía o pide sumar o agregar unidades ("súmale 3", "agrégale 10"). cantidad = cuántas se suman.
- ajustar: dejar la existencia en un total exacto ("déjalo en 8", "ajústalo a 12"). cantidad = el total final.
- danado: unidades dañadas, rotas o vencidas que salen del inventario.
- precio: cambiar el precio de venta.
- minimo: cambiar el mínimo o el máximo.
- ubicar: cambiar dónde se guarda un producto.

Otras:
- recordar: pide que recuerdes o anotes algo para después. texto = lo que hay que recordar, completo.
- confirmar o cancelar: dice que sí o que no a algo pendiente de confirmar.
- elegir: escoge una de las opciones numeradas que le diste. numero = la opción.
- responder: saludos, agradecimientos, preguntas generales, dudas sobre cómo usar el sistema, lo que esté en la memoria de la ferretería, o si no entiendes. texto = tu respuesta en español, corta (máximo dos frases), de tú. Nunca inventes existencias, precios ni ventas.

Reglas:
- Números en cifras: "treinta y ocho mil" = 38000, "dos millones" = 2000000, "media docena" = 6, "kilo y medio" = 1.5.
- Las medidas son parte del nombre del producto, no la cantidad: en "llegaron 12 brocas de un cuarto" la cantidad es 12 y el producto "brocas de un cuarto".
- Si se refiere al producto del que venían hablando ("ese", "¿y en el Sur?", "¿y el precio?"), escribe el nombre de ese producto.
- motivo: por qué, si lo dice; si no, null.

Ejemplos:
"¿Tenemos silicona?" -> {"accion":"existencia","producto":"silicona","sede":null}
"Llegaron cuarenta galones de thinner" -> {"accion":"entrada","producto":"galones de thinner","cantidad":40,"motivo":"llegaron"}
"Ponle 12 mil al rodillo" -> {"accion":"precio","producto":"rodillo","precio":12000}
"Gracias, muy amable" -> {"accion":"responder","texto":"Con gusto. ¿Te ayudo con algo más?"}"""


class Cerebro:
    def __init__(self, url: str = "http://127.0.0.1:8091", slot: int | None = None):
        self.url = url.rstrip("/")
        self.slot = slot

    def disponible(self) -> bool:
        try:
            return requests.get(f"{self.url}/health", timeout=1).status_code == 200
        except requests.RequestException:
            return False

    def decidir(self, mensaje: str, contexto: str, historia: list[dict], sedes: list[str]) -> tuple[dict, dict]:
        """Devuelve (intención, métricas). historia: [{"rol": "usuario"|"asistente", "texto": ...}]."""
        t0 = time.perf_counter()
        mensajes = [{"role": "system", "content": SISTEMA + "\n\n" + contexto}]
        for h in historia:
            mensajes.append({"role": "user" if h["rol"] == "usuario" else "assistant", "content": h["texto"]})
        mensajes.append({"role": "user", "content": mensaje})
        cuerpo = {
            "messages": mensajes,
            "temperature": 0,
            "max_tokens": 220,
            "cache_prompt": True,
            "response_format": {"type": "json_schema", "json_schema": {"name": "accion", "schema": esquema(sedes)}},
        }
        if self.slot is not None:
            cuerpo["id_slot"] = self.slot
        r = requests.post(f"{self.url}/v1/chat/completions", json=cuerpo, timeout=180)
        r.raise_for_status()
        datos = r.json()
        try:
            intencion = json.loads(datos["choices"][0]["message"]["content"])
        except (json.JSONDecodeError, KeyError, IndexError):
            intencion = {"accion": "responder", "texto": "No te entendí bien. ¿Me lo repites?"}
        for k, v in list(intencion.items()):
            if isinstance(v, str) and v.strip().lower() in ("null", "none", ""):
                intencion[k] = None
        t = datos.get("timings", {})
        return intencion, {
            "total_s": round(time.perf_counter() - t0, 2),
            "prompt_tokens": t.get("prompt_n"),
            "prompt_ms": round(t.get("prompt_ms") or 0),
            "gen_tokens": t.get("predicted_n"),
            "gen_ms": round(t.get("predicted_ms") or 0),
        }
