"""Motor de voz completo: Whisper -> reglas -> LLM (si hace falta) -> Piper."""

import atexit
import ctypes
import io
import signal
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

from voz.comandos import interpretar, verificar_llm
from voz.llm import InterpreteLLM

RAIZ = Path(__file__).resolve().parent.parent
BUILD = "llama-b11321"
MODELOS_LLM = {
    "qwen2.5-1.5b": "qwen2.5-1.5b-q4.gguf",
    "qwen2.5-3b": "qwen2.5-3b-q4.gguf",
    "qwen3-4b": "qwen3-4b-2507-q4.gguf",
}
# Vocabulario que se le da a Whisper como contexto para que acierte más
PROMPT_WHISPER = (
    "Inventario de ferretería. Cantidades: cuarenta y dos, ciento cincuenta, "
    "mil doscientos, kilo y medio, dos docenas. Órdenes: suma, quita, corrige, "
    "deshacer, dañado, anota. Ubicación: pasillo, estante, bodega. Sede norte, sede sur."
)
# Frases que Whisper inventa con audio casi en silencio (aprendió de subtítulos)
ALUCINACIONES = ("amara.org", "subtitulos", "subtítulos", "gracias por ver", "suscribete", "suscríbete")


def _morir_con_el_padre():
    """En Linux, el llama-server recibe SIGTERM si el proceso que lo lanzó muere."""
    try:
        ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)  # 1 = PR_SET_PDEATHSIG
    except OSError:
        pass


def arrancar_llm(modelo: str, aviso=print) -> InterpreteLLM | None:
    """Usa el llama-server que ya esté corriendo o arranca uno en CPU."""
    interprete = InterpreteLLM()
    if interprete.disponible():
        return interprete
    binario = RAIZ / "tools" / "llama-cpu" / BUILD / "llama-server"
    aviso(f"Cargando LLM {modelo}...")
    proc = subprocess.Popen(
        [str(binario), "-m", str(RAIZ / "models" / "llm" / MODELOS_LLM[modelo]), "-t", "4",
         "-c", "6144", "-np", "2", "--host", "127.0.0.1", "--port", "8091", "--no-webui"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        preexec_fn=_morir_con_el_padre if sys.platform == "linux" else None,
    )
    atexit.register(proc.terminate)
    for _ in range(300):
        if interprete.disponible():
            interprete.interpretar("Hay cinco.")  # deja el prompt de sistema en caché
            return interprete
        time.sleep(0.2)
    aviso("El LLM no arrancó; sigo solo con reglas.")
    return None


class Motor:
    def __init__(self, whisper="small", llm="qwen2.5-3b", sin_llm=False,
                 voz="es_MX-claude-high", aviso=print):
        from faster_whisper import WhisperModel
        from piper import PiperVoice

        aviso(f"Cargando Whisper {whisper}...")
        self.whisper = WhisperModel(whisper, device="cpu", compute_type="int8", cpu_threads=4,
                                    download_root=str(RAIZ / "models" / "whisper"))
        self.llm = None if sin_llm else arrancar_llm(llm, aviso)
        self.voz = PiperVoice.load(str(RAIZ / "models" / "voices" / f"{voz}.onnx"))
        self.bloqueo = threading.Lock()  # Whisper y el LLM atienden una petición a la vez

    def transcribir(self, ruta, producto: dict | None = None, prompt: str | None = None) -> tuple[str, float]:
        """El nombre del producto escaneado ayuda a Whisper ("tubo" y no "tuvo")."""
        t0 = time.perf_counter()
        if prompt is None:
            prompt = f"Producto: {producto['nombre']}. {PROMPT_WHISPER}" if producto else PROMPT_WHISPER
        segs, _ = self.whisper.transcribe(
            str(ruta), language="es", beam_size=1, initial_prompt=prompt,
            vad_filter=True, condition_on_previous_text=False, without_timestamps=True,
        )
        texto = " ".join(s.text.strip() for s in segs).strip()
        if any(a in texto.lower() for a in ALUCINACIONES):
            texto = ""
        return texto, time.perf_counter() - t0

    def interpretar(self, texto: str, producto: dict | None = None) -> tuple[dict, str, float]:
        """Devuelve (orden, motor usado, segundos)."""
        t0 = time.perf_counter()
        orden = interpretar(texto, producto)
        motor = "reglas"
        if orden["confianza"] == "baja" and texto.strip():
            if self.llm:
                propuesta, _ = self.llm.interpretar(texto, producto)
                orden = verificar_llm(propuesta, texto, reglas=orden)
                motor = "LLM"
            elif orden["accion"] != "desconocido":
                motor = "reglas (confianza baja)"
        return orden, motor, time.perf_counter() - t0

    def sintetizar(self, texto: str) -> tuple[bytes, float]:
        t0 = time.perf_counter()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            self.voz.synthesize_wav(texto, wf)
        return buf.getvalue(), time.perf_counter() - t0
