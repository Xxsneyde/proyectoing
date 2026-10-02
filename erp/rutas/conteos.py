"""Toma de inventario: crear conteos, contar con escáner/voz/teclado, revisar diferencias y aprobar."""

import base64
import json
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, UploadFile

from erp import conteos
import erp.db
from erp.db import ErrorNegocio, todos, transaccion, uno, valor
from erp.inventario import existencia
from erp.rutas.base import Ctx, numero, redirigir, requiere
from voz.comandos import interpretar

router = APIRouter()
_sin_motor = threading.Lock()


def _bloqueo(ctx: Ctx):
    motor = ctx.request.app.state.motor_voz
    return motor.bloqueo if motor else _sin_motor


@router.get("/conteos")
def lista(ctx: Ctx = Depends(requiere("conteos"))):
    filas = todos(ctx.con, """
        SELECT c.*, u.nombre AS usuario,
               (SELECT COUNT(*) FROM conteo_items WHERE conteo_id = c.id) AS contados,
               (SELECT COUNT(*) FROM conteo_items WHERE conteo_id = c.id AND ABS(contado - sistema) > 1e-9) AS con_diferencia
        FROM conteos c LEFT JOIN usuarios u ON u.id = c.usuario_id
        WHERE c.sede_id = ? ORDER BY c.estado = 'abierto' DESC, c.id DESC LIMIT 40""", ctx.sede_id)
    exactitud = uno(ctx.con, """
        SELECT COUNT(*) AS n, SUM(ABS(ci.contado - ci.sistema) < 1e-9) AS exactos
        FROM conteo_items ci JOIN conteos c ON c.id = ci.conteo_id
        WHERE c.sede_id = ? AND c.estado = 'aprobado' AND c.aprobado >= datetime('now', 'localtime', '-30 days')""", ctx.sede_id)
    sugeridos = conteos.sugeridos_ciclicos(ctx.con, ctx.sede_id)
    return ctx.render("conteos/lista.html", filas=filas, tipos=conteos.TIPOS, sugeridos=sugeridos,
                      exactitud=round(100 * exactitud["exactos"] / exactitud["n"], 1) if exactitud["n"] else None,
                      categorias=todos(ctx.con, "SELECT * FROM categorias ORDER BY nombre"),
                      ubicaciones=[r["u"] for r in todos(ctx.con, """
                          SELECT DISTINCT CASE WHEN INSTR(ubicacion, ' estante') > 0 THEN SUBSTR(ubicacion, 1, INSTR(ubicacion, ' estante') - 1)
                                 ELSE ubicacion END AS u FROM existencias WHERE sede_id = ? AND ubicacion <> '' ORDER BY u""", ctx.sede_id)],
                      sin_contar=valor(ctx.con, """SELECT COUNT(*) FROM existencias WHERE sede_id = ? AND
                          (ultimo_conteo IS NULL OR ultimo_conteo < datetime('now', 'localtime', '-60 days'))""", ctx.sede_id, defecto=0))


@router.post("/conteos")
def crear(nombre: str = Form(""), tipo: str = Form(...), categoria: str = Form(""), ubicacion: str = Form(""),
          ciego: str = Form(""), ctx: Ctx = Depends(requiere("conteos"))):
    filtro = categoria if tipo == "categoria" else ubicacion if tipo == "ubicacion" else ""
    with transaccion(ctx.con):
        cid = conteos.crear(ctx.con, ctx.sede_id, nombre, tipo, filtro, bool(ciego), ctx.usuario["id"])
    return redirigir(ctx, f"/conteos/{cid}", "Conteo iniciado. Escanea el primer producto.")


def _conteo(ctx: Ctx, conteo_id: int) -> dict:
    c = uno(ctx.con, "SELECT * FROM conteos WHERE id = ?", conteo_id)
    if not c or (c["sede_id"] != ctx.sede_id and not ctx.usuario["todas_las_sedes"]):
        raise ErrorNegocio("El conteo no existe en esta sede.")
    return c


