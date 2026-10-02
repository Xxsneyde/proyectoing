"""Asistente conversacional: reglas de la conversación con un cerebro de guion en vez del LLM.

El LLM real se mide aparte (bench/bench_asistente.py). Aquí se comprueba lo que no
puede fallar: nada se guarda sin confirmar, «no» cancela, los números inventados se
descartan, los permisos se respetan y la memoria funciona.
"""

import pytest

from erp import demo, inventario
from erp.asistente.buscar import codigo_en, numeros_en, resolver_producto
from erp.asistente.conversacion import Asistente
from erp.asistente.hablado import para_voz, pesos_voz
from erp.asistente.herramientas import Sesion
from erp.auth import ROLES
from erp.db import conectar, inicializar, uno, valor


class CerebroGuion:
    """Devuelve la intención escrita para cada frase, como si fuera el LLM."""

    def __init__(self, guion: dict):
        self.guion = guion
        self.llamadas = []

    def decidir(self, mensaje, contexto, historia, sedes):
        frase = mensaje.split("\n")[-1]
        self.llamadas.append({"mensaje": mensaje, "contexto": contexto, "historia": historia, "sedes": sedes})
        return dict(self.guion.get(frase, {"accion": "responder", "texto": "No sé."})), {}


@pytest.fixture(scope="module")
def ruta_db(tmp_path_factory):
    ruta = tmp_path_factory.mktemp("asistente") / "demo.db"
    con = conectar(ruta)
    demo.generar(con, dias=10, aviso=lambda *_: None)
    con.close()
    return ruta


@pytest.fixture
def con(ruta_db):
    c = conectar(ruta_db)
    inicializar(c)
    yield c
    c.close()


def sesion(con, usuario: str) -> Sesion:
    u = uno(con, "SELECT * FROM usuarios WHERE usuario = ?", usuario)
    sede = u["sede_id"] or 1
    datos = {**u, "sede_fija": u["sede_id"], "sede_id": sede, "rol_nombre": ROLES[u["rol"]], "token": "prueba",
             "todas_las_sedes": u["sede_id"] is None, "sede": valor(con, "SELECT nombre FROM sedes WHERE id = ?", sede)}
    return Sesion(con, datos, sede)


def producto(con, nombre: str) -> dict:
    return uno(con, "SELECT * FROM productos WHERE nombre = ?", nombre)


def charla(guion: dict) -> tuple[Asistente, CerebroGuion]:
    cerebro = CerebroGuion(guion)
    return Asistente(cerebro), cerebro


def test_entrada_solo_se_guarda_al_confirmar_y_el_si_no_pasa_por_el_llm(con):
    a, cerebro = charla({"Llegaron 20 bultos de cemento gris": {"accion": "entrada", "producto": "bultos de cemento gris",
                                                                "cantidad": 20, "motivo": "llegaron"}})
    s = sesion(con, "admin.norte")
    p = producto(con, "Cemento gris 50 kg")
    antes = inventario.existencia(con, p["id"], s.sede_id)
    r = a.procesar(s, "Llegaron 20 bultos de cemento gris")["asistente"]
    assert r["propuesta"]["estado"] == "pendiente" and "Sumar 20 bultos" in r["propuesta"]["titulo"]
    assert inventario.existencia(con, p["id"], s.sede_id) == antes
    r = a.procesar(s, "Sí, hágale")["asistente"]
    assert len(cerebro.llamadas) == 1
    assert inventario.existencia(con, p["id"], s.sede_id) == antes + 20
    assert f"Ahora hay {antes + 20:g}" in r["texto"]
    mov = uno(con, "SELECT * FROM movimientos WHERE producto_id = ? ORDER BY id DESC LIMIT 1", p["id"])
    assert mov["cantidad"] == 20 and "asistente" in mov["nota"] and mov["usuario_id"] == s.usuario["id"]
    assert "asistente" in valor(con, "SELECT detalle FROM auditoria ORDER BY id DESC LIMIT 1")
    tarjeta = a.mensajes(s)[-3]
    assert tarjeta["propuesta"]["estado"] == "confirmada"


