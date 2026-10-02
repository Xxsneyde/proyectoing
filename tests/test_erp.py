"""Reglas de negocio del ERP. Correr con: .venv/bin/python -m pytest tests -q"""

import pytest

from erp import auth, caja, cartera, compras, conteos, traslados, ventas
from erp.db import ErrorNegocio, conectar, inicializar, transaccion, uno, valor
from erp.inventario import existencia, mover


@pytest.fixture
def con(tmp_path):
    c = conectar(tmp_path / "prueba.db")
    inicializar(c)
    with transaccion(c):
        c.execute("INSERT INTO sedes (id, nombre) VALUES (1, 'Centro'), (2, 'Norte')")
        clave = auth.hash_clave("clave-segura")
        c.execute("INSERT INTO usuarios (id, nombre, usuario, clave_hash, rol, sede_id) VALUES "
                  "(1, 'Dueño', 'dueno', ?, 'admin', NULL), (2, 'Cajero', 'caja', ?, 'cajero', 1)", (clave, clave))
        c.execute("INSERT INTO proveedores (id, nit, nombre, plazo_dias) VALUES (1, '900', 'Proveedor', 30)")
        c.execute("INSERT INTO productos (id, codigo, nombre, unidad, costo, precio, iva, proveedor_id) VALUES "
                  "(1, '111', 'Tubo PVC presión 1/2 pulgada', 'unidades', 8000, 14900, 19, 1), "
                  "(2, '222', 'Cemento gris 50 kg', 'bultos', 20000, 33500, 19, 1), "
                  "(3, '333', 'Tornillo drywall 6x1 (caja x100)', 'cajas', 5000, 9500, 19, 1)")
        c.execute("INSERT INTO clientes (id, documento, nombre, cupo_credito, plazo_dias) VALUES (1, '123', 'Constructora', 100000, 30)")
        for pid, q in ((1, 30), (2, 10), (3, 5)):
            mover(c, pid, 1, "inicial", q)
        caja.abrir(c, 1, 2, 100_000)
    return c


def vender(con, items, medio="efectivo", **kw):
    with transaccion(con):
        return ventas.vender(con, sede_id=1, usuario_id=2, items=items, medio_pago=medio, **kw)


def test_iva_se_separa_del_precio():
    linea = ventas.calcular_linea(11900, 2, 0, 19)
    assert linea == {"bruto": 23800, "total": 23800, "base": 20000, "iva": 3800}


def test_venta_descuenta_inventario_y_deja_kardex(con):
    v = vender(con, [{"producto_id": 1, "cantidad": 3}], recibido=50000)
    assert existencia(con, 1, 1) == 27
    assert v["total"] == 44700 and v["cambio"] == 5300
    mov = uno(con, "SELECT * FROM movimientos WHERE referencia = ?", f"venta:{v['id']}")
    assert mov["cantidad"] == -3 and mov["saldo"] == 27 and mov["usuario_id"] == 2


def test_no_se_vende_lo_que_no_hay(con):
    with pytest.raises(ErrorNegocio, match="No hay suficiente"):
        vender(con, [{"producto_id": 3, "cantidad": 6}])
    assert existencia(con, 3, 1) == 5  # la transacción no dejó nada a medias


def test_venta_exige_caja_abierta(con):
    with pytest.raises(ErrorNegocio, match="Abre tu caja"):
        with transaccion(con):
            ventas.vender(con, sede_id=1, usuario_id=1, items=[{"producto_id": 1, "cantidad": 1}], medio_pago="tarjeta")


def test_credito_respeta_cupo_y_abonos(con):
    v = vender(con, [{"producto_id": 2, "cantidad": 2}], medio="credito", cliente_id=1)  # 67.000
    with pytest.raises(ErrorNegocio, match="cupo"):
        vender(con, [{"producto_id": 2, "cantidad": 1}], medio="credito", cliente_id=1)  # +33.500 > 100.000
    with transaccion(con):
        with pytest.raises(ErrorNegocio, match="supera el saldo"):
            cartera.abonar(con, v["id"], 70000, "efectivo", 2, 1)
        assert cartera.abonar(con, v["id"], 40000, "efectivo", 2, 1) == 27000
        assert cartera.abonar(con, v["id"], 27000, "transferencia", 2, 1) == 0
    assert uno(con, "SELECT estado FROM ventas WHERE id = ?", v["id"])["estado"] == "pagada"


