"""Intérprete de órdenes de voz para inventario, basado en reglas.

Convierte el texto transcrito ("hay veintiocho unidades", "corrige a 15",
"va en el pasillo tres, estante B") en una orden estructurada. Resuelve al
instante los casos comunes; lo que no entiende sale con accion="desconocido"
para que lo resuelva el LLM local.
"""

import re
import unicodedata
from difflib import get_close_matches
from functools import lru_cache

ACCIONES = (
    "conteo", "sumar", "restar", "corregir", "ubicacion",
    "danado", "consulta", "deshacer", "nota", "completo", "desconocido",
)

UNIDADES_NUM = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
    "veinte": 20, "veintiun": 21, "veintiuno": 21, "veintiuna": 21,
    "veintidos": 22, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25,
    "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
    "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
    "setenta": 70, "ochenta": 80, "noventa": 90,
    "cien": 100, "ciento": 100, "doscientos": 200, "doscientas": 200,
    "trescientos": 300, "trescientas": 300, "cuatrocientos": 400,
    "cuatrocientas": 400, "quinientos": 500, "quinientas": 500,
    "seiscientos": 600, "seiscientas": 600, "setecientos": 700,
    "setecientas": 700, "ochocientos": 800, "ochocientas": 800,
    "novecientos": 900, "novecientas": 900,
}
MULTIPLICADORES = {"mil": 1000, "millon": 1_000_000, "millones": 1_000_000}

UNIDADES_MEDIDA = {
    "unidad": "unidades", "unidades": "unidades", "caja": "cajas",
    "cajas": "cajas", "metro": "metros", "metros": "metros", "kilo": "kilos",
    "kilos": "kilos", "kg": "kilos", "quilo": "kilos", "quilos": "kilos",
    "rollo": "rollos", "rollos": "rollos",
    "bulto": "bultos", "bultos": "bultos", "galon": "galones",
    "galones": "galones", "paquete": "paquetes", "paquetes": "paquetes",
    "docena": "unidades", "docenas": "unidades", "bolsa": "bolsas", "bolsas": "bolsas",
}

# Medidas que forman parte del nombre del producto ("2 pulgadas", "50 kg"):
# si el empleado las dice, describen el producto, no son la cantidad contada
MEDIDAS_PRODUCTO = {
    "pulgada": "pulgada", "pulgadas": "pulgada", "kg": "kg", "kilo": "kg",
    "kilos": "kg", "kilogramo": "kg", "kilogramos": "kg", "quilo": "kg",
    "quilos": "kg", "gr": "g", "g": "g", "gramo": "g", "gramos": "g", "mm": "mm",
    "milimetros": "mm", "cm": "cm", "centimetros": "cm", "awg": "awg",
    "calibre": "awg", "litro": "l", "litros": "l", "galon": "galon",
    "galones": "galon", "m": "m", "metro": "m", "metros": "m",
}

PALABRAS_LUGAR = r"(pasillo|estante|bodega|seccion|vitrina|nivel|fila|mostrador)"

# Whisper a veces escribe el nombre de la letra: "estante be" -> "estante b"
NOMBRES_LETRAS = {
    "be": "b", "ce": "c", "de": "d", "efe": "f", "ge": "g", "hache": "h",
    "jota": "j", "ka": "k", "ele": "l", "eme": "m", "ene": "n", "pe": "p",
    "cu": "q", "erre": "r", "ese": "s", "te": "t", "ve": "v", "uve": "v",
    "equis": "x", "ye": "y", "zeta": "z",
}

# Palabras de órdenes; las transcripciones con errores leves se acercan a estas
PALABRAS_CLAVE = [
    "corrige", "corrigelo", "corregir", "correccion", "suma", "sumale", "sumar",
    "agrega", "agregale", "agregar", "anade", "anadele", "adiciona", "quita",
    "quitale", "quitar", "resta", "restale", "saca", "sacale", "descuenta",
    "descuentale", "danado", "danada", "danados", "danadas",
    "averiado", "averiados", "defectuoso", "defectuosos", "deshacer", "anula", "anular", "anota", "anotar",
    "observacion", "marcalo",
]
VOCABULARIO = (
    set(UNIDADES_NUM) | set(MULTIPLICADORES) | set(UNIDADES_MEDIDA)
    | set(PALABRAS_CLAVE) | set(NOMBRES_LETRAS)
)
NUMEROS_LARGOS = [p for p in UNIDADES_NUM if len(p) >= 8]