@router.get("/conteos/{conteo_id}")
def contar(conteo_id: int, ctx: Ctx = Depends(requiere("conteos"))):
    c = _conteo(ctx, conteo_id)
    if c["estado"] != "abierto":
        return redirigir(None, f"/conteos/{conteo_id}/resultado")
    return ctx.render("conteos/contar.html", c=c, voz=ctx.request.app.state.motor_voz is not None,
                      estado_inicial=json.dumps(_estado(ctx, c, None)))


@router.get("/conteos/{conteo_id}/resultado")
def resultado(conteo_id: int, ctx: Ctx = Depends(requiere("conteos"))):
    c = _conteo(ctx, conteo_id)
    return ctx.render("conteos/resultado.html", r=conteos.resumen(ctx.con, c["id"]))


@router.post("/conteos/{conteo_id}/aprobar")
def aprobar(conteo_id: int, ctx: Ctx = Depends(requiere("conteos.aprobar"))):
    _conteo(ctx, conteo_id)
    with transaccion(ctx.con):
        r = conteos.aprobar(ctx.con, conteo_id, ctx.usuario["id"])
    return redirigir(ctx, f"/conteos/{conteo_id}/resultado",
                     f"Conteo aprobado: {r['contados']} productos, {r['con_diferencia']} ajustados en el inventario.")


@router.post("/conteos/{conteo_id}/anular")
def anular(conteo_id: int, ctx: Ctx = Depends(requiere("conteos.aprobar"))):
    _conteo(ctx, conteo_id)
    with transaccion(ctx.con):
        conteos.anular(ctx.con, conteo_id, ctx.usuario["id"])
    return redirigir(ctx, "/conteos", "Conteo anulado; el inventario no cambió.")


# --- API de la pantalla de conteo ----------------------------------------

def _estado(ctx: Ctx, c: dict, producto_id: int | None) -> dict:
    """Lo que la pantalla necesita después de cada acción: el producto, su conteo y el avance."""
    r = conteos.resumen(ctx.con, c["id"])
    salida = {
        "avance": {"contados": r["contados"], "total": r["total_alcance"], "porcentaje": r["avance"]},
        "pendientes": [{"id": p["id"], "nombre": p["nombre"], "codigo": p["codigo"], "ubicacion": p["ubicacion"], "unidad": p["unidad"]}
                       for p in r["pendientes"][:12]],
        "producto": None,
    }
    if producto_id:
        p = uno(ctx.con, """SELECT p.id, p.codigo, p.nombre, p.unidad, COALESCE(e.ubicacion, '') AS ubicacion
                            FROM productos p LEFT JOIN existencias e ON e.producto_id = p.id AND e.sede_id = ?
                            WHERE p.id = ?""", c["sede_id"], producto_id)
        it = conteos.item(ctx.con, c["id"], producto_id)
        p["contado"] = it["contado"] if it else None
        p["danados"] = it["danados"] if it else 0
        p["nota"] = it["nota"] if it else ""
        if not c["ciego"]:
            p["sistema"] = it["sistema"] if it else existencia(ctx.con, producto_id, c["sede_id"])
        salida["producto"] = p
    return salida


def _abierto(ctx: Ctx, conteo_id: int) -> dict:
    c = _conteo(ctx, conteo_id)
    if c["estado"] != "abierto":
        raise ErrorNegocio("Este conteo ya está cerrado.")
    return c


@router.get("/api/conteos/{conteo_id}/producto/{producto_id}")
def api_producto(conteo_id: int, producto_id: int, ctx: Ctx = Depends(requiere("conteos"))):
    return _estado(ctx, _abierto(ctx, conteo_id), producto_id)


@router.post("/api/conteos/{conteo_id}/registrar")
def api_registrar(conteo_id: int, datos: dict = Body(...), ctx: Ctx = Depends(requiere("conteos"))):
    c = _abierto(ctx, conteo_id)
    pid = int(datos["producto_id"])
    with transaccion(ctx.con):
        if datos.get("danados"):
            conteos.marcar_danados(ctx.con, conteo_id, pid, numero(datos["danados"]), ctx.usuario["id"])
        else:
            conteos.registrar(ctx.con, conteo_id, pid, numero(datos["cantidad"]), ctx.usuario["id"],
                              datos.get("metodo", "manual"), "sumar" if datos.get("modo") == "sumar" else "reemplazar")
    return _estado(ctx, c, pid)


