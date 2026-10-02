"""Demo en vivo: inventario por voz con el micrófono del PC.

Simula que el empleado escanea productos. Por cada producto:
  Enter        -> empieza a grabar; Enter otra vez -> termina y procesa
  escribir     -> la orden escrita, sin micrófono (útil para probar)
  n            -> siguiente producto (simula escanear otro código)
  q            -> salir

Para hablar desde el celular usa servidor_movil.py.
Uso: .venv/bin/python demo_voz.py [--whisper small] [--sin-llm] [--llm qwen2.5-3b|qwen3-4b] [--mudo]
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
from voz.motor import Motor  # noqa: E402
from voz.sesion import PRODUCTOS, Sesion, numero_hablado  # noqa: E402

AZUL, VERDE, AMARILLO, ROJO, GRIS, FIN = "\033[94m", "\033[92m", "\033[93m", "\033[91m", "\033[90m", "\033[0m"


def grabar(ruta: Path) -> bool:
    grabador = shutil.which("arecord")
    if not grabador:
        print(f"{ROJO}No encontré arecord para grabar.{FIN}")
        return False
    proc = subprocess.Popen(
        [grabador, "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", str(ruta)],
        stderr=subprocess.PIPE,
    )
    input(f"{ROJO}● Grabando...{FIN} habla y presiona Enter al terminar ")
    proc.terminate()
    proc.wait()
    return ruta.exists() and ruta.stat().st_size > 8000


def reproducir(wav: bytes):
    reproductor = shutil.which("pw-play") or shutil.which("aplay")
    if not reproductor:
        return
    ruta = Path(tempfile.gettempdir()) / "ferreteria_respuesta.wav"
    ruta.write_bytes(wav)
    subprocess.Popen([reproductor, str(ruta)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--whisper", default="small")
    ap.add_argument("--llm", default="qwen2.5-3b")
    ap.add_argument("--sin-llm", action="store_true")
    ap.add_argument("--voz", default="es_MX-claude-high")
    ap.add_argument("--mudo", action="store_true", help="no reproducir la respuesta hablada")
    a = ap.parse_args()

    motor = Motor(a.whisper, a.llm, a.sin_llm, a.voz, aviso=lambda m: print(f"{GRIS}{m}{FIN}"))
    sesion = Sesion()
    ruta = Path(tempfile.gettempdir()) / "ferreteria_orden.wav"

    print(f"\n{VERDE}Listo.{FIN} Enter = hablar · escribir = orden escrita · n = siguiente · q = salir")
    print(f"{GRIS}Ejemplos: «hay veintiocho», «suma diez más», «corrige a quince», "
          f"«va en el pasillo tres estante B», «dos dañados», «¿cuánto hay en la sede norte?», «deshacer»{FIN}")
    i = 0
    while True:
        prod = PRODUCTOS[i % len(PRODUCTOS)]
        contado = sesion.conteo.get(prod["codigo"])
        print(f"\n{AZUL}▌ Escaneado:{FIN} {prod['nombre']}  {GRIS}[{prod['codigo']}]{FIN}")
        print(f"  Sistema: {prod['sistema']} {prod['unidad']} · Contado: "
              f"{numero_hablado(contado) if contado is not None else '—'}")
        entrada = input("> ").strip()
        if entrada.lower() == "q":
            break
        if entrada.lower() == "n":
            i += 1
            continue

        t_voz = 0.0
        if entrada:
            texto = entrada
        else:
            if not grabar(ruta):
                print(f"{AMARILLO}No se grabó audio. Revisa el micrófono o escribe la orden.{FIN}")
                continue
            texto, t_voz = motor.transcribir(ruta, prod)
            print(f"  {GRIS}Oí:{FIN} «{texto}»")

        orden, origen, t_int = motor.interpretar(texto, prod)
        respuesta = sesion.aplicar(prod, orden)
        wav, t_tts = motor.sintetizar(respuesta)
        if not a.mudo:
            reproducir(wav)
        campos = {k: v for k, v in orden.items() if v is not None and k not in ("texto", "confianza")}
        print(f"  {GRIS}Orden ({origen}):{FIN} {campos}")
        print(f"  {VERDE}➜ {respuesta}{FIN}")
        print(f"  {GRIS}Tiempos: voz→texto {t_voz*1000:.0f} ms · interpretar {t_int*1000:.0f} ms "
              f"· respuesta {t_tts*1000:.0f} ms · total {(t_voz+t_int+t_tts)*1000:.0f} ms{FIN}")

    print(f"\n{VERDE}Resumen de la sesión{FIN}")
    for p in PRODUCTOS:
        c = sesion.conteo.get(p["codigo"])
        if c is None and p["codigo"] not in sesion.danados:
            continue
        print(f"  {p['nombre']}: contado {numero_hablado(c) if c is not None else '—'} / sistema {p['sistema']}"
              f" · dañados {numero_hablado(sesion.danados.get(p['codigo'], 0))}"
              f" · ubicación {sesion.ubicacion.get(p['codigo'], '—')}")


if __name__ == "__main__":
    main()
