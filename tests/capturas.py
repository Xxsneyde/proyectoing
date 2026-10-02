"""Revisión visual del ERP: ingresa con un usuario, abre rutas y guarda capturas.

Uso: .venv/bin/python tests/capturas.py usuario /ruta1 /ruta2 ... [--movil] [--oscuro] [--base https://127.0.0.1:8444]
Las capturas quedan en tests/capturas/. Imprime el código HTTP, el ancho (para detectar
desbordes horizontales) y los errores de JavaScript de cada página.
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SALIDA = Path(__file__).resolve().parent / "capturas"


def main():
    args = sys.argv[1:]
    base = "https://127.0.0.1:8444"
    if "--base" in args:
        base = args[args.index("--base") + 1]
        args = [a for a in args if a not in ("--base", base)]
    movil, oscuro = "--movil" in args, "--oscuro" in args
    usuario, rutas = [a for a in args if not a.startswith("--")][0], [a for a in args if a.startswith("/")]
    SALIDA.mkdir(exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        vp = {"width": 390, "height": 844} if movil else {"width": 1440, "height": 900}
        ctx = b.new_context(viewport=vp, device_scale_factor=2 if movil else 1, is_mobile=movil, has_touch=movil,
                            ignore_https_errors=True, color_scheme="dark" if oscuro else "light")
        pg = ctx.new_page()
        errores = []
        pg.on("pageerror", lambda e: errores.append(str(e)))
        pg.on("console", lambda m: m.type == "error" and errores.append(m.text))
        pg.goto(base + "/ingresar")
        pg.fill("input[name=usuario]", usuario)
        pg.fill("input[name=clave]", "demo2026")
        pg.click("button.btn-primario")
        pg.wait_for_load_state("networkidle")
        for ruta in rutas:
            resp = pg.goto(base + ruta)
            pg.wait_for_load_state("networkidle")
            pg.wait_for_timeout(250)
            nombre = "".join(c if c.isalnum() else "_" for c in ruta.strip("/")) or "raiz"
            archivo = SALIDA / f"{usuario}_{nombre}{'_movil' if movil else ''}{'_oscuro' if oscuro else ''}.png"
            pg.screenshot(path=str(archivo), full_page=True)
            ancho = pg.evaluate("document.documentElement.scrollWidth")
            print(f"{ruta} {resp.status} ancho={ancho} errores={errores} -> {archivo.name}")
            errores.clear()
        b.close()


if __name__ == "__main__":
    main()
