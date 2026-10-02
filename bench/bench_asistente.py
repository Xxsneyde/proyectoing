"""Mide qué tan bien y qué tan rápido el LLM local entiende al asistente.

Uso: .venv/bin/python bench/bench_asistente.py qwen2.5-3b qwen3-4b [--puerto 8095] [--vulkan]
Arranca un llama-server por modelo (uno a la vez), corre las frases de
bench/frases_asistente.py y guarda bench/resultados/asistente_<modelo>.json.
"""

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from bench.frases_asistente import CASOS, CONTEXTO  # noqa: E402
from erp.asistente.cerebro import Cerebro  # noqa: E402
from voz.comandos import normalizar  # noqa: E402
from voz.motor import BUILD, MODELOS_LLM  # noqa: E402

SEDES = ["centro", "norte", "sur"]


def cumple(esperado: dict, obtenido: dict) -> list[str]:
    fallas = []
    for campo, valor in esperado.items():
        real = obtenido.get(campo)
        if campo == "accion":
            if real not in (valor if isinstance(valor, tuple) else (valor,)):
                fallas.append(f"accion={real}")
        elif isinstance(valor, tuple):  # varias respuestas válidas: null o un texto
            if not any(cumple({campo: v}, obtenido) == [] for v in valor):
                fallas.append(f"{campo}={real!r} (esperaba {valor})")
        elif valor is None:
            if campo == "sede" and real == "norte":  # quien habla trabaja en Norte: es lo mismo
                continue
            if real not in (None, ""):
                fallas.append(f"{campo}={real!r} (esperaba null)")
        elif isinstance(valor, (int, float)) and not isinstance(valor, bool):
            if not isinstance(real, (int, float)) or abs(float(real) - valor) > 1e-6:
                fallas.append(f"{campo}={real!r} (esperaba {valor})")
        else:
            texto = normalizar(str(real or "")).replace(" ", "")
            for palabra in str(valor).split():
                if not any(normalizar(alt).replace(" ", "") in texto for alt in palabra.split("|")):
                    fallas.append(f"{campo}={real!r} (sin «{palabra}»)")
                    break
    return fallas


def arrancar(modelo: str, puerto: int, vulkan: bool) -> subprocess.Popen:
    carpeta = "llama-vulkan" if vulkan else "llama-cpu"
    cmd = [str(RAIZ / "tools" / carpeta / BUILD / "llama-server"), "-m", str(RAIZ / "models" / "llm" / MODELOS_LLM[modelo]),
           "-t", "4", "-c", "8192", "-np", "1", "--host", "127.0.0.1", "--port", str(puerto), "--no-webui"]
    if vulkan:
        cmd += ["-ngl", "99"]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def medir(modelo: str, puerto: int, vulkan: bool) -> dict:
    proc = arrancar(modelo, puerto, vulkan)
    cerebro = Cerebro(f"http://127.0.0.1:{puerto}")
    try:
        for _ in range(600):
            if cerebro.disponible():
                break
            time.sleep(0.2)
        _, frio = cerebro.decidir("Hola", CONTEXTO, [], SEDES)
        print(f"\n== {modelo} ({'Vulkan' if vulkan else 'CPU'}) · primera llamada {frio['total_s']} s "
              f"({frio['prompt_tokens']} tokens de prompt)")
        filas = []
        for caso in CASOS:
            mensaje = f"{caso['estado']}\n{caso['texto']}" if caso.get("estado") else caso["texto"]
            intencion, m = cerebro.decidir(mensaje, CONTEXTO, caso.get("historia", []), SEDES)
            fallas = cumple(caso["esperado"], intencion)
            filas.append({"texto": caso["texto"], "obtenido": intencion, "fallas": fallas, **m})
            marca = "ok " if not fallas else "MAL"
            print(f"{marca} {m['total_s']:5.2f}s  {caso['texto'][:58]:58} -> "
                  f"{json.dumps(intencion, ensure_ascii=False)[:110]}{'  ✗ ' + '; '.join(fallas) if fallas else ''}")
        tiempos = [f["total_s"] for f in filas]
        resumen = {
            "modelo": modelo, "gpu": vulkan, "aciertos": sum(not f["fallas"] for f in filas), "total": len(filas),
            "media_s": round(statistics.mean(tiempos), 2), "p90_s": round(sorted(tiempos)[int(0.9 * len(tiempos))], 2),
            "max_s": max(tiempos), "frio_s": frio["total_s"], "filas": filas,
        }
        print(f"-> {resumen['aciertos']}/{resumen['total']} · media {resumen['media_s']} s · p90 {resumen['p90_s']} s · máx {resumen['max_s']} s")
        nombre = f"asistente_{modelo}{'_vulkan' if vulkan else ''}.json"
        (RAIZ / "bench" / "resultados" / nombre).write_text(json.dumps(resumen, ensure_ascii=False, indent=1), encoding="utf-8")
        return resumen
    finally:
        proc.terminate()
        proc.wait()


if __name__ == "__main__":
    args = sys.argv[1:]
    puerto = int(args[args.index("--puerto") + 1]) if "--puerto" in args else 8095
    vulkan = "--vulkan" in args
    modelos = [a for a in args if a in MODELOS_LLM] or ["qwen2.5-3b"]
    for mod in modelos:
        medir(mod, puerto, vulkan)
