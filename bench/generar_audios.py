"""Genera los audios de prueba con Piper (3 voces) en versión limpia y con ruido.

El ruido simula una ferretería: ruido de fondo, un taladro intermitente y
golpes metálicos. Las voces sintéticas son más limpias que una persona real,
así que esto mide la velocidad; la precisión real hay que verla con el
micrófono (demo_voz.py).
"""

import io
import json
import sys
import time
import wave
from pathlib import Path

import numpy as np
import soundfile as sf
from piper import PiperVoice

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from bench.frases import FRASES  # noqa: E402

VOCES = ["es_MX-ald-medium", "es_MX-claude-high", "es_ES-davefx-medium"]
SR = 16000
SNR_DB = 8  # relación voz/ruido; 8 dB ya es un local con bastante ruido
SALIDA = RAIZ / "bench" / "audios"


def sintetizar(voz: PiperVoice, texto: str) -> np.ndarray:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        voz.synthesize_wav(texto, wf)
    buf.seek(0)
    audio, sr = sf.read(buf, dtype="float32")
    if sr != SR:
        n = int(len(audio) * SR / sr)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio)
    # medio segundo de silencio antes y después, como al pulsar para hablar
    pausa = np.zeros(SR // 2, dtype=np.float32)
    return np.concatenate([pausa, audio.astype(np.float32), pausa])


def ruido_ferreteria(n: int, rng: np.random.Generator) -> np.ndarray:
    blanco = rng.standard_normal(n)
    espectro = np.fft.rfft(blanco)
    espectro /= np.sqrt(np.arange(1, len(espectro) + 1))  # ruido rosa
    fondo = np.fft.irfft(espectro, n)
    fondo /= np.abs(fondo).max() + 1e-9

    t = np.arange(n) / SR
    taladro = np.zeros(n)
    inicio = rng.integers(0, max(1, n - SR))
    dur = min(n - inicio, int(SR * rng.uniform(0.8, 1.6)))
    f0 = rng.uniform(140, 220)
    tt = t[:dur]
    zumbido = sum(np.sin(2 * np.pi * f0 * k * tt) / k for k in range(1, 8))
    taladro[inicio:inicio + dur] = zumbido * np.hanning(dur)

    golpes = np.zeros(n)
    for _ in range(rng.integers(1, 4)):
        pos = rng.integers(0, n - SR // 4)
        largo = SR // 4
        env = np.exp(-np.arange(largo) / (SR * 0.03))
        golpes[pos:pos + largo] += np.sin(2 * np.pi * rng.uniform(2000, 4000) * t[:largo]) * env

    mezcla = 0.6 * fondo + 0.5 * taladro / (np.abs(taladro).max() + 1e-9) + 0.4 * golpes
    return (mezcla / (np.sqrt(np.mean(mezcla ** 2)) + 1e-9)).astype(np.float32)


def main():
    SALIDA.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)
    manifiesto, tiempos_tts = [], []
    for nombre in VOCES:
        voz = PiperVoice.load(str(RAIZ / "models" / "voices" / f"{nombre}.onnx"))
        for f in FRASES:
            t0 = time.perf_counter()
            audio = sintetizar(voz, f["texto"])
            tiempos_tts.append(time.perf_counter() - t0)

            limpio = SALIDA / f"{nombre}_{f['id']}_limpio.wav"
            sf.write(limpio, audio, SR)

            ruido = ruido_ferreteria(len(audio), rng)
            voz_rms = np.sqrt(np.mean(audio[audio != 0] ** 2))
            ruido *= voz_rms / (10 ** (SNR_DB / 20))
            ruidoso = audio + ruido
            ruidoso /= max(1.0, np.abs(ruidoso).max())
            ruidoso_path = SALIDA / f"{nombre}_{f['id']}_ruido.wav"
            sf.write(ruidoso_path, ruidoso, SR)

            dur = len(audio) / SR
            for cond, ruta in (("limpio", limpio), ("ruido", ruidoso_path)):
                manifiesto.append({
                    "archivo": ruta.name, "voz": nombre, "condicion": cond,
                    "id": f["id"], "texto": f["texto"], "duracion": round(dur, 2),
                })
    (SALIDA / "manifiesto.json").write_text(json.dumps(manifiesto, ensure_ascii=False, indent=1))
    tiempos = sorted(tiempos_tts)
    print(f"{len(manifiesto)} audios generados en {SALIDA}")
    print(f"Piper (voz de respuesta): mediana {tiempos[len(tiempos)//2]*1000:.0f} ms por frase")
    (RAIZ / "bench" / "resultados").mkdir(exist_ok=True)
    (RAIZ / "bench" / "resultados" / "tts.json").write_text(json.dumps({
        "mediana_ms": tiempos[len(tiempos) // 2] * 1000, "max_ms": max(tiempos) * 1000,
    }))


if __name__ == "__main__":
    main()
