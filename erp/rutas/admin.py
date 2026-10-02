"""Administración: usuarios, sedes, datos de la empresa, respaldo, auditoría y cuenta propia."""

import math
import re
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form
from fastapi.responses import FileResponse, RedirectResponse
from starlette.background import BackgroundTask

from erp import auth
from erp.db import RUTA_DB, ErrorNegocio, auditar, config, guardar_config, todos, transaccion, uno, valor
from erp.reportes import ACCIONES
from erp.rutas.base import Ctx, redirigir, requiere

router = APIRouter()
POR_PAGINA = 60


@router.get("/admin")
def inicio(ctx: Ctx = Depends(requiere("admin"))):
    return RedirectResponse("/admin/usuarios", status_code=303)


# --- Usuarios ----------------------------------------------------------------

@router.get("/admin/usuarios")
def usuarios(ctx: Ctx = Depends(requiere("admin"))):
    filas = todos(ctx.con, """
        SELECT u.*, s.nombre AS sede, (SELECT MAX(creada) FROM sesiones WHERE usuario_id = u.id) AS ultima_sesion
        FROM usuarios u LEFT JOIN sedes s ON s.id = u.sede_id ORDER BY u.activo DESC, u.rol, u.nombre""")
    return ctx.render("admin/usuarios.html", filas=filas, pestana="usuarios",
                      sedes_lista=todos(ctx.con, "SELECT id, nombre FROM sedes WHERE activa = 1 ORDER BY id"))


def _validar_usuario(con, usuario: str, rol: str, sede_id: str, usuario_id=None) -> tuple[str, int | None]:
    usuario = usuario.strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]{3,30}", usuario):
        raise ErrorNegocio("El usuario debe tener de 3 a 30 letras sin tildes, números, punto o guion.")
    if uno(con, "SELECT id FROM usuarios WHERE usuario = ? AND id IS NOT ?", usuario, usuario_id):
        raise ErrorNegocio(f"El usuario «{usuario}» ya existe.")
    if rol not in auth.ROLES:
        raise ErrorNegocio("Rol inválido.")
    if rol == "admin":
        return usuario, None
    if not sede_id:
        raise ErrorNegocio("Elige la sede en la que trabaja.")
    return usuario, int(sede_id)


@router.post("/admin/usuarios")
def crear_usuario(nombre: str = Form(...), usuario: str = Form(...), rol: str = Form(...), sede_id: str = Form(""),
                  clave: str = Form(...), ctx: Ctx = Depends(requiere("admin"))):
    if not nombre.strip():
        raise ErrorNegocio("Escribe el nombre.")
    usuario, sede = _validar_usuario(ctx.con, usuario, rol, sede_id)
    auth.validar_clave_nueva(clave)
    with transaccion(ctx.con):
        ctx.con.execute("INSERT INTO usuarios (nombre, usuario, clave_hash, rol, sede_id) VALUES (?, ?, ?, ?, ?)",
                        (nombre.strip(), usuario, auth.hash_clave(clave), rol, sede))
        auditar(ctx.con, ctx.usuario["id"], ctx.sede_id, "usuario.crear", f"{nombre.strip()} ({usuario}) como {auth.ROLES[rol]}")
    return redirigir(ctx, "/admin/usuarios", f"Usuario {usuario} creado.")


@router.get("/admin/usuarios/{usuario_id}")
def usuario(usuario_id: int, ctx: Ctx = Depends(requiere("admin"))):
    u = uno(ctx.con, "SELECT * FROM usuarios WHERE id = ?", usuario_id)
    if not u:
        raise ErrorNegocio("El usuario no existe.")
    actividad = todos(ctx.con, "SELECT a.*, s.nombre AS sede FROM auditoria a LEFT JOIN sedes s ON s.id = a.sede_id "
                               "WHERE a.usuario_id = ? ORDER BY a.id DESC LIMIT 20", usuario_id)
    for a in actividad:
        a["accion_texto"] = ACCIONES.get(a["accion"], a["accion"])
    return ctx.render("admin/usuario.html", u=u, actividad=actividad, pestana="usuarios",
                      sedes_lista=todos(ctx.con, "SELECT id, nombre FROM sedes WHERE activa = 1 ORDER BY id"))


