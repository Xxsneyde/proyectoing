"""Encuentra productos, clientes y proveedores como los nombra la gente, y los números que dijo.

"el tubo de media", "cable doce", "bombillos de 9 vatios" -> el producto del catálogo.
Compara palabras (tolerando plurales y errores de Whisper) y medidas (1/2 = media = 0,5).
"""

import re
from difflib import SequenceMatcher

from erp.db import todos
from voz.comandos import MULTIPLICADORES, UNIDADES_NUM, _numero_digitos, extraer_numeros, normalizar

VACIAS = set("""de del la el los las lo un una unos unas para con por y o al a en que se me le les mi su
ese esa eso este esta esto hay tenemos tiene quedan queda cuanto cuanta cuantos cuantas producto""".split())
# Palabras de empaque: si coinciden, ayudan; si no, casi no restan ("bultos de cemento")
EMPAQUE = {"bulto", "unidad", "caja", "paquete", "rollo", "bolsa", "galon", "kilo", "metro", "par"}
# Unidades que van pegadas a una medida ("50 kg", "9 vatios"): describen el número, no son palabras
UNIDAD_DE_MEDIDA = {"kg", "kilo", "kilos", "kilogramo", "kilogramos", "quilo", "quilos", "pulgada", "pulgadas", "pulg",
                    "m", "metro", "metros", "mts", "w", "vatio", "vatios", "watts", "a", "amperio", "amperios", "amp",
                    "mm", "milimetro", "milimetros", "ml", "mililitro", "mililitros", "v", "voltio", "voltios", "oz",
                    "onza", "onzas", "g", "gramo", "gramos", "cm", "awg", "galon", "galones", "piezas", "pieza"}
# Medidas que no distinguen productos ("pulgada" está en casi toda la tubería): se ignoran siempre
SIEMPRE_UNIDAD = {"pulgada", "pulgadas", "pulg", "vatio", "vatios", "watts", "amperio", "amperios", "milimetro",
                  "milimetros", "mililitro", "mililitros", "voltio", "voltios", "onza", "onzas", "gramo", "gramos",
                  "kg", "mm", "ml", "cm", "awg", "kilogramo", "kilogramos"}
ALIAS = {"clavo": "puntilla", "foco": "bombillo", "bombilla": "bombillo", "pegamento": "pegante", "tiner": "thinner",
         "diluyente": "thinner", "taco": "breaker", "tomacorriente": "toma", "tarugo": "chazo", "silicon": "silicona",
         "masking": "enmascarar", "pintor": "enmascarar", "metro": "flexometro"}
FRACCIONES_HABLADAS = [
    (r"\b(\d+|un|una|dos|tres|cuatro|cinco|seis|siete) (?:pulgadas? )?y media\b", None),
    (r"\btres cuartos\b", 0.75), (r"\bun cuarto\b|\bcuarto\b", 0.25), (r"\btres octavos\b", 0.375),
    (r"\bun octavo\b", 0.125), (r"\bcinco dieciseisavos\b|\bcinco dieciseis\b", 0.3125), (r"\bmedia\b|\bmedio\b", 0.5),
]


def _singular(w: str) -> str:
    if len(w) > 4 and w.endswith("es") and w[-3] not in "aeiou":
        return w[:-2]
    if len(w) > 3 and w.endswith("s"):
        return w[:-1]
    return w


def _fraccion_a_decimal(texto: str) -> str:
    """'1-1/2' -> '1.5'; '1/2' -> '0.5' (en texto ya normalizado o crudo)."""
    texto = re.sub(r"(\d+)\s*-\s*(\d+)\s*/\s*(\d+)", lambda m: f"{int(m.group(1)) + int(m.group(2)) / int(m.group(3)):g}", texto)
    return re.sub(r"(\d+)\s*/\s*(\d+)", lambda m: f"{int(m.group(1)) / int(m.group(2)):g}", texto)


