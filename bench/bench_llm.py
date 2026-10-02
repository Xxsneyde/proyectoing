"""Mide los LLM locales (llama.cpp) en CPU y en la gráfica integrada (Vulkan).

Para cada modelo arranca llama-server, mide carga, RAM, latencia y precisión
sobre las frases de prueba y las difíciles, y lo apaga.
Uso: bench_llm.py [--whisper small]  (agrega las transcripciones reales de Whisper)
"""

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import psutil
import requests

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from bench.frases import FRASES, FRASES_DIFICILES, evaluar  # noqa: E402
from voz.llm import InterpreteLLM  # noqa: E402
from voz.motor import BUILD, MODELOS_LLM as MODELOS  # noqa: E402
PUERTO = 8091


def arrancar(modelo: str, backend: str) -> tuple[subprocess.Popen, float, float]:
    binario = RAIZ / "tools" / f"llama-{backend}" / BUILD / "llama-server"
    args = [
        str(binario), "-m", str(RAIZ / "models" / "llm" / MODELOS[modelo]),
        "-t", "4", "-c", "2048", "-np", "1", "--host", "127.0.0.1",
        "--port", str(PUERTO), "--no-webui",
    ]
    if backend == "vulkan":
        args += ["-ngl", "99"]
    libre_antes = psutil.virtual_memory().available
    log = open(RAIZ / "bench" / "resultados" / f"server_{modelo}_{backend}.log", "w")
    t0 = time.perf_counter()
    proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
    while time.perf_counter() - t0 < 180:
        if proc.poll() is not None:
            raise RuntimeError(f"llama-server terminó (código {proc.returncode}), ver {log.name}")
        try:
            if requests.get(f"http://127.0.0.1:{PUERTO}/health", timeout=1).status_code == 200:
                break
        except requests.RequestException:
            pass
        time.sleep(0.2)
    carga = time.perf_counter() - t0
    time.sleep(1)
    ram = (libre_antes - psutil.virtual_memory().available) / 1e6
    return proc, carga, ram


def medir(interprete: InterpreteLLM, casos: list[dict]) -> list[dict]:
    filas = []
    for caso in casos:
        orden, met = interprete.interpretar(caso["texto"])
        filas.append({
            "id": caso["id"], "texto": caso["texto"], "orden": orden, **met,
            "correcto": evaluar(caso["esperado"], orden),
        })
    return filas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modelos", nargs="*", default=list(MODELOS))
    ap.add_argument("--backends", nargs="*", default=["cpu", "vulkan"])
    ap.add_argument("--whisper", help="evaluar también las transcripciones de este modelo de Whisper")
    a = ap.parse_args()

    esperado = {f["id"]: f["esperado"] for f in FRASES}
    transcripciones = []
    if a.whisper:
        datos = json.loads((RAIZ / "bench" / "resultados" / f"whisper_{a.whisper}.json").read_text())
        vistos = set()
        for f in datos["filas"]:
            if f["transcripcion"] not in vistos:
                vistos.add(f["transcripcion"])
                transcripciones.append({"id": f["id"], "texto": f["transcripcion"], "esperado": esperado[f["id"]]})

    salida = RAIZ / "bench" / "resultados"
    for modelo in a.modelos:
        for backend in a.backends:
            nombre = f"{modelo}_{backend}"
            print(f"== {nombre}", flush=True)
            try:
                proc, carga, ram = arrancar(modelo, backend)
            except RuntimeError as e:
                print("  ERROR:", e, flush=True)
                continue
            try:
                interprete = InterpreteLLM(f"http://127.0.0.1:{PUERTO}")
                # La primera llamada procesa el prompt de sistema completo (en frío)
                _, frio = interprete.interpretar("Hay cinco.")
                normales = medir(interprete, FRASES)
                dificiles = medir(interprete, FRASES_DIFICILES)
                reales = medir(interprete, transcripciones) if transcripciones else []
            finally:
                proc.terminate()
                proc.wait(timeout=30)

            todas = normales + dificiles + reales
            lats = [f["total_s"] for f in todas]
            resumen = {
                "modelo": modelo, "backend": backend, "carga_s": round(carga, 1),
                "ram_mb": round(ram), "primera_llamada_s": round(frio["total_s"], 2),
                "latencia_mediana_s": round(statistics.median(lats), 2),
                "latencia_max_s": round(max(lats), 2),
                "gen_tok_s": round(statistics.median(f["gen_tok_s"] for f in todas if f["gen_tok_s"]), 1),
                "aciertos_normales": f"{sum(f['correcto'] for f in normales)}/{len(normales)}",
                "aciertos_dificiles": f"{sum(f['correcto'] for f in dificiles)}/{len(dificiles)}",
            }
            if reales:
                resumen["aciertos_transcripciones"] = f"{sum(f['correcto'] for f in reales)}/{len(reales)}"
            for f in normales + dificiles:
                if not f["correcto"]:
                    o = {k: v for k, v in f["orden"].items() if v is not None and k != "texto"}
                    print(f"  ✗ {f['texto']!r} -> {o}", flush=True)
            print(" ", json.dumps(resumen, ensure_ascii=False), flush=True)
            (salida / f"llm_{nombre}.json").write_text(json.dumps(
                {"resumen": resumen, "normales": normales, "dificiles": dificiles, "reales": reales},
                ensure_ascii=False, indent=1,
            ))


if __name__ == "__main__":
    main()
