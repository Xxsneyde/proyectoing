"""Servidor del ERP para la red local de la sede (PC + celulares por WiFi).

Uso: .venv/bin/python servidor_erp.py [--puerto 8443] [--sin-voz] [--demo] [--base otra.db] [--nube]
  --demo     crea datos de demostración si la base está vacía
  --sin-voz  no carga Whisper ni el LLM (arranca más rápido y usa menos RAM)
  --base     usa otra base de datos (por ejemplo, una copia para pruebas)
  --nube     versión de prueba en internet (Render u otro hosting): sin IA, sin certificado propio
             (el HTTPS lo pone la plataforma), puerto de la variable PORT y datos de demostración
"""

import argparse
import os
import sys
import threading
from pathlib import Path

import uvicorn

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
from erp.app import crear_app  # noqa: E402
from erp.db import conectar, inicializar, valor  # noqa: E402


def precalentar_asistente(url: str):
    """Deja las instrucciones del asistente en la caché del modelo (la primera vez tardan ~18 s)."""
    from erp.asistente.cerebro import Cerebro
    try:
        Cerebro(url, slot=1).decidir("Hola", "Sedes: centro.", [], ["centro"])
    except Exception as exc:  # noqa: BLE001 - si falla, la primera pregunta solo tarda más
        print(f"No se pudo precalentar el asistente: {exc}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--puerto", type=int, default=8443)
    ap.add_argument("--sin-voz", action="store_true")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--llm", default="qwen3-4b")
    ap.add_argument("--base")
    ap.add_argument("--nube", action="store_true")
    a = ap.parse_args()
    if a.nube:
        a.sin_voz = a.demo = True
        a.puerto = int(os.environ.get("PORT", "8000"))
    if a.base:
        import erp.db
        erp.db.RUTA_DB = Path(a.base).resolve()

    con = conectar()
    inicializar(con)
    if a.demo and not valor(con, "SELECT COUNT(*) FROM productos", defecto=0):
        from erp.demo import generar
        print("Generando datos de demostración...")
        generar(con)
    con.close()

    motor = None
    if not a.sin_voz:
        from voz.motor import Motor
        motor = Motor(llm=a.llm)
        if motor.llm:
            threading.Thread(target=precalentar_asistente, args=(motor.llm.url,), daemon=True).start()

    if a.nube:
        print(f"ERP listo en el puerto {a.puerto} (versión de prueba web, sin IA).", flush=True)
        uvicorn.run(crear_app(None, nube=True), host="0.0.0.0", port=a.puerto, proxy_headers=True,
                    forwarded_allow_ips="*", log_level="warning")
        return

    from servidor_movil import asegurar_certificado, ip_local
    ip = ip_local()
    crt, key = asegurar_certificado(ip)
    print(f"\nERP listo. En el PC: https://localhost:{a.puerto}  ·  En el celular (misma WiFi): https://{ip}:{a.puerto}\n",
          flush=True)
    uvicorn.run(crear_app(motor), host="0.0.0.0", port=a.puerto, ssl_certfile=str(crt), ssl_keyfile=str(key),
                log_level="warning")


if __name__ == "__main__":
    main()