def descomponer(texto: str, hablado: bool = False) -> tuple[list[str], list[float]]:
    """Texto -> (palabras significativas en singular, números/medidas).

    hablado=True convierte también "media", "tres cuartos" y números en palabras ("doce").
    """
    t = normalizar(re.sub(r"(\d)\s*x\s*(\d)|\bx(\d)", lambda m: f"{m.group(1)} x {m.group(2)}" if m.group(1) else f"x {m.group(3)}",
                          _fraccion_a_decimal(texto), flags=re.I))
    numeros: list[float] = []
    if hablado:
        def _y_media(m):
            base = m.group(1)
            n = _numero_digitos(base) if base[0].isdigit() else UNIDADES_NUM.get(base, 1)
            numeros.append(float(n) + 0.5)
            return " "
        for patron, valor in FRACCIONES_HABLADAS:
            if valor is None:
                t = re.sub(patron, _y_media, t)
            elif re.search(patron, t):
                numeros.append(valor)
                t = re.sub(patron, " ", t)
    tokens = t.split()
    usados = set()
    for valor, ini, fin in (extraer_numeros(tokens) if hablado else []):
        numeros.append(float(valor))
        usados.update(range(ini, fin))
    palabras = []
    for i, tok in enumerate(tokens):
        if i in usados:
            continue
        n = _numero_digitos(tok)
        if n is not None:
            numeros.append(float(n))
            continue
        anterior = tokens[i - 1] if i else ""
        if tok in UNIDAD_DE_MEDIDA and (i - 1 in usados or _numero_digitos(anterior) is not None):
            continue
        if tok in VACIAS or tok in SIEMPRE_UNIDAD or tok == "x" or tok in ("numero", "no"):
            continue
        if len(tok) < 2 and not tok.isdigit():
            continue
        w = _singular(tok)
        palabras.append(ALIAS.get(w, w))
    return palabras, numeros


def _parecido(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)):
        return 0.85
    if min(len(a), len(b)) >= 4 and SequenceMatcher(None, a, b).ratio() >= 0.8:
        return 0.7
    return 0.0


def puntuar(consulta: tuple[list[str], list[float]], nombre: tuple[list[str], list[float]]) -> tuple[float, float]:
    """(qué tanto de lo pedido está en el nombre, qué tanto del nombre se pidió)."""
    q_pal, q_num = consulta
    p_pal, p_num = nombre
    peso_total = puntos = 0.0
    usadas = set()
    for w in q_pal:
        peso = 0.3 if w in EMPAQUE else 1.0
        mejor, cual = 0.0, None
        for j, pw in enumerate(p_pal):
            s = _parecido(w, pw)
            if s > mejor:
                mejor, cual = s, j
        peso_total += peso
        puntos += peso * mejor
        if cual is not None and mejor:
            usadas.add(cual)
    for n in q_num:
        peso_total += 1.0
        if any(abs(n - pn) < 1e-6 for pn in p_num):
            puntos += 1.0
    if not peso_total:
        return 0.0, 0.0
    cobertura = (len(usadas) + sum(1 for pn in p_num if any(abs(pn - n) < 1e-6 for n in q_num))) / max(1, len(p_pal) + len(p_num))
    return puntos / peso_total, cobertura


_cache: dict = {}


def _catalogo(con) -> list[dict]:
    """Productos activos ya descompuestos; se recalcula si cambia el catálogo."""
    firma = tuple(con.execute("SELECT COUNT(*), MAX(actualizado), MAX(id) FROM productos WHERE activo = 1").fetchone())
    if _cache.get("firma") != firma:
        filas = todos(con, "SELECT id, codigo, nombre, unidad FROM productos WHERE activo = 1")
        for f in filas:
            f["_partes"] = descomponer(f["nombre"])
        _cache.update(firma=firma, filas=filas)
    return _cache["filas"]


