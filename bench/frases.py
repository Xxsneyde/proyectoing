"""Frases de prueba: lo que diría un empleado de ferretería contando inventario."""

FRASES = [
    {"id": "f01", "texto": "Cuarenta y dos.", "esperado": {"accion": "conteo", "cantidad": 42}},
    {"id": "f02", "texto": "Hay veintiocho unidades.", "esperado": {"accion": "conteo", "cantidad": 28, "unidad": "unidades"}},
    {"id": "f03", "texto": "Ciento cincuenta.", "esperado": {"accion": "conteo", "cantidad": 150}},
    {"id": "f04", "texto": "Mil doscientos.", "esperado": {"accion": "conteo", "cantidad": 1200}},
    {"id": "f05", "texto": "Ochocientos setenta y cinco metros.", "esperado": {"accion": "conteo", "cantidad": 875, "unidad": "metros"}},
    {"id": "f06", "texto": "Kilo y medio.", "esperado": {"accion": "conteo", "cantidad": 1.5, "unidad": "kilos"}},
    {"id": "f07", "texto": "Doce cajas.", "esperado": {"accion": "conteo", "cantidad": 12, "unidad": "cajas"}},
    {"id": "f08", "texto": "Dos docenas.", "esperado": {"accion": "conteo", "cantidad": 24}},
    {"id": "f09", "texto": "Ahí hay como sesenta y tres.", "esperado": {"accion": "conteo", "cantidad": 63}},
    {"id": "f10", "texto": "Suma diez más.", "esperado": {"accion": "sumar", "cantidad": 10}},
    {"id": "f11", "texto": "Agrégale quince.", "esperado": {"accion": "sumar", "cantidad": 15}},
    {"id": "f12", "texto": "Quita cuatro.", "esperado": {"accion": "restar", "cantidad": 4}},
    {"id": "f13", "texto": "Corrige el anterior a dieciséis.", "esperado": {"accion": "corregir", "cantidad": 16}},
    {"id": "f14", "texto": "No, eran treinta y seis.", "esperado": {"accion": "corregir", "cantidad": 36}},
    {"id": "f15", "texto": "Esto va en el pasillo tres, estante B.", "esperado": {"accion": "ubicacion", "cantidad": None, "ubicacion": "pasillo 3 estante b"}},
    {"id": "f16", "texto": "Hay dos dañados.", "esperado": {"accion": "danado", "cantidad": 2}},
    {"id": "f17", "texto": "Márcalo como dañado.", "esperado": {"accion": "danado", "cantidad": None}},
    {"id": "f18", "texto": "¿Cuánto hay de esto en la sede norte?", "esperado": {"accion": "consulta", "cantidad": None, "sede": "norte"}},
    {"id": "f19", "texto": "Deshacer el último.", "esperado": {"accion": "deshacer", "cantidad": None}},
    {"id": "f20", "texto": "Anota que la etiqueta no corresponde al producto.", "esperado": {"accion": "nota", "cantidad": None}},
]

# Frases más libres, pensadas para ver dónde las reglas no alcanzan y entra el LLM
FRASES_DIFICILES = [
    {"id": "d01", "texto": "Tres de esos están oxidados.", "esperado": {"accion": "danado", "cantidad": 3}},
    {"id": "d02", "texto": "Me equivoqué, son cuarenta, no cincuenta.", "esperado": {"accion": "corregir", "cantidad": 40}},
    {"id": "d03", "texto": "¿Qué me falta por contar en este pasillo?", "esperado": {"accion": "consulta", "cantidad": None}},
    {"id": "d04", "texto": "Súmale media docena.", "esperado": {"accion": "sumar", "cantidad": 6}},
    {"id": "d05", "texto": "Llegaron dos cajas más de las que conté.", "esperado": {"accion": "sumar", "cantidad": 2, "unidad": "cajas"}},
    {"id": "d06", "texto": "Bájale tres porque se vendieron.", "esperado": {"accion": "restar", "cantidad": 3}},
]