def test_anular_devuelve_mercancia_y_dinero(con):
    v = vender(con, [{"producto_id": 1, "cantidad": 2}])
    with transaccion(con):
        ventas.anular(con, v["id"], 2, 1, "Error de digitación")
    assert existencia(con, 1, 1) == 30
    r = caja.resumen(con, caja.caja_abierta(con, 1, 2)["id"])
    assert r["esperado"] == 100_000  # entró la venta y salió el reembolso
    with pytest.raises(ErrorNegocio, match="ya está anulada"):
        with transaccion(con):
            ventas.anular(con, v["id"], 2, 1, "otra vez")


def test_devolucion_parcial(con):
    v = vender(con, [{"producto_id": 1, "cantidad": 4}])
    item = uno(con, "SELECT id FROM venta_items WHERE venta_id = ?", v["id"])
    with transaccion(con):
        r = ventas.devolver(con, v["id"], {item["id"]: 1}, 2, 1, "No le sirvió")
    assert r["total"] == 14900 and existencia(con, 1, 1) == 27
    with pytest.raises(ErrorNegocio, match="más de lo vendido"):
        with transaccion(con):
            ventas.devolver(con, v["id"], {item["id"]: 4}, 2, 1, "otra")


def test_compra_parcial_actualiza_stock_y_costo_promedio(con):
    with transaccion(con):
        cid = compras.crear(con, 1, 1, [{"producto_id": 2, "cantidad": 10, "costo_unitario": 26000}], 1)
        compras.enviar(con, cid, 1)
        item = uno(con, "SELECT id FROM compra_items WHERE compra_id = ?", cid)
        compras.recibir(con, cid, {item["id"]: 10}, 1, "FV-1")
    assert existencia(con, 2, 1) == 20
    # 10 a $20.000 + 10 a $26.000 -> $23.000
    assert valor(con, "SELECT costo FROM productos WHERE id = 2") == 23000
    assert uno(con, "SELECT estado FROM compras WHERE id = ?", cid)["estado"] == "recibida"
    with transaccion(con):
        compras.pagar(con, cid, 1)
    assert compras.cuentas_por_pagar(con) == []


def test_traslado_en_transito_y_faltantes(con):
    with transaccion(con):
        tid = traslados.crear(con, 1, 2, [{"producto_id": 1, "cantidad": 10}], 1)
        traslados.enviar(con, tid, 1)
    assert existencia(con, 1, 1) == 20 and existencia(con, 1, 2) == 0  # en camino no está en ninguna
    item = uno(con, "SELECT id FROM traslado_items WHERE traslado_id = ?", tid)
    with transaccion(con):
        faltantes = traslados.recibir(con, tid, {item["id"]: 8}, 1)
    assert existencia(con, 1, 2) == 8 and faltantes


def test_anular_traslado_en_transito_regresa_al_origen(con):
    with transaccion(con):
        tid = traslados.crear(con, 1, 2, [{"producto_id": 2, "cantidad": 4}], 1)
        traslados.enviar(con, tid, 1)
        traslados.anular(con, tid, 1, "Se canceló el pedido")
    assert existencia(con, 2, 1) == 10


def test_conteo_solo_ajusta_la_diferencia_aunque_se_venda_mientras_tanto(con):
    with transaccion(con):
        cid = conteos.crear(con, 1, "Prueba", "general", "", True, 1)
        conteos.registrar(con, cid, 1, 28, 1, "escaner")  # sistema 30, hay 28: faltan 2
    vender(con, [{"producto_id": 1, "cantidad": 5}])     # se venden 5 después de contar
    with transaccion(con):
        conteos.aprobar(con, cid, 1)
    assert existencia(con, 1, 1) == 23                    # 30 - 5 vendidos - 2 faltantes


