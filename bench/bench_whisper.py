"""Mide Whisper (voz a texto) en CPU: velocidad, RAM y precisión de la orden final."""

import json
import statistics
import sys
import time
from pathlib import Path

import psutil
from faster_whisper import WhisperModel

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from bench.frases import FRASES, PROMPT_WHISPER, evaluar  # noqa: E402
from voz.comandos import interpretar  # noqa: E402

MODELOS = sys.argv[1:] or ["base", "small", "large-v3-turbo"]
AUDIOS = RAIZ / "bench" / "audios"
ESPERADO = {f["id"]: f["esperado"] for f in FRASES}


def ram_mb() -> float:
    return psutil.Process().memory_info().rss / 1e6


def transcribir(modelo: WhisperModel, ruta: Path) -> str:
    segmentos, _ = modelo.transcribe(
        str(ruta), language="es", beam_size=1, initial_prompt=PROMPT_WHISPER,
        condition_on_previous_text=False, without_timestamps=True, vad_filter=False,
    )
    return " ".join(s.text.strip() for s in segmentos).strip()


def main():
    manifiesto = json.loads((AUDIOS / "manifiesto.json").read_text())
    salida = RAIZ / "bench" / "resultados"
    salida.mkdir(exist_ok=True)
    for nombre in MODELOS:
        ram_antes = ram_mb()
        t0 = time.perf_counter()
        modelo = WhisperModel(
            nombre, device="cpu", compute_type="int8", cpu_threads=4,
            download_root=str(RAIZ / "models" / "whisper"),
        )
        carga = time.perf_counter() - t0
        transcribir(modelo, AUDIOS / manifiesto[0]["archivo"])  # calentamiento

        filas = []
        for item in manifiesto:
            t0 = time.perf_counter()
            texto = transcribir(modelo, AUDIOS / item["archivo"])
            lat = time.perf_counter() - t0
            t1 = time.perf_counter()
            orden = interpretar(texto)
            lat_reglas = time.perf_counter() - t1
            filas.append({
                **item, "transcripcion": texto, "latencia_s": round(lat, 3),
                "reglas_ms": round(lat_reglas * 1000, 3), "orden": orden,
                "correcto_reglas": evaluar(ESPERADO[item["id"]], orden),
            })
            marca = "✓" if filas[-1]["correcto_reglas"] else "✗"
            print(f"  [{nombre}] {marca} {lat:5.2f}s  {item['condicion']:6}  {texto!r}", flush=True)

        ram = ram_mb() - ram_antes
        resumen = {"modelo": nombre, "carga_s": round(carga, 1), "ram_mb": round(ram)}
        for cond in ("limpio", "ruido"):
            sub = [f for f in filas if f["condicion"] == cond]
            lats = [f["latencia_s"] for f in sub]
            resumen[cond] = {
                "latencia_mediana_s": round(statistics.median(lats), 2),
                "latencia_max_s": round(max(lats), 2),
                "aciertos": sum(f["correcto_reglas"] for f in sub),
                "total": len(sub),
                "duracion_audio_mediana_s": statistics.median(f["duracion"] for f in sub),
            }
        print(json.dumps(resumen, ensure_ascii=False), flush=True)
        (salida / f"whisper_{nombre}.json").write_text(
            json.dumps({"resumen": resumen, "filas": filas}, ensure_ascii=False, indent=1)
        )
        del modelo


if __name__ == "__main__":
    main()