@router.post("/admin/usuarios/{usuario_id}")
def editar_usuario(usuario_id: int, nombre: str = Form(...), usuario: str = Form(...), rol: str = Form(...),
                   sede_id: str = Form(""), activo: str = Form(""), ctx: Ctx = Depends(requiere("admin"))):
    antes = uno(ctx.con, "SELECT * FROM usuarios WHERE id = ?", usuario_id)
    if not antes:
        raise ErrorNegocio("El usuario no existe.")
    usuario, sede = _validar_usuario(ctx.con, usuario, rol, sede_id, usuario_id)
    activo_n = 1 if activo else 0
    if usuario_id == ctx.usuario["id"] and (not activo_n or rol != "admin"):
        raise ErrorNegocio("No puedes quitarte el acceso de dueño ni desactivarte a ti mismo.")
    admins = valor(ctx.con, "SELECT COUNT(*) FROM usuarios WHERE rol = 'admin' AND activo = 1 AND id <> ?", usuario_id, defecto=0)
    if antes["rol"] == "admin" and (rol != "admin" or not activo_n) and admins == 0:
        raise ErrorNegocio("Debe quedar al menos un usuario dueño activo.")
    with transaccion(ctx.con):
        ctx.con.execute("UPDATE usuarios SET nombre = ?, usuario = ?, rol = ?, sede_id = ?, activo = ? WHERE id = ?",
                        (nombre.strip(), usuario, rol, sede, activo_n, usuario_id))
        if not activo_n or rol != antes["rol"] or sede != antes["sede_id"]:
            ctx.con.execute("DELETE FROM sesiones WHERE usuario_id = ?", (usuario_id,))  # que vuelva a ingresar con sus nuevos permisos
        cambios = []
        if rol != antes["rol"]:
            cambios.append(f"rol {auth.ROLES[antes['rol']]} → {auth.ROLES[rol]}")
        if antes["activo"] and not activo_n:
            cambios.append("desactivado")
        auditar(ctx.con, ctx.usuario["id"], ctx.sede_id, "usuario.editar", f"{usuario}: {', '.join(cambios) or 'datos actualizados'}")
    return redirigir(ctx, f"/admin/usuarios/{usuario_id}", "Usuario actualizado.")


@router.post("/admin/usuarios/{usuario_id}/clave")
def restablecer_clave(usuario_id: int, clave: str = Form(...), ctx: Ctx = Depends(requiere("admin"))):
    u = uno(ctx.con, "SELECT usuario FROM usuarios WHERE id = ?", usuario_id)
    if not u:
        raise ErrorNegocio("El usuario no existe.")
    auth.validar_clave_nueva(clave)
    with transaccion(ctx.con):
        ctx.con.execute("UPDATE usuarios SET clave_hash = ? WHERE id = ?", (auth.hash_clave(clave), usuario_id))
        ctx.con.execute("DELETE FROM sesiones WHERE usuario_id = ? AND token <> ?", (usuario_id, ctx.usuario["token"]))
        auditar(ctx.con, ctx.usuario["id"], ctx.sede_id, "usuario.editar", f"{u['usuario']}: contraseña restablecida")
    return redirigir(ctx, f"/admin/usuarios/{usuario_id}", "Contraseña cambiada; sus sesiones abiertas se cerraron.")


# --- Sedes -------------------------------------------------------------------

@router.get("/admin/sedes")
def sedes(ctx: Ctx = Depends(requiere("admin"))):
    filas = todos(ctx.con, """
        SELECT s.*, (SELECT COUNT(*) FROM usuarios WHERE sede_id = s.id AND activo = 1) AS usuarios,
               (SELECT COALESCE(SUM(MAX(e.cantidad, 0) * p.costo), 0) FROM existencias e JOIN productos p ON p.id = e.producto_id
                WHERE e.sede_id = s.id) AS inventario
        FROM sedes s ORDER BY s.id""")
    return ctx.render("admin/sedes.html", filas=filas, pestana="sedes")


