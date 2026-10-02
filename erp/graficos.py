"""Gráficos SVG generados en el servidor (sin librerías externas, funcionan sin internet).

Especificación: columnas de máximo 24 px con punta redondeada de 4 px y base recta,
2 px de separación entre segmentos apilados, rejilla de 1 px, cada sede con su color
fijo, globo con todas las series del día y una tabla equivalente para quien no ve el color.
"""

import json
import math
from datetime import date

from markupsafe import Markup, escape

from erp.formato import pesos

DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def compacto(v: float) -> str:
    """Pesos cortos para ejes: $850 mil, $1,2 M."""
    if v >= 1_000_000:
        texto = f"{v / 1_000_000:.1f}".rstrip("0").rstrip(".").replace(".", ",")
        return f"${texto} M"
    if v >= 1_000:
        return f"${v / 1000:.0f} mil"
    return f"${v:.0f}"


def _paso_limpio(maximo: float) -> float:
    """Paso redondo del eje (1, 2, 2,5 o 5 × 10ⁿ) que deja el tope más cerca del máximo con 4 a 6 líneas."""
    if maximo <= 0:
        return 1
    opciones = []
    for pasos in (4, 5, 6):
        crudo = maximo / pasos
        potencia = 10 ** math.floor(math.log10(crudo))
        paso = next(m * potencia for m in (1, 2, 2.5, 5, 10) if crudo <= m * potencia)
        opciones.append((math.ceil(maximo / paso) * paso, paso))
    return min(opciones)[1]


def _columna(x: float, y: float, w: float, h: float, r: float) -> str:
    """Rectángulo con las esquinas superiores redondeadas (la punta del dato)."""
    r = min(r, h, w / 2)
    return (f"M{x:.1f},{y + h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
            f"H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} V{y + h:.1f} Z")


def columnas_apiladas(dias: list[dict], series: list[str], titulo: str, ancho: int = 760) -> Markup:
    """dias: [{dia, sedes: {nombre: valor}, total}] — series en el orden fijo de las sedes.
    `ancho` debe parecerse al ancho real en pantalla para que el texto no se agrande ni se encoja."""
    alto = 280
    izq, der, arriba, abajo = 58, 8, 10, 26
    w_plot, h_plot = ancho - izq - der, alto - arriba - abajo
    maximo = max((d["total"] for d in dias), default=0)
    paso = _paso_limpio(maximo)
    tope = max(paso, math.ceil(maximo / paso) * paso)
    banda = w_plot / max(len(dias), 1)
    barra = min(24, banda * 0.62)
    escala = h_plot / tope

    partes = [f'<svg viewBox="0 0 {ancho} {alto}" role="img" aria-label="{escape(titulo)}">']
    for i in range(int(tope / paso) + 1):
        y = arriba + h_plot - i * paso * escala
        partes.append(f'<line class="rejilla-g" x1="{izq}" x2="{ancho - der}" y1="{y:.1f}" y2="{y:.1f}"/>')
        partes.append(f'<text x="{izq - 8}" y="{y + 4:.1f}" text-anchor="end">{compacto(i * paso)}</text>')

    for i, d in enumerate(dias):
        x = izq + i * banda + (banda - barra) / 2
        base = arriba + h_plot
        presentes = [(k, s) for k, s in enumerate(series) if d["sedes"].get(s, 0) > 0]
        for j, (k, s) in enumerate(presentes):
            h = d["sedes"][s] * escala
            separacion = 2 if j < len(presentes) - 1 else 0
            alto_dib = max(h - separacion, 0.5)
            y = base - h
            if j == len(presentes) - 1:
                partes.append(f'<path class="serie-{k + 1}" d="{_columna(x, y, barra, alto_dib, 4)}"/>')
            else:
                partes.append(f'<rect class="serie-{k + 1}" x="{x:.1f}" y="{y + separacion:.1f}" width="{barra:.1f}" height="{alto_dib:.1f}"/>')
            base = y
        f = date.fromisoformat(d["dia"])
        if i % 5 == (len(dias) - 1) % 5:
            partes.append(f'<text x="{x + barra / 2:.1f}" y="{alto - 8}" text-anchor="middle">{f.day} {MESES[f.month - 1]}</text>')
        renglones = [{"nombre": s, "valor": pesos(d["sedes"].get(s, 0)), "clave": f"serie-{k + 1}"} for k, s in enumerate(series)]
        if len(series) > 1:
            renglones.append({"nombre": "Total", "valor": pesos(d["total"]), "total": True})
        globo = json.dumps({"titulo": f"{DIAS[f.weekday()].capitalize()} {f.day} {MESES[f.month - 1]}", "renglones": renglones},
                           ensure_ascii=False)
        partes.append(f'<rect class="zona" tabindex="0" x="{izq + i * banda:.1f}" y="{arriba}" width="{banda:.1f}" '
                      f'height="{h_plot}" rx="4" data-globo="{escape(globo)}"><title>{escape(f.isoformat())}</title></rect>')
    partes.append(f'<line class="base-g" x1="{izq}" x2="{ancho - der}" y1="{arriba + h_plot}" y2="{arriba + h_plot}"/>')
    partes.append("</svg>")
    return Markup("".join(partes))


def barras(filas: list[dict], etiqueta: str = "nombre", campo: str = "total", detalle=None) -> Markup:
    """Barras horizontales de un solo tono (comparar magnitudes). El valor va al final de la barra."""
    maximo = max((f[campo] or 0 for f in filas), default=0) or 1
    partes = []
    for f in filas:
        v = f[campo] or 0
        globo = ""
        if detalle:
            globo = f' data-globo="{escape(json.dumps({"titulo": f[etiqueta], "renglones": detalle(f)}, ensure_ascii=False))}" tabindex="0"'
        partes.append(
            f'<div class="barra-fila"{globo}><span class="nombre" title="{escape(f[etiqueta])}">{escape(f[etiqueta])}</span>'
            f'<span class="pista"><span class="relleno" style="width:{max(1.5, 100 * v / maximo):.1f}%"></span></span>'
            f'<span class="num">{pesos(v)}</span></div>'
        )
    return Markup("".join(partes))
