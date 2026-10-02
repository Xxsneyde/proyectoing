"""Usuarios, contraseñas, sesiones y permisos por rol."""

import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta

from erp.db import ErrorNegocio, ahora, uno

ROLES = {
    "admin": "Dueño",
    "administrador": "Administrador de sede",
    "cajero": "Cajero",
    "bodeguero": "Bodeguero",
}

_TODOS = {"admin", "administrador", "cajero", "bodeguero"}
_GESTION = {"admin", "administrador"}
PERMISOS = {
    "panel": _GESTION,
    "productos.ver": _TODOS,
    "productos.editar": _GESTION,
    "inventario.ver": _TODOS,
    "inventario.ajustar": _GESTION,
    "conteos": {"admin", "administrador", "bodeguero"},
    "conteos.aprobar": _GESTION,
    "ventas": {"admin", "administrador", "cajero"},
    "ventas.anular": _GESTION,
    "caja": {"admin", "administrador", "cajero"},
    "clientes": {"admin", "administrador", "cajero"},
    "compras.ver": {"admin", "administrador", "bodeguero"},
    "compras.gestionar": _GESTION,
    "compras.recibir": {"admin", "administrador", "bodeguero"},
    "traslados": {"admin", "administrador", "bodeguero"},
    "reportes": _GESTION,
    "auditoria": _GESTION,
    "admin": {"admin"},
    "asistente": _TODOS,
}

# En la versión web (Vercel u otro hosting sin disco permanente) cada instancia arma su propia base.
# La sesión va firmada en la cookie para que sobreviva aunque otra instancia atienda la siguiente visita.
SESIONES_FIRMADAS = False

INICIO_POR_ROL = {"admin": "/panel", "administrador": "/panel", "cajero": "/ventas/pos", "bodeguero": "/conteos"}
DURACION_SESION = timedelta(hours=12)
_intentos: dict[str, list[float]] = {}


def puede(usuario: dict | None, permiso: str) -> bool:
    return bool(usuario) and usuario["rol"] in PERMISOS.get(permiso, set())


def hash_clave(clave: str) -> str:
    sal = secrets.token_bytes(16)
    h = hashlib.scrypt(clave.encode(), salt=sal, n=2**14, r=8, p=1)
    return f"scrypt${sal.hex()}${h.hex()}"


def verificar_clave(clave: str, guardado: str) -> bool:
    try:
        _, sal, h = guardado.split("$")
    except ValueError:
        return False
    calculado = hashlib.scrypt(clave.encode(), salt=bytes.fromhex(sal), n=2**14, r=8, p=1)
    return hmac.compare_digest(calculado.hex(), h)


def validar_clave_nueva(clave: str):
    if len(clave) < 8:
        raise ErrorNegocio("La contraseña debe tener al menos 8 caracteres.")


def iniciar_sesion(con, usuario: str, clave: str) -> str:
    """Devuelve el token de sesión. Bloquea 5 minutos tras 5 intentos fallidos."""
    usuario = usuario.strip().lower()
    recientes = [t for t in _intentos.get(usuario, []) if time.time() - t < 300]
    if len(recientes) >= 5:
        raise ErrorNegocio("Demasiados intentos fallidos. Espera 5 minutos.")
    u = uno(con, "SELECT * FROM usuarios WHERE usuario = ? AND activo = 1", usuario)
    if not u or not verificar_clave(clave, u["clave_hash"]):
        _intentos[usuario] = recientes + [time.time()]
        raise ErrorNegocio("Usuario o contraseña incorrectos.")
    _intentos.pop(usuario, None)
    sede = u["sede_id"] or uno(con, "SELECT id FROM sedes WHERE activa = 1 ORDER BY id")["id"]
    expira = (datetime.now() + DURACION_SESION).strftime("%Y-%m-%d %H:%M:%S")
    token = token_firmado(u["id"], sede, expira) if SESIONES_FIRMADAS else secrets.token_urlsafe(32)
    con.execute("INSERT INTO sesiones (token, usuario_id, sede_id, expira) VALUES (?, ?, ?, ?)",
                (token, u["id"], sede, expira))
    con.execute("DELETE FROM sesiones WHERE expira < ?", (ahora(),))
    return token


def _firma(cuerpo: str) -> str:
    secreto = (os.environ.get("ERP_SECRETO") or "ferreteria-version-de-prueba").encode()
    return hmac.new(secreto, cuerpo.encode(), hashlib.sha256).hexdigest()[:40]


def token_firmado(usuario_id: int, sede_id: int, expira: str) -> str:
    cuerpo = f"{usuario_id}.{sede_id}.{expira.replace(' ', 'T')}.{secrets.token_hex(8)}"
    return f"{cuerpo}.{_firma(cuerpo)}"


def _rehidratar(con, token: str):
    """Si la sesión firmada no está en esta base (otra instancia), la vuelve a crear."""
    partes = token.split(".")
    if len(partes) != 5 or not hmac.compare_digest(partes[4], _firma(".".join(partes[:4]))):
        return
    try:
        usuario_id, sede_id, expira = int(partes[0]), int(partes[1]), partes[2].replace("T", " ")
    except ValueError:
        return
    if expira > ahora() and uno(con, "SELECT 1 FROM usuarios WHERE id = ? AND activo = 1", usuario_id) \
            and uno(con, "SELECT 1 FROM sedes WHERE id = ? AND activa = 1", sede_id):
        con.execute("INSERT OR IGNORE INTO sesiones (token, usuario_id, sede_id, expira) VALUES (?, ?, ?, ?)",
                    (token, usuario_id, sede_id, expira))


def usuario_de_sesion(con, token: str | None) -> dict | None:
    if not token:
        return None
    if SESIONES_FIRMADAS and not uno(con, "SELECT 1 FROM sesiones WHERE token = ?", token):
        _rehidratar(con, token)
    fila = uno(con, """
        SELECT u.id, u.nombre, u.usuario, u.rol, u.sede_id AS sede_fija, s.token, s.sede_id,
               se.nombre AS sede
        FROM sesiones s JOIN usuarios u ON u.id = s.usuario_id AND u.activo = 1
        JOIN sedes se ON se.id = s.sede_id
        WHERE s.token = ? AND s.expira > ?""", token, ahora())
    if fila:
        fila["rol_nombre"] = ROLES[fila["rol"]]
        fila["todas_las_sedes"] = fila["sede_fija"] is None
    return fila


def cambiar_sede(con, usuario: dict, sede_id: int):
    if not usuario["todas_las_sedes"] and sede_id != usuario["sede_fija"]:
        raise ErrorNegocio("Tu usuario solo trabaja en su sede.")
    if not uno(con, "SELECT id FROM sedes WHERE id = ? AND activa = 1", sede_id):
        raise ErrorNegocio("La sede no existe.")
    con.execute("UPDATE sesiones SET sede_id = ? WHERE token = ?", (sede_id, usuario["token"]))


def cerrar_sesion(con, token: str):
    con.execute("DELETE FROM sesiones WHERE token = ?", (token,))