def buscar_productos(con, texto: str, limite: int = 8) -> list[dict]:
    """Candidatos ordenados de mejor a peor, con 'puntaje' y 'cobertura'."""
    if not texto or not texto.strip():
        return []
    codigo = re.sub(r"\D", "", texto)
    if len(codigo) >= 6:
        for f in _catalogo(con):
            if f["codigo"] == codigo:
                return [{**f, "puntaje": 1.0, "cobertura": 1.0}]
    consulta = descomponer(texto, hablado=True)
    if not consulta[0] and not consulta[1]:
        return []
    puntuados = []
    for f in _catalogo(con):
        p, c = puntuar(consulta, f["_partes"])
        # Coincidir solo en un número ("de 40") no basta: debe compartir alguna palabra
        if consulta[0] and not any(_parecido(w, pw) for w in consulta[0] for pw in f["_partes"][0]):
            continue
        if p >= 0.5:
            puntuados.append({**f, "puntaje": round(p, 3), "cobertura": round(c, 3)})
    puntuados.sort(key=lambda f: (-f["puntaje"], -f["cobertura"], f["nombre"]))
    return puntuados[:limite]


def resolver_producto(con, texto: str) -> dict:
    """{'unico': producto | None, 'candidatos': [...]}. Único si uno se destaca claramente."""
    candidatos = buscar_productos(con, texto)
    if not candidatos:
        return {"unico": None, "candidatos": []}
    mejor = candidatos[0]["puntaje"]
    empatados = [c for c in candidatos if c["puntaje"] >= mejor - 0.1 and c["puntaje"] >= 0.6]
    if mejor >= 0.75 and len(empatados) == 1:
        return {"unico": candidatos[0], "candidatos": empatados}
    # Si el nombre completo de uno coincide con lo pedido ("thinner" vs "Thinner"), gana ese
    completos = [c for c in empatados if c["cobertura"] >= 0.999 and c["puntaje"] >= 0.999]
    if len(completos) == 1:
        return {"unico": completos[0], "candidatos": empatados}
    return {"unico": None, "candidatos": empatados or candidatos[:5]}


def producto_en_texto(con, texto: str) -> dict:
    """Cuando el producto viene dentro de una frase larga ("cámbiale el precio al candado de 40 a 25 mil"):
    gana el producto con más partes de su nombre presentes en la frase."""
    pal, nums = descomponer(texto, hablado=True)
    puntuados = []
    for f in _catalogo(con):
        p_pal, p_num = f["_partes"]
        if not p_pal:
            continue
        palabras = sum(1 for pw in p_pal if any(_parecido(w, pw) >= 0.85 for w in pal))
        if not palabras:
            continue
        medidas = sum(1 for pn in p_num if any(abs(pn - n) < 1e-6 for n in nums))
        cobertura = (palabras + medidas) / (len(p_pal) + len(p_num))
        if cobertura >= 0.6:
            puntuados.append({**f, "puntaje": round(cobertura, 3), "cobertura": round(cobertura, 3), "_n": palabras + medidas})
    puntuados.sort(key=lambda f: (-f["puntaje"], -f["_n"], f["nombre"]))
    if not puntuados:
        return {"unico": None, "candidatos": []}
    mejores = [f for f in puntuados if f["puntaje"] == puntuados[0]["puntaje"] and f["_n"] == puntuados[0]["_n"]]
    return {"unico": mejores[0] if len(mejores) == 1 else None, "candidatos": mejores[:5]}


def buscar_nombre(filas: list[dict], texto: str, campo: str = "nombre") -> list[dict]:
    """Para clientes y proveedores: los que mejor coinciden con lo dicho."""
    consulta = descomponer(texto or "", hablado=True)
    if not consulta[0]:
        return []
    salida = []
    for f in filas:
        p, c = puntuar((consulta[0], []), descomponer(f[campo]))
        if p >= 0.6:
            salida.append({**f, "puntaje": p, "cobertura": c})
    salida.sort(key=lambda f: (-f["puntaje"], -f["cobertura"]))
    if salida:
        mejor = salida[0]["puntaje"]
        salida = [f for f in salida if f["puntaje"] >= mejor - 0.1]
    return salida


