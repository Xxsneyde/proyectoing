"""Texto para leer en voz alta: cifras, fracciones y siglas en palabras.

Piper lee "$1.234.500" o "1/2 pulgada" de forma rara; aquí se convierten a
"un millón doscientos treinta y cuatro mil quinientos pesos" y "media pulgada".
"""

import re

_UNIDADES = ("cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince "
             "dieciséis diecisiete dieciocho diecinueve veinte veintiuno veintidós veintitrés veinticuatro "
             "veinticinco veintiséis veintisiete veintiocho veintinueve").split()
_DECENAS = {3: "treinta", 4: "cuarenta", 5: "cincuenta", 6: "sesenta", 7: "setenta", 8: "ochenta", 9: "noventa"}
_CENTENAS = {1: "ciento", 2: "doscientos", 3: "trescientos", 4: "cuatrocientos", 5: "quinientos",
             6: "seiscientos", 7: "setecientos", 8: "ochocientos", 9: "novecientos"}
FRACCIONES = {"1/2": "media", "1/4": "un cuarto", "3/4": "tres cuartos", "3/8": "tres octavos", "1/8": "un octavo",
              "5/16": "cinco dieciseisavos", "1/32": "un treintaidosavo", "7/16": "siete dieciseisavos"}
SIGLAS = {"PVC": "pe ve ce", "THHN": "te hache hache ene", "AWG": "a we ge", "LED": "led", "N95": "ene noventa y cinco",
          "3M": "tres eme", "IVA": "iva", "OC": "o ce", "TR": "te erre"}
MEDIDAS = {"kg": "kilos", "mm": "milímetros", "ml": "mililitros", "m": "metros", "W": "vatios", "V": "voltios",
           "A": "amperios", "oz": "onzas", "g": "gramos", "cm": "centímetros"}


def _menor_mil(n: int) -> str:
    if n < 30:
        return _UNIDADES[n]
    if n < 100:
        d, u = divmod(n, 10)
        return _DECENAS[d] + (f" y {_UNIDADES[u]}" if u else "")
    if n == 100:
        return "cien"
    c, resto = divmod(n, 100)
    return _CENTENAS[c] + (f" {_menor_mil(resto)}" if resto else "")


def entero_a_palabras(n: int) -> str:
    """1234500 -> 'un millón doscientos treinta y cuatro mil quinientos'."""
    if n < 0:
        return "menos " + entero_a_palabras(-n)
    if n < 1000:
        return _menor_mil(n)
    if n < 1_000_000:
        miles, resto = divmod(n, 1000)
        cabeza = "mil" if miles == 1 else _apocopar(_menor_mil(miles)) + " mil"
        return cabeza + (f" {_menor_mil(resto)}" if resto else "")
    millones, resto = divmod(n, 1_000_000)
    cabeza = "un millón" if millones == 1 else _apocopar(entero_a_palabras(millones)) + " millones"
    return cabeza + (f" {entero_a_palabras(resto)}" if resto else "")


def _apocopar(texto: str) -> str:
    """'veintiuno mil' -> 'veintiún mil'; 'uno' delante de mil/millones -> 'un'."""
    if texto.endswith("veintiuno"):
        return texto[:-9] + "veintiún"
    if texto.endswith("uno"):
        return texto[:-3] + "un"
    return texto


def numero_a_palabras(x: float) -> str:
    """12 -> 'doce'; 1.5 -> 'uno coma cinco'; 0.5 -> 'cero coma cinco'."""
    x = round(float(x), 3)
    if x == int(x):
        return entero_a_palabras(int(x))
    entero, dec = f"{abs(x):.3f}".split(".")
    dec = dec.rstrip("0")
    signo = "menos " if x < 0 else ""
    return f"{signo}{entero_a_palabras(int(entero))} coma {' '.join(_UNIDADES[int(d)] for d in dec) if dec.startswith('0') else entero_a_palabras(int(dec))}"


def pesos_voz(valor: float, redondear: bool = True) -> str:
    """Para decir en voz alta. Redondea lo grande: 72.881.230 -> 'setenta y dos millones novecientos mil pesos'."""
    v = round(valor)
    if redondear and abs(v) >= 1_000_000:
        v = round(v, -5)
    elif redondear and abs(v) >= 100_000:
        v = round(v, -3)
    texto = entero_a_palabras(v)
    if texto.endswith("millón") or texto.endswith("millones"):
        return texto + " de pesos"
    return _apocopar(texto) + (" peso" if v == 1 else " pesos")


def para_voz(texto: str) -> str:
    """Convierte cifras, fracciones, medidas y siglas de un texto a palabras."""
    t = texto.replace("«", "").replace("»", "").replace("·", ",")
    t = re.sub(r"-?\$\s?(\d{1,3}(?:\.\d{3})+|\d+)", lambda m: ("menos " if m.group(0).startswith("-") else "")
               + pesos_voz(int(m.group(1).replace(".", "")), redondear=False), t)
    t = re.sub(r"\b(\d+)-(\d+/\d+)", lambda m: f"{m.group(1)} y {FRACCIONES.get(m.group(2), m.group(2))}", t)
    t = re.sub(r"\b\d+/\d+\b", lambda m: FRACCIONES.get(m.group(0), m.group(0).replace("/", " sobre ")), t)
    t = re.sub(r"\b(\d+)\s?x\s?(\d+)\b", r"\1 por \2", t)
    t = re.sub(r"\bx(\d+)\b", r"por \1", t)
    for sigla, dicha in SIGLAS.items():
        t = re.sub(rf"\b{re.escape(sigla)}\b", dicha, t)
    t = re.sub(r"(\d)\s?(kg|mm|ml|cm|oz|m|W|V|A|g)\b", lambda m: f"{m.group(1)} {MEDIDAS[m.group(2)]}", t)
    t = re.sub(r"(?<![\d.,])1 (pulgada|unidad|caja|bolsa|pieza|libra|onza)\b", r"una \1", t)
    t = re.sub(r"(?<![\d.,])1 (bulto|metro|kilo|galón|galon|paquete|rollo|par)\b", r"un \1", t)
    t = re.sub(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?",
               lambda m: numero_a_palabras(float(m.group(0).replace(".", "").replace(",", "."))), t)
    t = t.replace("%", " por ciento").replace("+", "más ")
    t = re.sub(r"\.(?:\s*\.)+", ".", t)  # "S.A.S.." -> "S.A.S."
    return re.sub(r"\s+", " ", t).strip()