# Palabras normales en una orden. Si la frase trae otras, las reglas no están
# seguras ("bájale tres porque se vendieron") y conviene que decida el LLM.
RELLENO = set("""
el la los las lo le les de del en a al y o que con por para se me mi su esto
esta este estan estaba eso esa ese estos estas esos esas aqui ahi alli aca alla hay son
eran era es tengo tenemos quedan queda conte cuento contamos contar va van
pon ponle ponlo ponla dejalo marcalo marca marcar registra registralo
corrigelo correccion perdon equivoque cambia cambialo cambiala no si mas
otro otros otra otras menos roto rota rotos rotas malo mala malos malas
averiada averiadas defectuosa defectuosas como deshaz borra borrar elimina
eliminar anulalo ultimo anterior nota cuanto cuanta cuantos cuantas falta
faltan existencia existencias stock total sede producto productos medio media
punto coma ay eh ah bueno listo ok vale pues entonces aproximadamente cual
ya todo todos todas encuentra encuentran danaron dano danos rompieron rompio
quebrado quebrados partido partidos completo completos completa completas
cuadra igual sistema
""".split())

PATRON_DESHACER = r"deshac\w*|deshaz|borra(?:r)? el ultimo|elimina(?:r)? el ultimo|anula\w*"
PATRON_COMPLETO = (
    r"(?:estan|esta|se encuentran?|hay) tod[oa]s|complet[oa]s?|cuadra|"
    r"igual (?:al|que el) sistema"
)
PATRON_DANADO = (
    r"danad[oa]s?|danaron|danos?|averiad[oa]s?|rot[oa]s?|romp\w*|"
    r"quebrad[oa]s?|partid[oa]s?|defectuos[oa]s?|malo|malos"
)


