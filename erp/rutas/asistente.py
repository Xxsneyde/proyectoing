"""Pantalla del asistente y su API: preguntas por texto o voz, botones de confirmar/elegir y la memoria."""

import base64
import json
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, UploadFile

from erp.asistente.herramientas import Sesion, ayuda
import erp.db
from erp.db import ErrorNegocio, transaccion
from erp.rutas.base import Ctx, numero, requiere

router = APIRouter()
_sin_motor = threading.Lock()
PROMPT_WHISPER = ("Asistente de inventario de una ferretería en Colombia. ¿Cuánto cemento gris hay? ¿Qué falta en la sede Norte? "
                  "Llegaron veinte bultos. Registra un producto nuevo a treinta y ocho mil pesos. Tubo PVC de media pulgada, "
                  "cable THHN, puntillas, brochas, galones, thinner. Ventas de hoy, cartera, proveedor, pedido, mínimo, máximo.")
CAMPOS_NUMERO = ("precio", "costo", "cantidad", "minimo", "maximo")


def _sesion(ctx: Ctx) -> Sesion:
    return Sesion(ctx.con, ctx.usuario, ctx.sede_id)


def _bloqueo(ctx: Ctx):
    motor = ctx.request.app.state.motor_voz
    return motor.bloqueo if motor else _sin_motor


def _estado_pantalla(ctx: Ctx) -> dict:
    a = ctx.request.app.state.asistente
    s = _sesion(ctx)
    pendiente = a._estado(s).get("pendiente") or {}
    return {"mensajes": a.mensajes(s), "pendiente_id": pendiente.get("mensaje_id"),
            "notas": [{**n, "mia": n["usuario_id"] == ctx.usuario["id"]} for n in a.notas(ctx.con)]}


def _registrar(ctx: Ctx, oi: str, salida: dict, t_voz: float | None, t_total: float):
    asistente = salida.get("asistente") or {}
    with open(erp.db.RUTA_DB.parent / "asistente_registro.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"hora": datetime.now().isoformat(timespec="seconds"), "usuario": ctx.usuario["usuario"],
                            "oi": oi, "intencion": salida.get("metricas", {}).get("intencion"), "respuesta": asistente.get("texto"),
                            "voz_ms": round(t_voz * 1000) if t_voz is not None else None,
                            "llm_ms": salida.get("metricas", {}).get("total_s") and round(salida["metricas"]["total_s"] * 1000),
                            "total_ms": round(t_total * 1000)}, ensure_ascii=False) + "\n")


def _responder(ctx: Ctx, salida: dict, hablar: bool, extra: dict | None = None) -> dict:
    motor = ctx.request.app.state.motor_voz
    respuesta = {**_estado_pantalla(ctx), **(extra or {})}
    voz = (salida.get("asistente") or {}).get("voz")
    if hablar and motor and voz:
        wav, _ = motor.sintetizar(voz)
        respuesta["audio"] = base64.b64encode(wav).decode()
    return respuesta


@router.get("/asistente")
def pantalla(ctx: Ctx = Depends(requiere("asistente"))):
    a = ctx.request.app.state.asistente
    s = _sesion(ctx)
    return ctx.render("asistente/chat.html", inicial=json.dumps(_estado_pantalla(ctx), ensure_ascii=False),
                      activo=a.cerebro is not None, voz=ctx.request.app.state.motor_voz is not None,
                      ayuda=ayuda(s)["texto"].split("\n")[1:-1])


@router.post("/api/asistente/mensaje")
def mensaje(datos: dict = Body(...), ctx: Ctx = Depends(requiere("asistente"))):
    texto = str(datos.get("texto", ""))[:500]
    t0 = time.perf_counter()
    with _bloqueo(ctx):
        salida = ctx.request.app.state.asistente.procesar(_sesion(ctx), texto)
        respuesta = _responder(ctx, salida, bool(datos.get("hablar")))
    _registrar(ctx, texto, salida, None, time.perf_counter() - t0)
    return respuesta


@router.post("/api/asistente/voz")
def voz(audio: UploadFile = File(...), hablar: str = Form("1"), ctx: Ctx = Depends(requiere("asistente"))):
    motor = ctx.request.app.state.motor_voz
    if not motor:
        raise ErrorNegocio("La voz no está activa en este servidor. Escribe tu pregunta.")
    t0 = time.perf_counter()
    sufijo = ".m4a" if "mp4" in (audio.content_type or "") else ".webm"
    with tempfile.NamedTemporaryFile(suffix=sufijo) as tmp:
        tmp.write(audio.file.read())
        tmp.flush()
        with _bloqueo(ctx):
            a = ctx.request.app.state.asistente
            foco = a._estado(_sesion(ctx)).get("foco")
            prompt = PROMPT_WHISPER
            if foco:
                nombre = ctx.con.execute("SELECT nombre FROM productos WHERE id = ?", (foco,)).fetchone()
                prompt = f"Producto: {nombre[0]}. {PROMPT_WHISPER}" if nombre else prompt
            texto, t_voz = motor.transcribir(Path(tmp.name), prompt=prompt)
            salida = a.procesar(_sesion(ctx), texto)
            respuesta = _responder(ctx, salida, hablar == "1", {"oi": texto})
    _registrar(ctx, texto, salida, t_voz, time.perf_counter() - t0)
    return respuesta


def _campos(crudos: dict) -> dict:
    """Lo que la persona corrigió en la tarjeta del producto nuevo."""
    limpio = {}
    for clave, v in (crudos or {}).items():
        if clave in CAMPOS_NUMERO:
            limpio[clave] = numero(v, None) if str(v or "").strip() else None
        elif clave in ("categoria_id", "proveedor_id"):
            limpio[clave] = int(v) if str(v or "").strip() not in ("", "null", "None") else None
        elif clave == "iva":
            limpio[clave] = int(v)
        elif clave in ("nombre", "codigo", "unidad"):
            limpio[clave] = str(v or "").strip()
    return limpio


@router.post("/api/asistente/boton")
def boton(datos: dict = Body(...), ctx: Ctx = Depends(requiere("asistente"))):
    with _bloqueo(ctx):
        salida = ctx.request.app.state.asistente.boton(_sesion(ctx), str(datos.get("tipo")), int(datos.get("mensaje_id") or 0),
                                                       opcion=datos.get("opcion"), campos=_campos(datos.get("campos")))
        return _responder(ctx, salida, bool(datos.get("hablar")))


@router.post("/api/asistente/nueva")
def nueva(ctx: Ctx = Depends(requiere("asistente"))):
    with transaccion(ctx.con):
        ctx.request.app.state.asistente.reiniciar(_sesion(ctx))
    return _estado_pantalla(ctx)


@router.post("/api/asistente/notas/{nota_id}/borrar")
def borrar_nota(nota_id: int, ctx: Ctx = Depends(requiere("asistente"))):
    with transaccion(ctx.con):
        ctx.request.app.state.asistente.borrar_nota(_sesion(ctx), nota_id)
    return _estado_pantalla(ctx)
