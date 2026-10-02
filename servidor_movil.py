"""Servidor para usar el celular como micrófono y pantalla del inventario.

El celular abre https://<ip-del-pc>:8443, graba la orden y la envía por WiFi;
el PC la transcribe, la interpreta y contesta con voz. Nada sale de la red local.
Cada grabación queda en bench/real/ para medir la precisión con voces reales.

Uso: .venv/bin/python servidor_movil.py [--puerto 8443] [--llm qwen2.5-3b] [--sin-llm]
"""

import argparse
import base64
import json
import socket
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
from voz.motor import Motor  # noqa: E402
from voz.sesion import PRODUCTOS, Sesion  # noqa: E402

CERTS = RAIZ / "certs"
REGISTRO = RAIZ / "bench" / "real"

app = FastAPI()
sesion = Sesion()
indice = 0
motor: Motor | None = None
candado = threading.Lock()  # Whisper y el LLM atienden una orden a la vez


class Texto(BaseModel):
    texto: str


class Paso(BaseModel):
    paso: int


def ip_local() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no envía nada; solo elige la interfaz de salida
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def asegurar_certificado(ip: str) -> tuple[Path, Path]:
    """Certificado propio para HTTPS: Safari solo da el micrófono en páginas seguras."""
    CERTS.mkdir(exist_ok=True)
    crt, key, marca = CERTS / "servidor.crt", CERTS / "servidor.key", CERTS / "ip.txt"
    if not (crt.exists() and key.exists() and marca.exists() and marca.read_text() == ip):
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
            "-keyout", str(key), "-out", str(crt), "-days", "365",
            "-subj", "/CN=Ferreteria IA (local)",
            "-addext", f"subjectAltName=IP:{ip},IP:127.0.0.1,DNS:localhost",
            "-addext", "extendedKeyUsage=serverAuth",
        ], check=True, capture_output=True)
        key.chmod(0o600)
        marca.write_text(ip)
    return crt, key


def producto() -> dict:
    return PRODUCTOS[indice % len(PRODUCTOS)]


def estado() -> dict:
    return {
        "producto": sesion.estado(producto()),
        "indice": indice % len(PRODUCTOS),
        "total": len(PRODUCTOS),
        "llm": motor.llm is not None,
    }


def procesar(texto: str, t_voz: float, archivo: str | None = None) -> dict:
    prod = producto()
    orden, origen, t_int = motor.interpretar(texto, prod)
    respuesta = sesion.aplicar(prod, orden)
    wav, t_tts = motor.sintetizar(respuesta)
    campos = {k: v for k, v in orden.items() if v is not None and k not in ("texto", "confianza")}
    tiempos = {"voz_texto_ms": round(t_voz * 1000), "interpretar_ms": round(t_int * 1000),
               "respuesta_ms": round(t_tts * 1000)}

    REGISTRO.mkdir(parents=True, exist_ok=True)
    with open(REGISTRO / "registro.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "hora": datetime.now().isoformat(timespec="seconds"), "archivo": archivo,
            "producto": prod["codigo"], "oi": texto, "orden": campos, "motor": origen,
            "respuesta": respuesta, **tiempos,
        }, ensure_ascii=False) + "\n")
    print(f"[{datetime.now():%H:%M:%S}] «{texto}» -> {campos} ({origen}) -> {respuesta} "
          f"| voz {tiempos['voz_texto_ms']} ms, interpretar {tiempos['interpretar_ms']} ms", flush=True)

    return {
        "oi": texto, "orden": campos, "motor": origen, "respuesta": respuesta,
        "audio": base64.b64encode(wav).decode(), "tiempos": tiempos, **estado(),
    }


@app.get("/")
def inicio():
    return FileResponse(RAIZ / "web" / "movil.html", headers={"Cache-Control": "no-store"})


@app.get("/api/estado")
def api_estado():
    return estado()


@app.post("/api/voz")
def api_voz(audio: UploadFile = File(...)):
    tipo = audio.content_type or ""
    ext = ".m4a" if "mp4" in tipo else ".webm" if "webm" in tipo else ".audio"
    carpeta = REGISTRO / "audios"
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3] + ext
    (carpeta / nombre).write_bytes(audio.file.read())
    with candado:
        texto, t_voz = motor.transcribir(carpeta / nombre, producto())
        return procesar(texto, t_voz, nombre)


@app.post("/api/texto")
def api_texto(datos: Texto):
    with candado:
        return procesar(datos.texto.strip(), 0.0)


@app.post("/api/producto")
def api_producto(datos: Paso):
    global indice
    indice += datos.paso
    return estado()


def main():
    global motor
    ap = argparse.ArgumentParser()
    ap.add_argument("--puerto", type=int, default=8443)
    ap.add_argument("--whisper", default="small")
    ap.add_argument("--llm", default="qwen2.5-3b")
    ap.add_argument("--sin-llm", action="store_true")
    ap.add_argument("--voz", default="es_MX-claude-high")
    a = ap.parse_args()

    motor = Motor(a.whisper, a.llm, a.sin_llm, a.voz)
    ip = ip_local()
    crt, key = asegurar_certificado(ip)
    url = f"https://{ip}:{a.puerto}"

    print(f"\nAbre en el celular (misma red WiFi): {url}\n")
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.print_ascii(invert=True)
    except ImportError:
        pass
    print("Safari avisará que la conexión no es privada: es el certificado local. "
          "Toca «Mostrar detalles» → «visitar este sitio web».\n", flush=True)

    uvicorn.run(app, host="0.0.0.0", port=a.puerto, ssl_certfile=str(crt), ssl_keyfile=str(key),
                log_level="warning")


if __name__ == "__main__":
    main()