def corregir_palabra(tok: str) -> str:
    """Acerca palabras mal transcritas al vocabulario ("corige" -> "corrige").

    Solo toca palabras desconocidas y largas, para no cambiar "cuarto" por
    "cuatro" ni "setenta" por "sesenta", que son palabras válidas.
    """
    if tok in VOCABULARIO or tok in RELLENO or len(tok) < 5 or tok[0].isdigit():
        return tok
    cerca = get_close_matches(tok, PALABRAS_CLAVE + list(UNIDADES_MEDIDA), n=1, cutoff=0.8)
    if cerca:
        return cerca[0]
    if len(tok) >= 8:
        cerca = get_close_matches(tok, NUMEROS_LARGOS, n=1, cutoff=0.85)
        if cerca:
            return cerca[0]
    return tok


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y sin signos (conserva separadores numéricos)."""
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^\w\s.,]", " ", t)
    # Conserva "1.200" y "1,5"; el resto de puntos y comas son puntuación
    t = re.sub(r"(?<!\d)[.,]|[.,](?!\d)", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _numero_digitos(tok: str):
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", tok):
        return int(tok.replace(".", ""))
    if re.fullmatch(r"\d+[.,]\d{3}", tok) and "," in tok:
        return int(tok.replace(",", ""))
    if re.fullmatch(r"\d+[.,]\d+", tok):
        return float(tok.replace(",", "."))
    if tok.isdigit():
        return int(tok)
    return None


def extraer_numeros(tokens):
    """Devuelve [(valor, inicio, fin)] con los números (palabras o dígitos)."""
    encontrados = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        digito = _numero_digitos(tok)
        if digito is not None:
            valor, fin = digito, i + 1
        elif tok in ("medio", "media") and i + 1 < len(tokens) and tokens[i + 1] in UNIDADES_MEDIDA:
            valor, fin = 0.5, i + 1
        elif tok in UNIDADES_NUM or tok in MULTIPLICADORES:
            total, actual, j, ultimo = 0, 0, i, None
            while j < len(tokens):
                t = tokens[j]
                if t in UNIDADES_NUM:
                    v = UNIDADES_NUM[t]
                    # Solo se suman combinaciones válidas: "ciento veinte", "mil
                    # doscientos", "treinta y dos". "Uno, doce" son dos números.
                    if ultimo is not None and not (
                        (ultimo >= 100 and ultimo % 100 == 0 and v < ultimo and v < 100)
                        or (ultimo >= 1000 and v < 1000)
                        or (30 <= ultimo <= 90 and tokens[j - 1] == "y" and v < 10)
                    ):
                        break
                    actual += v
                    ultimo = v
                elif t in MULTIPLICADORES:
                    m = MULTIPLICADORES[t]
                    if m == 1000:
                        actual = (actual or 1) * 1000
                        total += actual
                    else:
                        total = (total + (actual or 1)) * m
                    actual = 0
                    ultimo = m
                elif t == "y" and j + 1 < len(tokens) and tokens[j + 1] in UNIDADES_NUM:
                    pass
                else:
                    break
                j += 1
            valor, fin = total + actual, j
            # "un"/"una" sueltos solo cuentan si hay contexto numérico después
            if tok in ("un", "una") and fin == i + 1:
                siguiente = tokens[fin] if fin < len(tokens) else ""
                if siguiente not in UNIDADES_MEDIDA and siguiente not in (
                    "danado", "danada", "danados", "danadas", "y",
                ):
                    i += 1
                    continue
        else:
            i += 1
            continue

        # Fracciones: "y medio", "dos metros y medio", "punto cinco", "coma cinco"
        if fin + 1 < len(tokens) and tokens[fin] == "y" and tokens[fin + 1] in ("medio", "media"):
            valor, fin = valor + 0.5, fin + 2
        elif (
            fin + 2 < len(tokens) and tokens[fin] in UNIDADES_MEDIDA
            and tokens[fin + 1] == "y" and tokens[fin + 2] in ("medio", "media")
        ):
            valor += 0.5
        elif (
            fin + 1 < len(tokens) and tokens[fin] in ("punto", "coma")
            and tokens[fin + 1] in UNIDADES_NUM
        ):
            dec = UNIDADES_NUM[tokens[fin + 1]]
            valor, fin = valor + dec / (10 ** len(str(dec))), fin + 2
        encontrados.append((valor, i, fin))
        i = fin
    return encontrados


def _a_digitos(palabra: str) -> str:
    if palabra in UNIDADES_NUM:
        return str(UNIDADES_NUM[palabra])
    return palabra


def normalizar_ubicacion(texto):
    """'Pasillo tres, estante B' -> 'pasillo 3 estante b'."""
    if not texto:
        return None
    t = normalizar(str(texto))
    partes = re.findall(PALABRAS_LUGAR + r"(?: (?:numero|la|el))?(?: ([a-z0-9]+))?", t)
    piezas = []
    for lugar, valor in partes:
        if valor and not re.fullmatch(PALABRAS_LUGAR, valor):
            valor = NOMBRES_LETRAS.get(valor, valor)
            piezas.append(f"{lugar} {_a_digitos(valor)}")
        else:
            piezas.append(lugar)
    return " ".join(piezas) if piezas else t


def _contiene(t: str, patron: str) -> bool:
    return re.search(rf"\b(?:{patron})\b", t) is not None


@lru_cache(maxsize=512)
def contexto_producto(nombre: str) -> tuple[frozenset, frozenset, frozenset]:
    """Palabras, medidas y dimensiones del nombre del producto.

    "Tubo PVC presión 1/2 pulgada" -> ({tubo, pvc, presion}, {0.5}, {})
    "Tornillo drywall 6x1"         -> ({tornillo, drywall}, {}, {(6, 1)})
    """
    t = re.sub(r"(\d+)\s*/\s*(\d+)", lambda m: f"{int(m.group(1)) / int(m.group(2)):g}", nombre)
    t = re.sub(r"(\d+)\s*x\s*(\d+)", r"\1 por \2", t, flags=re.I)
    tokens = normalizar(t).split()
    palabras, medidas, dims = set(), set(), set()
    for i, tok in enumerate(tokens):
        valor = _numero_digitos(tok)
        if valor is not None:
            sig = tokens[i + 1] if i + 1 < len(tokens) else ""
            if sig in MEDIDAS_PRODUCTO:
                medidas.add(float(valor))
            elif sig == "por" and i + 2 < len(tokens) and _numero_digitos(tokens[i + 2]) is not None:
                dims.add((float(valor), float(_numero_digitos(tokens[i + 2]))))
        elif not (
            tok in MEDIDAS_PRODUCTO or tok in UNIDADES_MEDIDA or tok in UNIDADES_NUM
            or tok in RELLENO or len(tok) < 3
        ):
            palabras.add(tok)
    return frozenset(palabras), frozenset(medidas), frozenset(dims)


MEDIDAS_LARGAS = [m for m in MEDIDAS_PRODUCTO if len(m) >= 5]


def _es_palabra_producto(tok: str, palabras) -> bool:
    """'tubos', 'tuvo' (Whisper) y 'dopulgadas' cuentan como parte del producto."""
    if tok in VOCABULARIO or tok in RELLENO or tok in MEDIDAS_PRODUCTO or _numero_digitos(tok) is not None:
        return False
    if palabras and (tok in palabras or tok.rstrip("s") in palabras
                     or (tok.endswith("es") and tok[:-2] in palabras)):
        return True
    if len(tok) >= 4 and palabras and get_close_matches(tok, palabras, n=1, cutoff=0.75):
        return True
    return len(tok) >= 6 and bool(get_close_matches(tok, MEDIDAS_LARGAS, n=1, cutoff=0.8))


def _quitar_producto(tokens: list[str], ctx) -> list[str]:
    """Quita lo que describe al producto para que no se confunda con la cantidad."""
    palabras, medidas, dims = ctx
    quitar = set()
    numeros = extraer_numeros(tokens)
    for k, (valor, ini, fin) in enumerate(numeros):
        sig = tokens[fin] if fin < len(tokens) else ""
        if float(valor) in medidas and sig in MEDIDAS_PRODUCTO:  # "dos pulgadas", "50 kg"
            quitar.update(range(ini, fin + 1))
        elif (
            sig == "por" and k + 1 < len(numeros) and numeros[k + 1][1] == fin + 1
            and (float(valor), float(numeros[k + 1][0])) in dims  # "seis por uno"
        ):
            quitar.update(range(ini, numeros[k + 1][2]))
    for i, tok in enumerate(tokens):
        if (
            tok in ("medio", "media") and 0.5 in medidas and i + 1 < len(tokens)
            and tokens[i + 1] in MEDIDAS_PRODUCTO  # "media pulgada"
        ):
            quitar.update((i, i + 1))
        elif i not in quitar and _es_palabra_producto(tok, palabras):
            quitar.add(i)
    return [tok for i, tok in enumerate(tokens) if i not in quitar]


def interpretar(texto: str, producto: dict | None = None) -> dict:
    """Texto transcrito -> {accion, cantidad, unidad, ubicacion, sede, confianza, texto}.

    producto es el que se acaba de escanear: sus palabras y medidas ("tubo PVC
    de media pulgada") no cuentan como cantidad ni como palabras desconocidas.
    confianza="baja" significa que la frase trae palabras que las reglas no
    conocen o varios números; el llamador debería pasársela al LLM.
    """
    ctx = contexto_producto(producto["nombre"]) if producto else (frozenset(), frozenset(), frozenset())
    t = " ".join(
        tok if _es_palabra_producto(tok, ctx[0]) else corregir_palabra(tok)
        for tok in normalizar(texto).split()
    )
    orden = {
        "accion": "desconocido", "cantidad": None, "unidad": None,
        "ubicacion": None, "sede": None, "confianza": "baja", "texto": texto.strip(),
    }
    if not t:
        return orden

    # Ubicación: se extrae primero para que "pasillo tres" no cuente como cantidad
    sin_lugar = t
    if re.search(rf"\b{PALABRAS_LUGAR}\b", t):
        orden["ubicacion"] = normalizar_ubicacion(t)
        sin_lugar = re.sub(
            rf"\b{PALABRAS_LUGAR}(?: (?:numero|la|el))?(?: [a-z0-9]+)?", " ", t
        )

    sede = re.search(r"\bsede (?:de |del |la |el )?([a-z]+)", t)
    if sede:
        orden["sede"] = sede.group(1)
        sin_lugar = sin_lugar.replace(sede.group(0), " ")

    tokens = _quitar_producto(sin_lugar.split(), ctx)
    # "kilo y medio" sin número delante significa uno y medio
    for i, tok in enumerate(tokens):
        previo = tokens[i - 1] if i else ""
        if (
            tok in UNIDADES_MEDIDA and tokens[i + 1:i + 2] == ["y"]
            and tokens[i + 2:i + 3] in (["medio"], ["media"])
            and previo not in UNIDADES_NUM and _numero_digitos(previo) is None
        ):
            tokens.insert(i, "1")
            break
    numeros = extraer_numeros(tokens)
    if numeros:
        # "no, eran treinta... no, treinta y seis": gana el último número dicho
        valor, _, fin = numeros[-1]
        siguiente = tokens[fin] if fin < len(tokens) else ""
        if siguiente in ("docena", "docenas"):
            valor *= 12
        orden["cantidad"] = int(valor) if float(valor).is_integer() else valor
        if siguiente in UNIDADES_MEDIDA:
            orden["unidad"] = UNIDADES_MEDIDA[siguiente]
    if orden["unidad"] is None:
        for tok in tokens:
            if tok in UNIDADES_MEDIDA and tok not in ("docena", "docenas"):
                orden["unidad"] = UNIDADES_MEDIDA[tok]
                break

    # Intención, de la más específica a la más general
    if _contiene(t, r"cuant[oa]s?|que me falta|que falta|existencias?|stock"):
        orden["accion"] = "consulta"
    elif _contiene(t, PATRON_DESHACER):
        orden["accion"] = "deshacer"
    elif _contiene(t, r"anota|nota|observacion"):
        orden["accion"] = "nota"
    elif _contiene(t, PATRON_DANADO):
        orden["accion"] = "danado"
    elif _contiene(t, r"corrig\w*|correccion|corregir|me equivoque|perdon|no eran|no son|no era|cambia\w* a"):
        orden["accion"] = "corregir"
    elif _contiene(t, r"quita\w*|resta\w*|saca\w*|descuenta\w*|menos"):
        orden["accion"] = "restar"
    elif _contiene(t, r"suma\w*|agrega\w*|anade\w*|adiciona\w*|mas|otr[oa]s"):
        orden["accion"] = "sumar"
    elif _contiene(t, PATRON_COMPLETO) and orden["cantidad"] is None:
        orden["accion"] = "completo"
    elif orden["ubicacion"] and orden["cantidad"] is None:
        orden["accion"] = "ubicacion"
    elif orden["cantidad"] is not None:
        orden["accion"] = "conteo"
    elif orden["sede"]:
        orden["accion"] = "consulta"

    # Órdenes que necesitan número y no lo trajeron: mejor que decida el LLM
    if orden["accion"] in ("sumar", "restar", "corregir") and orden["cantidad"] is None:
        orden["accion"] = "desconocido"

    desconocidas = [
        tok for tok in tokens
        if tok not in VOCABULARIO and tok not in RELLENO and _numero_digitos(tok) is None
    ]
    # Una nota es texto libre por naturaleza; el resto debe ser una frase simple
    if orden["accion"] == "nota" or (
        orden["accion"] != "desconocido" and not desconocidas and len(numeros) <= 1
    ):
        orden["confianza"] = "alta"
    return orden


def verificar_llm(orden: dict, texto: str, reglas: dict | None = None) -> dict:
    """Descarta lo que el LLM no puede respaldar con lo que la persona dijo.

    Si el LLM coincide con las reglas en la acción, lo que le falte (casi
    siempre la cantidad) se toma de las reglas, que son mejores con los números.

    Los modelos pequeños, cuando dudan, eligen "deshacer" o copian datos de los
    ejemplos del prompt ("pasillo 7", "sede sur"). En inventario eso borra o
    ensucia registros sin que nadie lo note, así que cada campo debe tener una
    palabra que lo respalde en la frase.
    """
    t = " ".join(corregir_palabra(tok) for tok in normalizar(texto).split())
    tokens = t.split()
    o = dict(orden)
    acc = o.get("accion", "desconocido")
    if reglas and reglas.get("accion") == acc:
        for campo in ("cantidad", "unidad", "ubicacion", "sede"):
            if o.get(campo) is None and reglas.get(campo) is not None:
                o[campo] = reglas[campo]

    if o.get("ubicacion") and not re.search(rf"\b{PALABRAS_LUGAR}\b", t):
        o["ubicacion"] = None
    if o.get("sede") and not (_contiene(t, "sede") or normalizar(str(o["sede"])) in tokens):
        o["sede"] = None
    if o.get("unidad") and not any(tok in UNIDADES_MEDIDA for tok in tokens):
        o["unidad"] = None

    hay_numero = bool(extraer_numeros(tokens)) or _contiene(t, r"docenas?|medi[oa]")
    respaldo = {
        "deshacer": _contiene(t, PATRON_DESHACER),
        "nota": _contiene(t, r"anota\w*|nota|apunta\w*|observacion"),
        "completo": _contiene(t, PATRON_COMPLETO),
        "ubicacion": bool(o.get("ubicacion")),
        "consulta": _contiene(t, r"cuant[oa]s?|que|sede|falta\w*|existencias?|stock") or "?" in texto,
        "conteo": hay_numero and o.get("cantidad") is not None,
        "sumar": hay_numero and o.get("cantidad") is not None,
        "restar": hay_numero and o.get("cantidad") is not None,
        "corregir": hay_numero and o.get("cantidad") is not None,
        "danado": True,
    }
    if not respaldo.get(acc, False):
        acc = "desconocido"
    if acc == "danado" and not hay_numero:
        o["cantidad"] = None  # "márcalo como dañado" sin número: uno
    o["accion"] = acc
    if acc == "desconocido":
        o.update(cantidad=None, unidad=None, ubicacion=None, sede=None)
    return o


if __name__ == "__main__":
    import json
    import sys

    for frase in sys.argv[1:] or ["Hay veintiocho unidades"]:
        print(frase, "->", json.dumps(interpretar(frase), ensure_ascii=False))