def test_no_cancela_aunque_el_modelo_diga_otra_cosa(con):
    a, cerebro = charla({"Llegaron 5 bultos de yeso": {"accion": "entrada", "producto": "yeso", "cantidad": 5, "motivo": None},
                         "no, mejor no": {"accion": "confirmar"}})
    s = sesion(con, "admin.norte")
    p = producto(con, "Yeso 25 kg")
    antes = inventario.existencia(con, p["id"], s.sede_id)
    a.procesar(s, "Llegaron 5 bultos de yeso")
    r = a.procesar(s, "no, mejor no")["asistente"]
    assert "no cambié nada" in r["texto"]
    assert len(cerebro.llamadas) == 1
    assert inventario.existencia(con, p["id"], s.sede_id) == antes
    assert a.mensajes(s)[-3]["propuesta"]["estado"] == "cancelada"


def test_numeros_que_no_dijo_se_descartan(con):
    frase = "agrega un producto nuevo que se llama guantes de látex, se vende a 4500 y llegaron 50"
    a, _ = charla({frase: {"accion": "crear_producto", "nombre": "guantes de látex", "precio": 4500, "costo": 3000,
                           "unidad": "unidades", "categoria": None, "codigo": "7700000000000", "cantidad": 50,
                           "minimo": None, "maximo": None, "iva": None}})
    r = a.procesar(sesion(con, "dueno"), frase)["asistente"]
    campos = {c["clave"]: c["valor"] for c in r["propuesta"]["campos"]}
    assert campos["precio"] == 4500 and campos["cantidad"] == 50
    assert campos["costo"] is None and campos["codigo"] is None


def test_producto_nuevo_pide_el_precio_y_lo_toma_de_la_respuesta(con):
    a, cerebro = charla({"registra el cemento blanco de 25 kilos": {
        "accion": "crear_producto", "nombre": "Cemento blanco 25 kg", "precio": None, "costo": None, "unidad": None,
        "categoria": None, "codigo": None, "cantidad": None, "minimo": None, "maximo": None, "iva": None}})
    s = sesion(con, "dueno")
    r = a.procesar(s, "registra el cemento blanco de 25 kilos")["asistente"]
    assert r["propuesta"]["falta"] == "precio" and "precio" in r["texto"]
    r = a.procesar(s, "a treinta y dos mil quinientos")["asistente"]
    assert len(cerebro.llamadas) == 1
    campos = {c["clave"]: c["valor"] for c in r["propuesta"]["campos"]}
    assert campos["precio"] == 32500 and not r["propuesta"].get("falta")
    # Categoría, proveedor y unidad se toman del producto más parecido (Cemento blanco 20 kg)
    parecido = producto(con, "Cemento blanco 20 kg")
    assert campos["categoria_id"] == parecido["categoria_id"] and campos["unidad"] == "bultos"
    r = a.procesar(s, "sí")["asistente"]
    nuevo = producto(con, "Cemento blanco 25 kg")
    assert nuevo and nuevo["precio"] == 32500 and nuevo["codigo"].startswith("INT-")
    assert "registré" in r["texto"]


def test_precio_cien_veces_mayor_que_el_parecido_avisa(con):
    frase = "crea el producto Tubo PVC presión 5 pulgadas a 2000000"
    a, _ = charla({frase: {"accion": "crear_producto", "nombre": "Tubo PVC presión 5 pulgadas", "precio": 2000000, "costo": None,
                           "unidad": None, "categoria": None, "codigo": None, "cantidad": None, "minimo": None, "maximo": None, "iva": None}})
    r = a.procesar(sesion(con, "dueno"), frase)["asistente"]
    assert any("ceros" in x for x in r["propuesta"]["avisos"])


def test_confirmar_desde_la_tarjeta_usa_lo_editado(con):
    frase = "crea el producto silicona negra 280 ml, precio 15500, código 7701234567890"
    a, _ = charla({frase: {"accion": "crear_producto", "nombre": "silicona negra 280 ml", "precio": 15500, "costo": None,
                           "unidad": None, "categoria": None, "codigo": None, "cantidad": None, "minimo": None, "maximo": None, "iva": None}})
    s = sesion(con, "dueno")
    r = a.procesar(s, frase)["asistente"]
    assert {c["clave"]: c["valor"] for c in r["propuesta"]["campos"]}["codigo"] == "7701234567890"
    a.boton(s, "confirmar", r["id"], campos={"precio": 16900, "costo": 11000, "cantidad": 12})
    nuevo = producto(con, "Silicona negra 280 ml")
    assert nuevo["precio"] == 16900 and nuevo["costo"] == 11000 and nuevo["codigo"] == "7701234567890"
    assert inventario.existencia(con, nuevo["id"], s.sede_id) == 12


