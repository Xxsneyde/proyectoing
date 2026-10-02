"""Acceso a la base SQLite local del ERP.

Una conexión por petición (SQLite en modo WAL soporta lectores concurrentes y
un escritor). Las operaciones de negocio usan `transaccion()`: o se guarda
todo o no se guarda nada.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
RUTA_DB = RAIZ.parent / "datos" / "ferreteria.db"


class ErrorNegocio(Exception):
    """Regla de negocio incumplida; el mensaje se muestra tal cual al usuario."""


def conectar(ruta: Path | str | None = None) -> sqlite3.Connection:
    ruta = Path(ruta or RUTA_DB)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ruta, timeout=15, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 15000")
    return con


# Columnas agregadas después de la primera versión: (tabla, columna, definición)
MIGRACIONES = [
    ("conteo_items", "danados_anterior", "REAL"),
    ("conteo_items", "ultima_accion", "TEXT NOT NULL DEFAULT 'contado'"),
]


def inicializar(con: sqlite3.Connection):
    con.executescript((RAIZ / "schema.sql").read_text(encoding="utf-8"))
    for tabla, columna, definicion in MIGRACIONES:
        existentes = {f[1] for f in con.execute(f"PRAGMA table_info({tabla})")}
        if columna not in existentes:
            con.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}")


@contextmanager
def transaccion(con: sqlite3.Connection):
    """BEGIN IMMEDIATE: toma el bloqueo de escritura al inicio y evita carreras
    entre dos cajas que venden el último producto al mismo tiempo."""
    if con.in_transaction:  # transacción anidada: la de afuera decide
        yield con
        return
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def uno(con, sql: str, *params):
    fila = con.execute(sql, params).fetchone()
    return dict(fila) if fila else None


def todos(con, sql: str, *params) -> list[dict]:
    return [dict(f) for f in con.execute(sql, params).fetchall()]


def valor(con, sql: str, *params, defecto=None):
    fila = con.execute(sql, params).fetchone()
    return fila[0] if fila and fila[0] is not None else defecto


_reloj: datetime | None = None  # hora simulada al generar datos de demostración


def fijar_reloj(momento: datetime | None):
    global _reloj
    _reloj = momento


def ahora() -> str:
    return (_reloj or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def hoy() -> str:
    return (_reloj or datetime.now()).strftime("%Y-%m-%d")


def config(con, clave: str, defecto: str = "") -> str:
    return valor(con, "SELECT valor FROM config WHERE clave = ?", clave, defecto=defecto)


def guardar_config(con, clave: str, valor_: str):
    con.execute(
        "INSERT INTO config (clave, valor) VALUES (?, ?) "
        "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor", (clave, valor_),
    )


def consecutivo(con, prefijo: str) -> str:
    """Siguiente número de documento: 'V1-000124'. Debe llamarse dentro de una transacción."""
    clave = f"consecutivo:{prefijo}"
    n = int(config(con, clave, "0")) + 1
    guardar_config(con, clave, str(n))
    return f"{prefijo}-{n:06d}"


def auditar(con, usuario_id: int | None, sede_id: int | None, accion: str, detalle: str = ""):
    con.execute(
        "INSERT INTO auditoria (fecha, usuario_id, sede_id, accion, detalle) VALUES (?, ?, ?, ?, ?)",
        (ahora(), usuario_id, sede_id, accion, detalle),
    )
