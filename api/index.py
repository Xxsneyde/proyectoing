"""Entrada para Vercel: versión de prueba web del ERP (sin IA).

Vercel no tiene disco permanente: cada instancia crea su base en /tmp con los datos de
demostración al arrancar (~2 s). Las sesiones van firmadas en la cookie, así que siguen
válidas aunque otra instancia atienda la siguiente visita. Lo que se registre se pierde
cuando Vercel recicla la instancia.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import erp.db  # noqa: E402

erp.db.RUTA_DB = Path(os.environ.get("ERP_BASE", "/tmp/ferreteria.db"))

from erp.app import crear_app  # noqa: E402
from erp.db import conectar, inicializar, valor  # noqa: E402

_con = conectar()
inicializar(_con)
if not valor(_con, "SELECT COUNT(*) FROM productos", defecto=0):
    from erp.demo import generar

    generar(_con, aviso=lambda *_: None)
_con.close()

app = crear_app(None, nube=True)
