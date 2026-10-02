"""Intérprete de órdenes con un LLM local servido por llama-server (llama.cpp).

Se usa solo cuando las reglas no entienden la frase. La salida está forzada
a un JSON con esquema fijo, así el modelo no puede responder texto libre.
"""

import json
import time

import requests

ESQUEMA = {
    "type": "object",
    "properties": {
        "accion": {"type": "string", "enum": [
            "conteo", "sumar", "restar", "corregir", "ubicacion",
            "danado", "consulta", "deshacer", "nota", "completo", "desconocido",
        ]},
        "cantidad": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "unidad": {"anyOf": [{"type": "string", "enum": [
            "unidades", "cajas", "metros", "kilos", "rollos", "bultos", "galones", "paquetes", "bolsas",
        ]}, {"type": "null"}]},
        "ubicacion": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "sede": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["accion", "cantidad", "unidad", "ubicacion", "sede"],
}

# Los ejemplos del prompt son distintos de las frases de prueba a propósito, y
# no traen ubicaciones ni sedes concretas: los modelos pequeños tienden a copiarlas
SISTEMA = """Eres el intérprete de órdenes de voz del inventario de una ferretería en Colombia. El empleado ya escaneó un producto y dice una orden corta sobre ese producto. Responde SOLO con el JSON.

Acciones:
- conteo: dice cuántas hay ("hay 30", "nueve cajas", "quedan 12 tubos").
- sumar: agrega a lo ya contado ("súmale 7", "pon 5 más", "aparecieron otras 3").
- restar: quita de lo contado ("quítale 2", "descuenta 6").
- corregir: cambia el registro anterior ("corrígelo a 20", "no, eran 45"). Si dice el número equivocado y el correcto, usa el correcto.
- completo: dice que la cantidad está completa o igual al sistema ("están todos", "está completo", "cuadra").
- ubicacion: dice dónde queda el producto (pasillo, estante, bodega...).
- danado: unidades dañadas, rotas, oxidadas o defectuosas ("hay uno roto", "se partieron dos").
- consulta: hace una pregunta sobre existencias o sobre otra sede.
- deshacer: pide expresamente deshacer, borrar o anular el último registro.
- nota: pide anotar una observación.
- desconocido: no es una orden de inventario (saludos, frases sueltas, ruido) o no estás seguro.

Reglas:
- El empleado puede nombrar el producto y sus medidas ("tubo de media pulgada", "cemento de 50 kilos", "puntilla de 2 pulgadas"). Eso describe el producto: NO es la cantidad.
- cantidad en número: "dos metros y medio" = 2.5, "una docena" = 12, "veinte" = 20. Si no dice cantidad, null.
- unidad solo si la dice (unidades, cajas, metros, kilos, rollos, bultos, galones, paquetes, bolsas); si no, null.
- ubicacion y sede: SOLO si la persona las dice; si no, null. Nunca las inventes. ubicacion en minúsculas y con dígitos ("pasillo N estante X"); sede en minúsculas, solo el nombre.
- Ante la duda, usa desconocido: es mejor pedir que repita que registrar algo equivocado."""


class InterpreteLLM:
    def __init__(self, url: str = "http://127.0.0.1:8091", slot: int | None = 0):
        self.url = url.rstrip("/")
        self.slot = slot  # el asistente usa otro: cada uno conserva su prompt en caché

    def disponible(self) -> bool:
        try:
            return requests.get(f"{self.url}/health", timeout=1).status_code == 200
        except requests.RequestException:
            return False

    def interpretar(self, texto: str, producto: dict | None = None) -> tuple[dict, dict]:
        """Devuelve (orden, métricas). producto es el que se acaba de escanear."""
        t0 = time.perf_counter()
        mensaje = texto
        if producto:
            mensaje = (f"Producto escaneado: {producto['nombre']} (se cuenta en {producto['unidad']}).\n"
                       f"Orden: {texto}")
        r = requests.post(f"{self.url}/v1/chat/completions", json={
            "messages": [
                {"role": "system", "content": SISTEMA},
                {"role": "user", "content": mensaje},
            ],
            "temperature": 0,
            "max_tokens": 96,
            "cache_prompt": True,
            "response_format": {"type": "json_schema", "json_schema": {"name": "orden", "schema": ESQUEMA}},
            **({"id_slot": self.slot} if self.slot is not None else {}),
        }, timeout=120)
        r.raise_for_status()
        datos = r.json()
        total = time.perf_counter() - t0
        contenido = datos["choices"][0]["message"]["content"]
        try:
            orden = json.loads(contenido)
        except json.JSONDecodeError:
            orden = {"accion": "desconocido"}
        # Los modelos pequeños a veces escriben "null" como texto
        for campo in ("unidad", "ubicacion", "sede"):
            if str(orden.get(campo)).strip().lower() in ("null", "none", ""):
                orden[campo] = None
        orden["texto"] = texto
        tiempos = datos.get("timings", {})
        return orden, {
            "total_s": total,
            "prompt_tokens": tiempos.get("prompt_n"),
            "prompt_ms": tiempos.get("prompt_ms"),
            "gen_tokens": tiempos.get("predicted_n"),
            "gen_tok_s": tiempos.get("predicted_per_second"),
        }