@router.post("/admin/sedes")
def crear_sede(nombre: str = Form(...), direccion: str = Form(""), telefono: str = Form(""), ctx: Ctx = Depends(requiere("admin"))):
    if not nombre.strip():
        raise ErrorNegocio("Escribe el nombre de la sede.")
    if uno(ctx.con, "SELECT id FROM sedes WHERE LOWER(nombre) = ?", nombre.strip().lower()):
        raise ErrorNegocio("Ya existe una sede con ese nombre.")
    with transaccion(ctx.con):
        ctx.con.execute("INSERT INTO sedes (nombre, direccion, telefono) VALUES (?, ?, ?)", (nombre.strip(), direccion.strip(), telefono.strip()))
        auditar(ctx.con, ctx.usuario["id"], ctx.sede_id, "sede.crear", nombre.strip())
    return redirigir(ctx, "/admin/sedes", f"Sede {nombre.strip()} creada. Define mínimos y máximos desde cada producto.")


@router.post("/admin/sedes/{sede_id}")
def editar_sede(sede_id: int, nombre: str = Form(...), direccion: str = Form(""), telefono: str = Form(""),
                activa: str = Form(""), ctx: Ctx = Depends(requiere("admin"))):
    if not activa and valor(ctx.con, "SELECT COUNT(*) FROM sedes WHERE activa = 1 AND id <> ?", sede_id, defecto=0) == 0:
        raise ErrorNegocio("Debe quedar al menos una sede activa.")
    if not activa and valor(ctx.con, "SELECT COUNT(*) FROM cajas WHERE sede_id = ? AND estado = 'abierta'", sede_id, defecto=0):
        raise ErrorNegocio("La sede tiene cajas abiertas; ciérralas antes de desactivarla.")
    with transaccion(ctx.con):
        ctx.con.execute("UPDATE sedes SET nombre = ?, direccion = ?, telefono = ?, activa = ? WHERE id = ?",
                        (nombre.strip(), direccion.strip(), telefono.strip(), 1 if activa else 0, sede_id))
        auditar(ctx.con, ctx.usuario["id"], ctx.sede_id, "sede.editar", nombre.strip() + ("" if activa else " (desactivada)"))
    return redirigir(ctx, "/admin/sedes", "Sede actualizada.")


# --- Empresa, integraciones y respaldo ----------------------------------------

@router.get("/admin/sistema")
def sistema(ctx: Ctx = Depends(requiere("admin"))):
    ruta = Path(RUTA_DB)
    tamano = sum(Path(str(ruta) + s).stat().st_size for s in ("", "-wal") if Path(str(ruta) + s).exists())
    conteos = {t: valor(ctx.con, f"SELECT COUNT(*) FROM {t}", defecto=0) for t in ("productos", "ventas", "movimientos", "auditoria")}
    return ctx.render("admin/sistema.html", pestana="sistema", ruta_db=str(ruta), tamano_mb=round(tamano / 1e6, 1),
                      conteos=conteos, voz=ctx.request.app.state.motor_voz is not None,
                      empresa_nombre=config(ctx.con, "empresa_nombre"), empresa_nit=config(ctx.con, "empresa_nit"),
                      demo=config(ctx.con, "demo") == "1", ultimo_respaldo=config(ctx.con, "ultimo_respaldo"))


@router.post("/admin/sistema")
def guardar_sistema(empresa_nombre: str = Form(...), empresa_nit: str = Form(""), ctx: Ctx = Depends(requiere("admin"))):
    if not empresa_nombre.strip():
        raise ErrorNegocio("Escribe el nombre de la empresa.")
    with transaccion(ctx.con):
        guardar_config(ctx.con, "empresa_nombre", empresa_nombre.strip())
        guardar_config(ctx.con, "empresa_nit", empresa_nit.strip())
        auditar(ctx.con, ctx.usuario["id"], ctx.sede_id, "config.editar", f"Empresa: {empresa_nombre.strip()}")
    return redirigir(ctx, "/admin/sistema", "Datos de la empresa guardados.")


@router.get("/admin/respaldo")
def respaldo(ctx: Ctx = Depends(requiere("admin"))):
    """Copia consistente de la base aunque haya ventas en curso (API de respaldo de SQLite)."""
    momento = datetime.now()
    tmp = Path(tempfile.mkstemp(suffix=".db")[1])
    destino = sqlite3.connect(tmp)
    ctx.con.backup(destino)
    destino.close()
    with transaccion(ctx.con):
        guardar_config(ctx.con, "ultimo_respaldo", momento.strftime("%Y-%m-%d %H:%M:%S"))
        auditar(ctx.con, ctx.usuario["id"], ctx.sede_id, "config.editar", "Respaldo descargado")
    return FileResponse(tmp, filename=f"ferreteria_{momento:%Y-%m-%d_%H%M}.db", media_type="application/octet-stream",
                        background=BackgroundTask(tmp.unlink, missing_ok=True))


