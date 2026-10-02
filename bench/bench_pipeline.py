"""Evalúa el intérprete completo (reglas -> LLM -> verificación) con el producto como contexto.

Necesita un llama-server en el puerto 8091 (lo arranca servidor_movil.py o demo_voz.py).
"""

import json
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from bench.frases import FRASES, FRASES_DIFICILES, FRASES_PRODUCTO, evaluar  # noqa: E402
from voz.comandos import interpretar, verificar_llm  # noqa: E402
from voz.llm import InterpreteLLM  # noqa: E402
from voz.sesion import PRODUCTOS  # noqa: E402


def main():
    llm = InterpreteLLM()
    if not llm.disponible():
        sys.exit("No hay llama-server en el puerto 8091.")
    grupos = {"normales": FRASES, "difíciles": FRASES_DIFICILES, "con producto": FRASES_PRODUCTO}
    resumen = {}
    for nombre, casos in grupos.items():
        bien = al_llm = 0
        for caso in casos:
            prod = PRODUCTOS[caso.get("producto", 0)]
            t0 = time.perf_counter()
            orden = interpretar(caso["texto"], prod)
            motor = "reglas"
            if orden["confianza"] == "baja":
                propuesta, _ = llm.interpretar(caso["texto"], prod)
                orden = verificar_llm(propuesta, caso["texto"], reglas=orden)
                motor, al_llm = "LLM", al_llm + 1
            ms = (time.perf_counter() - t0) * 1000
            ok = evaluar(caso["esperado"], orden)
            bien += ok
            campos = {k: v for k, v in orden.items() if v is not None and k not in ("texto", "confianza")}
            print(f"  {'✓' if ok else '✗'} [{motor:6} {ms:5.0f} ms] {caso['texto']!r} -> {campos}", flush=True)
        resumen[nombre] = f"{bien}/{len(casos)} (al LLM: {al_llm})"
        print(f"== {nombre}: {resumen[nombre]}", flush=True)
    print(json.dumps(resumen, ensure_ascii=False))


if __name__ == "__main__":
    main()