def test_conteo_sumar_deshacer_y_danados(con):
    with transaccion(con):
        cid = conteos.crear(con, 1, "Prueba", "general", "", False, 1)
        conteos.registrar(con, cid, 2, 6, 1)
        conteos.registrar(con, cid, 2, 4, 1, modo="sumar")    # mismo producto en otra ubicación
        assert conteos.item(con, cid, 2)["contado"] == 10
        assert "volví a 6" in conteos.deshacer(con, cid, 2)
        conteos.registrar(con, cid, 2, 10, 1)
        conteos.marcar_danados(con, cid, 2, 1, 1)
        conteos.aprobar(con, cid, 1)
    assert existencia(con, 2, 1) == 9                         # 10 contados, 1 dañado sale


def test_voz_en_conteo_ciego_no_revela_el_sistema(con):
    with transaccion(con):
        cid = conteos.crear(con, 1, "Ciego", "general", "", True, 1)
        r = conteos.aplicar_voz(con, cid, 1, {"accion": "conteo", "cantidad": 25}, 1, "hay 25")
        assert r == "Registradas 25."
        r = conteos.aplicar_voz(con, cid, 1, {"accion": "consulta"}, 1, "cuánto dice el sistema")
        assert "ciego" in r
        r = conteos.aplicar_voz(con, cid, 1, {"accion": "desconocido"}, 1, "hay 20 cementos")
        assert "otro producto" in r


def test_cierre_de_caja_con_faltante(con):
    vender(con, [{"producto_id": 1, "cantidad": 1}])
    caja_id = caja.caja_abierta(con, 1, 2)["id"]
    with transaccion(con):
        r = caja.cerrar(con, caja_id, 110_000, 2)
    assert r["esperado"] == 114_900 and r["diferencia"] == -4_900


def test_login_bloquea_tras_intentos_fallidos(con):
    with transaccion(con):
        assert auth.iniciar_sesion(con, "dueno", "clave-segura")
        for _ in range(5):
            with pytest.raises(ErrorNegocio, match="incorrectos"):
                auth.iniciar_sesion(con, "caja", "mala")
        with pytest.raises(ErrorNegocio, match="Demasiados intentos"):
            auth.iniciar_sesion(con, "caja", "clave-segura")


def test_permisos_por_rol():
    cajero, bodeguero = {"rol": "cajero"}, {"rol": "bodeguero"}
    assert auth.puede(cajero, "ventas") and not auth.puede(cajero, "conteos")
    assert auth.puede(bodeguero, "conteos") and not auth.puede(bodeguero, "conteos.aprobar")
    assert not auth.puede(bodeguero, "ventas") and not auth.puede(None, "panel")


def test_deshacer_en_conteo_deshace_la_ultima_accion(con):
    with transaccion(con):
        cid = conteos.crear(con, 1, "Prueba", "general", "", True, 1)
        conteos.registrar(con, cid, 1, 17, 1)
        conteos.registrar(con, cid, 1, 20, 1)
        conteos.marcar_danados(con, cid, 1, 2, 1)
        assert "quité los dañados" in conteos.deshacer(con, cid, 1)
        assert conteos.item(con, cid, 1)["danados"] == 0 and conteos.item(con, cid, 1)["contado"] == 20
        assert "volví a 17" in conteos.deshacer(con, cid, 1)


def test_voz_avisa_cantidades_muy_lejos_de_lo_esperado_sin_revelarlas(con):
    with transaccion(con):
        cid = conteos.crear(con, 1, "Ciego", "general", "", True, 1)
        r = conteos.aplicar_voz(con, cid, 1, {"accion": "conteo", "cantidad": 50}, 1, "ahí cincuenta a todos")
        assert "lejos de lo esperado" in r and "30" not in r   # el sistema dice 30
        r = conteos.aplicar_voz(con, cid, 1, {"accion": "conteo", "cantidad": 28}, 1, "hay 28")
        assert r == "Registradas 28."
