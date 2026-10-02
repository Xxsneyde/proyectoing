"""Junta los resultados en bench/resultados/INFORME.md.

Los aciertos de Whisper se recalculan con el intérprete actual, así una
mejora en voz/comandos.py se refleja sin volver a transcribir.
"""

import json
import statistics
import sys
from pathlib import Path

import psutil

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from bench.frases import FRASES, FRASES_DIFICILES, evaluar  # noqa: E402
from voz.comandos import interpretar  # noqa: E402

RES = RAIZ / "bench" / "resultados"
ESPERADO = {f["id"]: f["esperado"] for f in FRASES}


def pct(a, b):
    return f"{a}/{b} ({100 * a / b:.0f} %)"


def main():
    lineas = ["# Prueba de IA local — inventario por voz", ""]
    cpu = Path("/proc/cpuinfo").read_text().split("model name")[1].split("\n")[0].strip(": \t")
    lineas += [f"Equipo: {cpu}, {psutil.virtual_memory().total / 2**30:.0f} GB de RAM, sin GPU dedicada.", ""]

    lineas += ["## Voz a texto (Whisper, CPU int8) + intérprete por reglas", "",
               "| Modelo | RAM | Tiempo por frase (~2,4 s de audio) | Orden correcta (limpio) | Orden correcta (ruido) |",
               "|---|---|---|---|---|"]
    whisper = {}
    for nombre in ("base", "small", "large-v3-turbo"):
        ruta = RES / f"whisper_{nombre}.json"
        if not ruta.exists():
            continue
        d = json.loads(ruta.read_text())
        whisper[nombre] = d
        r = d["resumen"]
        celdas = []
        for cond in ("limpio", "ruido"):
            sub = [f for f in d["filas"] if f["condicion"] == cond]
            celdas.append(pct(sum(evaluar(ESPERADO[f["id"]], interpretar(f["transcripcion"])) for f in sub), len(sub)))
        lineas.append(f"| {nombre} | {r['ram_mb']} MB | {r['limpio']['latencia_mediana_s']} s "
                      f"(máx {max(r['limpio']['latencia_max_s'], r['ruido']['latencia_max_s'])} s) | {celdas[0]} | {celdas[1]} |")

    tts = json.loads((RES / "tts.json").read_text()) if (RES / "tts.json").exists() else None
    if tts:
        lineas += ["", f"Respuesta hablada (Piper): {tts['mediana_ms']:.0f} ms por frase."]

    llms = sorted(RES.glob("llm_*.json"))
    if llms:
        lineas += ["", "## LLM local (llama.cpp, cuantizado Q4)", "",
                   "| Modelo | Motor | RAM | Carga | Respuesta típica | Tokens/s | Frases normales | Frases difíciles | Transcripciones reales |",
                   "|---|---|---|---|---|---|---|---|---|"]
        for ruta in llms:
            r = json.loads(ruta.read_text())["resumen"]
            motor = "CPU" if r["backend"] == "cpu" else "Iris Xe (Vulkan)"
            lineas.append(
                f"| {r['modelo']} | {motor} | {r['ram_mb']} MB | {r['carga_s']} s | {r['latencia_mediana_s']} s "
                f"(máx {r['latencia_max_s']} s) | {r['gen_tok_s']} | {r['aciertos_normales']} | "
                f"{r['aciertos_dificiles']} | {r.get('aciertos_transcripciones', '—')} |")

    # Híbrido: reglas primero; si no entienden, el LLM
    if "small" in whisper and llms:
        lineas += ["", "## Híbrido sobre Whisper small (reglas → LLM cuando las reglas tienen confianza baja)", "",
                   "| LLM de respaldo | Limpio | Ruido | Frases que pasaron al LLM | Frases difíciles (texto) |", "|---|---|---|---|---|"]
        filas = whisper["small"]["filas"]
        for ruta in llms:
            d = json.loads(ruta.read_text())
            por_texto = {f["texto"]: f for f in d["reales"] + d["dificiles"]}
            if not por_texto:
                continue
            res, al_llm = {"limpio": [0, 0], "ruido": [0, 0]}, 0
            for f in filas:
                orden = interpretar(f["transcripcion"])
                if orden["confianza"] == "baja" and f["transcripcion"] in por_texto:
                    orden = por_texto[f["transcripcion"]]["orden"]
                    al_llm += 1
                res[f["condicion"]][0] += evaluar(ESPERADO[f["id"]], orden)
                res[f["condicion"]][1] += 1
            dificiles = 0
            for f in FRASES_DIFICILES:
                orden = interpretar(f["texto"])
                if orden["confianza"] == "baja":
                    orden = por_texto[f["texto"]]["orden"]
                dificiles += evaluar(f["esperado"], orden)
            r = d["resumen"]
            lineas.append(f"| {r['modelo']} ({r['backend']}) | {pct(*res['limpio'])} | {pct(*res['ruido'])} | "
                          f"{al_llm} de {len(filas)} | {dificiles}/{len(FRASES_DIFICILES)} |")

    texto = "\n".join(lineas) + "\n"
    (RES / "INFORME.md").write_text(texto)
    print(texto)


if __name__ == "__main__":
    main()