def test_sumale_nunca_se_toma_como_dejar_en(con):
    frase = "súmale cinco al martillo de uña"
    a, _ = charla({frase: {"accion": "ajustar", "producto": "martillo de uña", "cantidad": 5, "motivo": "súmale"}})
    r = a.procesar(sesion(con, "admin.norte"), frase)["asistente"]
    assert r["propuesta"]["accion"] == "entrada" and r["propuesta"]["cantidad"] == 5


def test_producto_ambiguo_da_opciones_y_la_segunda_se_entiende_sin_llm(con):
    a, cerebro = charla({"se dañó una brocha": {"accion": "danado", "producto": "brocha", "cantidad": 1, "motivo": None}})
    s = sesion(con, "admin.norte")
    p = producto(con, "Brocha 2 pulgadas")
    inventario.mover(con, p["id"], s.sede_id, "ajuste", 5)
    antes = inventario.existencia(con, p["id"], s.sede_id)
    r = a.procesar(s, "se dañó una brocha")["asistente"]
    assert [o["texto"] for o in r["opciones"]][:2] == ["Brocha 1 pulgada", "Brocha 2 pulgadas"]
    r = a.procesar(s, "la segunda")["asistente"]
    assert len(cerebro.llamadas) == 1
    assert r["propuesta"]["producto"] == "Brocha 2 pulgadas"
    a.procesar(s, "dale")
    assert inventario.existencia(con, p["id"], s.sede_id) == antes - 1


def test_danar_mas_de_lo_que_hay_se_rechaza(con):
    a, _ = charla({"se dañaron 9999 bultos de gravilla": {"accion": "danado", "producto": "gravilla", "cantidad": 9999, "motivo": None}})
    r = a.procesar(sesion(con, "admin.norte"), "se dañaron 9999 bultos de gravilla")["asistente"]
    assert r["tipo"] == "error" and "solo hay" in r["texto"] and "propuesta" not in r


def test_sigue_hablando_del_mismo_producto(con):
    a, cerebro = charla({"¿Cuánto cemento gris hay?": {"accion": "existencia", "producto": "cemento gris", "sede": None},
                         "¿y en el sur?": {"accion": "existencia", "producto": None, "sede": "sur"}})
    s = sesion(con, "dueno")
    a.procesar(s, "¿Cuánto cemento gris hay?")
    r = a.procesar(s, "¿y en el sur?")["asistente"]
    assert "Cemento gris 50 kg" in r["texto"] and "sede Sur" in r["texto"]
    assert "Producto del que venían hablando: Cemento gris 50 kg" in cerebro.llamadas[-1]["mensaje"]
    assert cerebro.llamadas[-1]["historia"][-2]["texto"] == "¿Cuánto cemento gris hay?"


def test_el_cajero_no_ve_ventas_ni_ajusta_inventario(con):
    a, _ = charla({"¿cuánto vendimos hoy?": {"accion": "ventas", "periodo": "hoy", "sede": None},
                   "llegaron 10 bultos de arena": {"accion": "entrada", "producto": "arena", "cantidad": 10, "motivo": None}})
    s = sesion(con, "caja.centro")
    assert "administración" in a.procesar(s, "¿cuánto vendimos hoy?")["asistente"]["texto"]
    r = a.procesar(s, "llegaron 10 bultos de arena")["asistente"]
    assert "propuesta" not in r and r["tipo"] == "aviso"


def test_la_administradora_de_sede_no_ve_ventas_de_otra_sede(con):
    a, _ = charla({"ventas de hoy en el sur": {"accion": "ventas", "periodo": "hoy", "sede": "sur"}})
    r = a.procesar(sesion(con, "admin.norte"), "ventas de hoy en el sur")["asistente"]
    assert r["tipo"] == "error" and "Norte" in r["texto"]


def test_las_cifras_salen_de_la_base(con):
    a, _ = charla({"¿qué falta?": {"accion": "bajo_minimo", "sede": None},
                   "¿cuánto vendimos hoy?": {"accion": "ventas", "periodo": "hoy", "sede": None}})
    s = sesion(con, "dueno")
    r = a.procesar(s, "¿qué falta?")["asistente"]
    assert f"hay {len(inventario.bajo_minimo(con, s.sede_id))} productos" in r["texto"]
    from erp import reportes
    from erp.db import hoy
    from erp.formato import pesos
    total = reportes.ventas_periodo(con, None, hoy(), hoy())["total"]
    assert pesos(total) in a.procesar(s, "¿cuánto vendimos hoy?")["asistente"]["texto"]