# Frases con el producto escaneado como contexto. Las "real" son transcripciones
# de grabaciones reales desde el iPhone (2026-10-01); el resto, variantes probables.
# producto = posición en voz.sesion.PRODUCTOS
FRASES_PRODUCTO = [
    {"id": "r01", "producto": 0, "texto": "Hola.", "esperado": {"accion": "desconocido"}},
    {"id": "r02", "producto": 4, "texto": "Cementos gris, cincuenta gramos. Hay cuatro bolsas.", "esperado": {"accion": "conteo", "cantidad": 4}},
    {"id": "r03", "producto": 4, "texto": "Cemento gris 50 gr. Hay cuatro bolsas.", "esperado": {"accion": "conteo", "cantidad": 4}},
    {"id": "r04", "producto": 0, "texto": "Ahí se encuentra todos.", "esperado": {"accion": "completo"}},
    {"id": "r05", "producto": 0, "texto": "Hay veinticinco unidades de tubo PVC.", "esperado": {"accion": "conteo", "cantidad": 25}},
    {"id": "r06", "producto": 0, "texto": "Hay 25 unidades de tubo PVC.", "esperado": {"accion": "conteo", "cantidad": 25}},
    {"id": "r07", "producto": 3, "texto": "Hay tres puntillas dopulgadas con cabeza.", "esperado": {"accion": "conteo", "cantidad": 3}},
    {"id": "r08", "producto": 3, "texto": "Hay tres puntillas dos pulgadas con cabeza.", "esperado": {"accion": "conteo", "cantidad": 3}},
    {"id": "r09", "producto": 0, "texto": "Hay cual, hay 25 unidades que tuvo PVC.", "esperado": {"accion": "conteo", "cantidad": 25}},
    {"id": "r10", "producto": 0, "texto": "Se dañaron tres tubos.", "esperado": {"accion": "danado", "cantidad": 3}},
    {"id": "r11", "producto": 0, "texto": "Sede norte.", "esperado": {"accion": "consulta", "sede": "norte"}},
    {"id": "r12", "producto": 3, "texto": "Hay 20 tornillos, hay 30 cables THHN 12 AWG.", "esperado": {"accion": "desconocido"}},
    {"id": "r13", "producto": 3, "texto": "Puntilla 2 pulgadas con cabeza. Hay 16 kilos.", "esperado": {"accion": "conteo", "cantidad": 16}},
    {"id": "r14", "producto": 4, "texto": "Cemento gris, hay 60 vultos.", "esperado": {"accion": "conteo", "cantidad": 60}},
    {"id": "r15", "producto": 4, "texto": "Puedes agregar un nuevo Producto.", "esperado": {"accion": "desconocido"}},
    {"id": "p01", "producto": 0, "texto": "Tubo de media pulgada, hay treinta.", "esperado": {"accion": "conteo", "cantidad": 30}},
    {"id": "p02", "producto": 4, "texto": "Cemento de cincuenta kilos, quedan ocho bultos.", "esperado": {"accion": "conteo", "cantidad": 8}},
    {"id": "p03", "producto": 1, "texto": "Tornillo seis por uno, doce cajas.", "esperado": {"accion": "conteo", "cantidad": 12}},
    {"id": "p04", "producto": 2, "texto": "Del cable doce hay doscientos cuarenta metros.", "esperado": {"accion": "conteo", "cantidad": 240}},
    {"id": "p05", "producto": 0, "texto": "Están completos.", "esperado": {"accion": "completo"}},
    {"id": "p06", "producto": 0, "texto": "Un momento.", "esperado": {"accion": "desconocido"}},
    {"id": "p07", "producto": 3, "texto": "Ya, listo, sigamos.", "esperado": {"accion": "desconocido"}},
]

from voz.motor import PROMPT_WHISPER  # noqa: E402,F401  (lo usan los scripts de bench)


def evaluar(esperado: dict, obtenido: dict) -> bool:
    """La orden es correcta si coinciden todos los campos esperados."""
    from voz.comandos import normalizar_ubicacion

    for campo, valor in esperado.items():
        dato = obtenido.get(campo)
        if campo == "cantidad":
            if valor is None:
                if dato not in (None, 0, ""):
                    return False
            else:
                try:
                    if abs(float(dato) - float(valor)) > 1e-6:
                        return False
                except (TypeError, ValueError):
                    return False
        elif campo == "ubicacion":
            if normalizar_ubicacion(dato) != valor:
                return False
        elif campo in ("sede", "unidad", "accion"):
            if str(dato or "").strip().lower() != valor:
                return False
    return True