@router.post("/api/conteos/{conteo_id}/deshacer")
def api_deshacer(conteo_id: int, datos: dict = Body(...), ctx: Ctx = Depends(requiere("conteos"))):
    c = _abierto(ctx, conteo_id)
    with transaccion(ctx.con):
        mensaje = conteos.deshacer(ctx.con, conteo_id, int(datos["producto_id"]))
    return {**_estado(ctx, c, int(datos["producto_id"])), "respuesta": mensaje}


def _ejecutar_orden(ctx: Ctx, c: dict, producto_id: int, texto: str, t_voz: float) -> dict:
    p = uno(ctx.con, "SELECT id, nombre, unidad FROM productos WHERE id = ?", producto_id)
    if not p:
        raise ErrorNegocio("El producto no existe.")
    motor = ctx.request.app.state.motor_voz
    if motor:
        orden, origen, t_int = motor.interpretar(texto, p)
    else:
        orden, origen, t_int = interpretar(texto, p), "reglas", 0.0
    if orden.get("confianza") == "baja" and origen != "LLM":
        # Sin el LLM que la confirme, una frase dudosa no se registra: mejor pedir que repita
        orden = {"accion": "desconocido", "texto": texto}
    with transaccion(ctx.con):
        respuesta = conteos.aplicar_voz(ctx.con, c["id"], producto_id, orden, ctx.usuario["id"], texto)
    audio = None
    if motor:
        wav, _ = motor.sintetizar(respuesta)
        audio = base64.b64encode(wav).decode()
    with open(erp.db.RUTA_DB.parent / "voz_registro.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"hora": datetime.now().isoformat(timespec="seconds"), "usuario": ctx.usuario["usuario"],
                            "conteo": c["id"], "producto": p["nombre"], "oi": texto, "motor": origen,
                            "orden": {k: v for k, v in orden.items() if v is not None and k not in ("texto", "confianza")},
                            "respuesta": respuesta, "voz_ms": round(t_voz * 1000), "interpretar_ms": round(t_int * 1000)},
                           ensure_ascii=False) + "\n")
    return {**_estado(ctx, c, producto_id), "oi": texto, "respuesta": respuesta, "motor": origen, "audio": audio,
            "tiempos": {"voz_ms": round(t_voz * 1000), "interpretar_ms": round(t_int * 1000)}}


@router.post("/api/conteos/{conteo_id}/voz")
def api_voz(conteo_id: int, producto_id: int = Form(...), audio: UploadFile = File(...),
            ctx: Ctx = Depends(requiere("conteos"))):
    c = _abierto(ctx, conteo_id)
    motor = ctx.request.app.state.motor_voz
    if not motor:
        raise ErrorNegocio("El asistente de voz no está activo en este servidor. Escribe la orden.")
    p = uno(ctx.con, "SELECT id, nombre, unidad FROM productos WHERE id = ?", producto_id)
    sufijo = ".m4a" if "mp4" in (audio.content_type or "") else ".webm"
    with tempfile.NamedTemporaryFile(suffix=sufijo) as tmp:
        tmp.write(audio.file.read())
        tmp.flush()
        with _bloqueo(ctx):
            texto, t_voz = motor.transcribir(Path(tmp.name), p)
    with _bloqueo(ctx):
        return _ejecutar_orden(ctx, c, producto_id, texto, t_voz)


@router.post("/api/conteos/{conteo_id}/texto")
def api_texto(conteo_id: int, datos: dict = Body(...), ctx: Ctx = Depends(requiere("conteos"))):
    c = _abierto(ctx, conteo_id)
    with _bloqueo(ctx):
        return _ejecutar_orden(ctx, c, int(datos["producto_id"]), str(datos.get("texto", "")).strip(), 0.0)