def nombre_catalogo(nombre: str) -> str:
    """Como se escribe en el catálogo: 'cemento blanco de 25 kilos' -> 'Cemento blanco 25 kg',
    'tubo de media pulgada' -> 'Tubo 1/2 pulgada', 'cable de doce' -> 'Cable 12'."""
    t = " " + re.sub(r"\s+", " ", nombre.strip()) + " "
    palabras = {k: str(v) for k, v in UNIDADES_NUM.items() if 1 < v < 100 and k not in ("un", "una")}
    t = re.sub(r"(?i)\b(" + "|".join(sorted(palabras, key=len, reverse=True)) + r")\b(?= (?:pulgadas?|kilos?|kg|metros?|"
               r"mililitros?|litros?|vatios?|amperios?|galones?|mm|milímetros?|awg)\b)", lambda m: palabras[m.group(1).lower()], t)
    for patron, cambio in [(r"(?i)\bmedia pulgada\b", "1/2 pulgada"), (r"(?i)\btres cuartos(?: de)?(?= pulgada| )", "3/4"),
                           (r"(?i)\bun cuarto(?: de)?(?= pulgada| )", "1/4"), (r"(?i)\btres octavos(?: de)?(?= pulgada| )", "3/8"),
                           (r"(?i)(\d) (?:kilos?|kilogramos?)\b", r"\1 kg"), (r"(?i)(\d) metros?\b", r"\1 m"),
                           (r"(?i)(\d) mililitros?\b", r"\1 ml"), (r"(?i)(\d) vatios?\b", r"\1 W"),
                           (r"(?i)(\d) amperios?\b", r"\1 A"), (r"(?i)(\d) milímetros?\b", r"\1 mm"),
                           (r"(?i) de (?=\d)", " "), (r"(?i) de (?=1/2|3/4|1/4|3/8)", " ")]:
        t = re.sub(patron, cambio, t)
    t = re.sub(r"(?i)\b(pvc|cpvc|led|thhn|awg|n95)\b", lambda m: m.group(1).upper(), t).strip()
    return t[:1].upper() + t[1:]


# --- Números dichos --------------------------------------------------------------

def numeros_en(texto: str) -> list[float]:
    """Todos los números que la persona dijo, en palabras o cifras: '38 mil', 'treinta y ocho mil', '$15.500',
    '2,5 millones', 'media docena'."""
    t = normalizar(texto or "")
    tokens = t.split()
    salida = [float(v) for v, _, _ in extraer_numeros(tokens)]
    for i, tok in enumerate(tokens):
        n = _numero_digitos(tok)
        siguiente = tokens[i + 1] if i + 1 < len(tokens) else ""
        if n is not None and siguiente in MULTIPLICADORES:
            base = float(n) * MULTIPLICADORES[siguiente]
            salida.append(base)
            resto = tokens[i + 2:i + 6]
            extra = extraer_numeros(resto)
            if extra and extra[0][1] == 0 and extra[0][0] < MULTIPLICADORES[siguiente]:
                salida.append(base + extra[0][0])
        if tok in ("docena", "docenas"):
            previo = tokens[i - 1] if i else ""
            if previo in ("media", "medio"):
                salida.append(6.0)
            else:
                p = _numero_digitos(previo) if previo else None
                p = p if p is not None else UNIDADES_NUM.get(previo)
                salida.append(12.0 * (p if p and previo not in ("una", "un") else 1))
    if re.search(r"\b(kilo|metro|galon|bulto|litro)s? y medi[oa]\b", t):
        salida.append(1.5)
    if re.search(r"\bmedio millon\b", t):
        salida.append(500_000.0)
    if re.search(r"\b(?:un|una|uno)\b", t):  # "se rompió una brocha": una = 1
        salida.append(1.0)
    return salida


def codigo_en(texto: str) -> str | None:
    """El código de barras que dijo después de 'código': '7701234567890', '7701 2345 67890' o 'ABC-123'."""
    m = re.search(r"c[oó]digo(?: de barras)?(?: es| n[uú]mero)?[:\s]+((?:\d[\d\s.-]{4,24}\d)|[A-Za-z0-9][A-Za-z0-9-]{3,24})",
                  texto or "", re.I)
    if not m:
        return None
    crudo = m.group(1)
    return re.sub(r"[\s.-]", "", crudo) if crudo[0].isdigit() else crudo.upper()