# --- Auditoría -----------------------------------------------------------------

@router.get("/auditoria")
def auditoria(usuario: str = "", modulo: str = "", desde: str = "", hasta: str = "", q: str = "", pagina: int = 1,
              ctx: Ctx = Depends(requiere("auditoria"))):
    condiciones, params = [], []
    if not ctx.usuario["todas_las_sedes"]:
        condiciones.append("a.sede_id = ?")
        params.append(ctx.sede_id)
    if usuario.isdigit():
        condiciones.append("a.usuario_id = ?")
        params.append(int(usuario))
    if modulo:
        condiciones.append("a.accion LIKE ?")
        params.append(modulo + ".%")
    if desde:
        condiciones.append("a.fecha >= ?")
        params.append(desde)
    if hasta:
        condiciones.append("a.fecha < date(?, '+1 day')")
        params.append(hasta)
    if q.strip():
        condiciones.append("LOWER(a.detalle) LIKE ?")
        params.append(f"%{q.strip().lower()}%")
    donde = ("WHERE " + " AND ".join(condiciones)) if condiciones else ""
    total = valor(ctx.con, f"SELECT COUNT(*) FROM auditoria a {donde}", *params, defecto=0)
    paginas = max(1, math.ceil(total / POR_PAGINA))
    pagina = min(max(1, pagina), paginas)
    filas = todos(ctx.con, f"""SELECT a.*, u.nombre AS usuario, s.nombre AS sede FROM auditoria a
                               LEFT JOIN usuarios u ON u.id = a.usuario_id LEFT JOIN sedes s ON s.id = a.sede_id
                               {donde} ORDER BY a.id DESC LIMIT ? OFFSET ?""", *params, POR_PAGINA, (pagina - 1) * POR_PAGINA)
    for f in filas:
        f["accion_texto"] = ACCIONES.get(f["accion"], f["accion"])
    modulos = {"venta": "Ventas", "caja": "Caja", "cartera": "Cartera", "compra": "Compras", "traslado": "Traslados",
               "conteo": "Conteos", "inventario": "Inventario", "producto": "Productos", "cliente": "Clientes",
               "proveedor": "Proveedores", "usuario": "Usuarios", "sede": "Sedes", "config": "Configuración"}
    return ctx.render("admin/auditoria.html", filas=filas, total=total, pagina=pagina, paginas=paginas, modulos=modulos,
                      usuario_f=usuario, modulo=modulo, desde=desde, hasta=hasta, q=q,
                      usuarios=todos(ctx.con, "SELECT id, nombre FROM usuarios ORDER BY nombre"))


# --- Cuenta propia --------------------------------------------------------------

@router.get("/cuenta")
def cuenta(ctx: Ctx = Depends(requiere())):
    return ctx.render("admin/cuenta.html")


@router.post("/cuenta/clave")
def cambiar_clave(actual: str = Form(...), nueva: str = Form(...), confirmar: str = Form(...), ctx: Ctx = Depends(requiere())):
    u = uno(ctx.con, "SELECT clave_hash FROM usuarios WHERE id = ?", ctx.usuario["id"])
    if not auth.verificar_clave(actual, u["clave_hash"]):
        raise ErrorNegocio("La contraseña actual no es correcta.")
    if nueva != confirmar:
        raise ErrorNegocio("La nueva contraseña y su confirmación no coinciden.")
    auth.validar_clave_nueva(nueva)
    with transaccion(ctx.con):
        ctx.con.execute("UPDATE usuarios SET clave_hash = ? WHERE id = ?", (auth.hash_clave(nueva), ctx.usuario["id"]))
        ctx.con.execute("DELETE FROM sesiones WHERE usuario_id = ? AND token <> ?", (ctx.usuario["id"], ctx.usuario["token"]))
        auditar(ctx.con, ctx.usuario["id"], ctx.sede_id, "usuario.editar", f"{ctx.usuario['usuario']}: cambió su contraseña")
    return redirigir(ctx, "/cuenta", "Contraseña cambiada. Las sesiones en otros equipos se cerraron.")