def test_lo_que_se_le_pide_recordar_llega_al_cerebro(con):
    a, cerebro = charla({"Recuerda que el de cemento entrega los martes": {"accion": "recordar",
                                                                           "texto": "El proveedor de cemento entrega los martes"}})
    s = sesion(con, "bodega.centro")
    r = a.procesar(s, "Recuerda que el de cemento entrega los martes")["asistente"]
    assert r["tipo"] == "ok"
    a.procesar(s, "hola")
    assert "El de cemento entrega los martes. (Andrés Muñoz)" in cerebro.llamadas[-1]["contexto"]
    assert a.notas(con)[0]["texto"] == "El de cemento entrega los martes."


def test_ayuda_y_nada_pendiente_no_usan_el_llm(con):
    a, cerebro = charla({})
    s = sesion(con, "caja.centro")
    r = a.procesar(s, "¿Qué puedes hacer?")["asistente"]
    assert "existencias" in r["voz"] and "productos nuevos" not in r["texto"]
    assert not cerebro.llamadas


def test_la_conversacion_larga_se_recorta_para_el_modelo(con):
    a, cerebro = charla({})
    s = sesion(con, "admin.norte")
    for k in range(20):
        a.procesar(s, f"hola {k}")
    historias = [c["historia"] for c in cerebro.llamadas]
    assert max(len(h) for h in historias) <= 10
    # Entre un turno y el siguiente la historia solo crece (el modelo reusa su caché); se recorta de vez en cuando
    recortes = sum(1 for antes, despues in zip(historias, historias[1:], strict=False) if despues[:len(antes)] != antes)
    assert 1 <= recortes <= 5


def test_sin_cerebro_avisa(con):
    r = Asistente(None).procesar(sesion(con, "dueno"), "¿cuánto cemento hay?")["asistente"]
    assert r["tipo"] == "error" and "sin voz" in r["texto"]


# --- Piezas sueltas -------------------------------------------------------------------------

@pytest.mark.parametrize("dicho, esperado", [
    ("cemento gris", "Cemento gris 50 kg"), ("bombillos de 9 vatios", "Bombillo LED 9 W"), ("cable doce", "Cable THHN 12 AWG"),
    ("puntilla de dos y media", "Puntilla 2-1/2 pulgadas con cabeza"), ("tubos de pvc de tres cuartos", "Tubo PVC presión 3/4 pulgada"),
    ("el metro de 8", "Flexómetro 8 m"), ("broca de un cuarto", "Broca para concreto 1/4"), ("galones de thinner", "Thinner"),
])
def test_encuentra_el_producto_como_lo_dice_la_gente(con, dicho, esperado):
    assert resolver_producto(con, dicho)["unico"]["nombre"] == esperado


def test_si_no_es_claro_no_adivina(con):
    r = resolver_producto(con, "tubo de media pulgada")
    assert r["unico"] is None and {c["nombre"] for c in r["candidatos"]} >= {"Tubo PVC presión 1/2 pulgada", "Tubo conduit PVC 1/2 pulgada x 3 m"}


def test_numeros_y_codigos_dichos():
    assert 38000 in numeros_en("lo vendemos a 38 mil") and 32500 in numeros_en("treinta y dos mil quinientos")
    assert 2_500_000 in numeros_en("2,5 millones") and 6 in numeros_en("media docena") and 15500 in numeros_en("$15.500")
    assert codigo_en("código de barras 7701 2345 67890") == "7701234567890"


def test_cifras_para_la_voz():
    assert pesos_voz(72_881_230) == "setenta y dos millones novecientos mil pesos"
    assert para_voz("Cemento gris 50 kg: hay 21 bultos, a $34.900.") == (
        "Cemento gris cincuenta kilos: hay veintiuno bultos, a treinta y cuatro mil novecientos pesos.")
    assert para_voz("Tubo PVC 1/2") == "Tubo pe ve ce media"


# --- Errores encontrados en conversaciones reales con el modelo -------------------------------

def test_una_sola_cifra_no_es_precio_y_costo_y_la_medida_no_es_la_unidad(con):
    frase = "Registra un producto nuevo: cemento blanco de 25 kilos, nos cuesta 26 mil"
    a, cerebro = charla({frase: {"accion": "crear_producto", "nombre": "cemento blanco de 25 kilos", "precio": 26000,
                                 "costo": 26000, "unidad": "kilos", "categoria": "Construcción", "codigo": None,
                                 "cantidad": None, "minimo": None, "maximo": None, "iva": None}})
    s = sesion(con, "admin.norte")
    a.reiniciar(s)
    r = a.procesar(s, frase)["asistente"]
    campos = {c["clave"]: c["valor"] for c in r["propuesta"]["campos"]}
    assert campos["costo"] == 26000 and campos["precio"] is None and r["propuesta"]["falta"] == "precio"
    assert campos["unidad"] == "bultos"  # la del cemento blanco que ya existe, no "kilos"


