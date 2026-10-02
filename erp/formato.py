"""Formato colombiano para pesos, cantidades y fechas."""

from datetime import datetime

MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def _miles(entero: int) -> str:
    return f"{entero:,}".replace(",", ".")


def pesos(valor) -> str:
    """1234567 -> '$1.234.567'; -500 -> '-$500'."""
    if valor is None:
        return "—"
    v = round(valor)
    return ("-$" if v < 0 else "$") + _miles(abs(v))


def cant(x, signo: bool = False) -> str:
    """12 -> '12', 1.5 -> '1,5', 1200 -> '1.200'; con signo: '+3'."""
    if x is None:
        return "—"
    x = round(float(x), 3) + 0.0
    entero = int(abs(x))
    decimales = f"{abs(x) - entero:.3f}"[2:].rstrip("0")
    texto = _miles(entero) + ("," + decimales if decimales else "")
    if x < 0:
        texto = "-" + texto
    elif signo and x > 0:
        texto = "+" + texto
    return texto


def fecha(texto: str | None, hora: bool = True) -> str:
    """'2026-10-01 14:05:00' -> '1 oct 2026, 2:05 p. m.'"""
    if not texto:
        return "—"
    try:
        d = datetime.strptime(texto[:19], "%Y-%m-%d %H:%M:%S" if len(texto) > 10 else "%Y-%m-%d")
    except ValueError:
        return texto
    base = f"{d.day} {MESES[d.month - 1]} {d.year}"
    if hora and len(texto) > 10:
        h = d.hour % 12 or 12
        base += f", {h}:{d.minute:02d} {'a. m.' if d.hour < 12 else 'p. m.'}"
    return base


def pct(x, decimales: int = 0) -> str:
    if x is None:
        return "—"
    return f"{x:.{decimales}f}".replace(".", ",") + " %"