def test_el_precio_dicho_con_un_borrador_abierto_va_al_borrador_y_no_a_otro_producto(con):
    frase = "registra el cemento blanco de 25 kilos a 30 mil"
    a, _ = charla({frase: {"accion": "crear_producto", "nombre": "cemento blanco de 25 kilos", "precio": 30000, "costo": None,
                           "unidad": None, "categoria": None, "codigo": None, "cantidad": None, "minimo": None, "maximo": None,
                           "iva": None},
                   "mejor a treinta y dos mil quinientos": {"accion": "precio", "producto": "cemento blanco de 25 kilos", "precio": 32500}})
    s = sesion(con, "admin.norte")
    a.reiniciar(s)
    existente = producto(con, "Cemento blanco 20 kg")
    a.procesar(s, frase)
    r = a.procesar(s, "mejor a treinta y dos mil quinientos")["asistente"]
    assert r["propuesta"]["accion"] == "crear_producto"
    assert {c["clave"]: c["valor"] for c in r["propuesta"]["campos"]}["precio"] == 32500
    assert producto(con, "Cemento blanco 20 kg")["precio"] == existente["precio"]


def test_para_cambiar_datos_una_coincidencia_debil_se_pregunta(con):
    frase = "sube el yeso de 40 kilos a 50 mil"
    a, _ = charla({frase: {"accion": "precio", "producto": "yeso de 40 kilos", "precio": 50000}})
    s = sesion(con, "admin.norte")
    a.reiniciar(s)
    r = a.procesar(s, frase)["asistente"]
    assert "propuesta" not in r and r["texto"] == "¿Te refieres a Yeso 25 kg?"
    r = a.procesar(s, "sí")["asistente"]
    assert r["propuesta"]["producto"] == "Yeso 25 kg" and r["propuesta"]["estado"] == "pendiente"
    a.procesar(s, "no")


def test_la_nota_se_guarda_con_las_palabras_de_quien_habla(con):
    frase = "Recuerda que el proveedor de cemento entrega los martes"
    a, _ = charla({frase: {"accion": "recordar", "texto": "El proveedor de cemento blanco 20 kg entrega los martes."}})
    s = sesion(con, "admin.norte")
    a.procesar(s, frase)
    assert a.notas(con)[0]["texto"] == "El proveedor de cemento entrega los martes."


def test_textos_rotos_del_modelo_no_se_usan(con):
    a, _ = charla({"¿Cuánto cemento gris hay?": {"accion": "existencia", "producto": "cemento gris", "sede": None},
                   "¿y a cómo lo vendemos?": {"accion": "info_producto",
                                              "producto": "cemento gris 50 kg','precio':33500} nunca das cifras"}})
    s = sesion(con, "dueno")
    a.reiniciar(s)
    a.procesar(s, "¿Cuánto cemento gris hay?")
    r = a.procesar(s, "¿y a cómo lo vendemos?")["asistente"]
    assert r["texto"].startswith("Cemento gris 50 kg:\nPrecio de venta")


def test_lo_que_el_modelo_inventa_en_un_producto_nuevo_no_se_usa(con):
    frase = "Registra un producto nuevo: silicona gris de 280 mililitros, nos cuesta 9 mil"
    a, _ = charla({frase: {"accion": "crear_producto", "nombre": "silicona gris de 280 mililitros", "precio": 9000, "costo": 9000,
                           "unidad": "metros", "categoria": "acabados", "codigo": None, "cantidad": 0, "minimo": 10,
                           "maximo": 50, "iva": 0}})
    s = sesion(con, "admin.norte")
    a.reiniciar(s)
    campos = {c["clave"]: c["valor"] for c in a.procesar(s, frase)["asistente"]["propuesta"]["campos"]}
    parecida = producto(con, "Silicona transparente 280 ml")
    assert campos["iva"] == 19 and campos["unidad"] == "unidades" and campos["categoria_id"] == parecida["categoria_id"]
    assert campos["minimo"] is None and campos["maximo"] is None and campos["cantidad"] is None
    assert campos["costo"] == 9000 and campos["precio"] is None and campos["nombre"] == "Silicona gris 280 ml"
