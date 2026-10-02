"""Pruebas de la web completa sobre una base de demostración temporal.

Recorren las páginas con cada rol, verifican permisos y ejecutan los flujos que escriben
(vender, abonar, contar, comprar, trasladar, administrar). Correr con:
    .venv/bin/python -m pytest tests -q
"""

import sqlite3
import warnings

import pytest

warnings.filterwarnings("ignore", category=DeprecationWarning)
from fastapi.testclient import TestClient  # noqa: E402

import erp.db  # noqa: E402
from erp import demo  # noqa: E402

PAGINAS = {
    "dueno": ["/panel", "/panel/contenido", "/ventas/pos", "/ventas", "/caja", "/caja/historial", "/productos", "/productos/1",
              "/productos/nuevo", "/productos/1/editar", "/inventario", "/inventario?filtro=bajo", "/inventario/movimientos",
              "/conteos", "/traslados", "/traslados/nuevo", "/compras", "/compras?vista=por_pagar", "/compras/nueva",
              "/proveedores", "/proveedores/1", "/clientes", "/clientes?vista=cartera", "/clientes/1", "/reportes",
              "/reportes?vista=productos", "/reportes?vista=inventario", "/reportes?vista=merma", "/reportes?vista=compras",
              "/admin/usuarios", "/admin/sedes", "/admin/sistema", "/auditoria", "/cuenta", "/asistente"],
    "caja.centro": ["/ventas/pos", "/ventas", "/caja", "/clientes", "/productos", "/inventario", "/cuenta", "/asistente"],
    "bodega.centro": ["/conteos", "/inventario", "/traslados", "/compras", "/productos", "/asistente"],
}
PROHIBIDAS = {
    "caja.centro": ["/panel", "/conteos", "/compras", "/reportes", "/admin/usuarios", "/auditoria", "/traslados"],
    "bodega.centro": ["/panel", "/ventas/pos", "/caja", "/compras/nueva", "/reportes", "/admin/usuarios"],
    "admin.norte": ["/admin/usuarios", "/admin/sistema"],
}


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    ruta = tmp_path_factory.mktemp("web") / "demo.db"
    con = erp.db.conectar(ruta)
    demo.generar(con, dias=12, aviso=lambda *_: None)
    con.close()
    original = erp.db.RUTA_DB
    erp.db.RUTA_DB = ruta
    yield ruta
    erp.db.RUTA_DB = original


def cliente(base, usuario: str, clave: str = demo.CLAVE_DEMO) -> TestClient:
    from erp.app import crear_app
    c = TestClient(crear_app(), base_url="https://testserver")
    r = c.post("/ingresar", data={"usuario": usuario, "clave": clave}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return c


@pytest.mark.parametrize("usuario", list(PAGINAS))
def test_paginas_cargan_por_rol(base, usuario):
    c = cliente(base, usuario)
    for ruta in PAGINAS[usuario]:
        r = c.get(ruta)
        assert r.status_code == 200, f"{usuario} {ruta}: {r.status_code}"


@pytest.mark.parametrize("usuario", list(PROHIBIDAS))
def test_permisos_por_rol(base, usuario):
    c = cliente(base, usuario)
    for ruta in PROHIBIDAS[usuario]:
        assert c.get(ruta).status_code == 403, f"{usuario} no debería ver {ruta}"


def test_sin_sesion_redirige_al_ingreso(base):
    from erp.app import crear_app
    c = TestClient(crear_app(), base_url="https://testserver")
    r = c.get("/panel", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/ingresar")
    assert c.get("/api/productos?q=tubo").status_code == 401


def test_post_desde_otro_sitio_se_rechaza(base):
    c = cliente(base, "dueno")
    r = c.post("/admin/sedes", data={"nombre": "Pirata"}, headers={"Origin": "https://sitio-malo.example"})
    assert r.status_code == 403


def test_venta_y_abono_por_la_web(base):
    c = cliente(base, "caja.centro")
    p = c.get("/api/productos/codigo/7702001000123").json()["producto"]
    r = c.post("/api/ventas", json={"items": [{"producto_id": p["id"], "cantidad": 2}], "medio_pago": "efectivo", "recibido": 50000})
    assert r.status_code == 200, r.text
    assert r.json()["venta"]["cambio"] == 50000 - 2 * p["precio"]
    assert c.get("/api/productos/codigo/7702001000123").json()["producto"]["existencia"] == p["existencia"] - 2
    # Sin cupo suficiente el crédito se rechaza con un mensaje claro
    cli = c.get("/api/clientes?q=diego").json()["clientes"][0]
    r = c.post("/api/ventas", json={"items": [{"producto_id": p["id"], "cantidad": 1000}], "medio_pago": "credito", "cliente_id": cli["id"]})
    assert r.status_code == 422 and "error" in r.json()


def test_conteo_por_la_web_con_ordenes_escritas(base):
    c = cliente(base, "bodega.centro")
    r = c.post("/conteos", data={"tipo": "general", "nombre": "Prueba web", "ciego": "1"}, headers={"HX-Request": "true"})
    destino = r.headers["HX-Redirect"]
    conteo_id = int(destino.rsplit("/", 1)[1])
    p = c.get("/api/productos/codigo/7705004000111").json()["producto"]
    e = c.post(f"/api/conteos/{conteo_id}/texto", json={"producto_id": p["id"], "texto": "Puntilla 2 pulgadas con cabeza. Hay 16 kilos."}).json()
    assert e["producto"]["contado"] == 16 and "sistema" not in e["producto"]  # ciego: no revela el sistema
    e = c.post(f"/api/conteos/{conteo_id}/texto", json={"producto_id": p["id"], "texto": "hay 20 tornillos y 30 cables"}).json()
    assert e["producto"]["contado"] == 16 and "otro producto" in e["respuesta"]
    e = c.post(f"/api/conteos/{conteo_id}/texto", json={"producto_id": p["id"], "texto": "Hola."}).json()
    assert e["producto"]["contado"] == 16
    # El bodeguero no aprueba: lo hace la administración
    assert c.post(f"/conteos/{conteo_id}/aprobar").status_code == 403
    admin = cliente(base, "dueno")
    assert admin.post(f"/conteos/{conteo_id}/aprobar", headers={"HX-Request": "true"}).status_code == 200
    assert admin.get("/api/productos/codigo/7705004000111").json()["producto"]["existencia"] == 16


def test_administracion_de_usuarios(base):
    c = cliente(base, "dueno")
    h = {"HX-Request": "true"}
    r = c.post("/admin/usuarios", data={"nombre": "Nuevo Cajero", "usuario": "caja.nuevo", "rol": "cajero", "sede_id": "1",
                                        "clave": "segura123"}, headers=h)
    assert "HX-Redirect" in r.headers
    assert c.post("/admin/usuarios", data={"nombre": "X", "usuario": "Con Espacio", "rol": "cajero", "sede_id": "1",
                                           "clave": "segura123"}, headers=h).headers.get("HX-Trigger")
    nuevo = cliente(base, "caja.nuevo", "segura123")
    assert nuevo.get("/ventas/pos").status_code == 200
    # El dueño no puede quitarse su propio acceso
    uid = sqlite3.connect(base).execute("SELECT id FROM usuarios WHERE usuario = 'dueno'").fetchone()[0]
    r = c.post(f"/admin/usuarios/{uid}", data={"nombre": "Dueño", "usuario": "dueno", "rol": "cajero", "sede_id": "1", "activo": "1"}, headers=h)
    assert "No puedes" in r.headers.get("HX-Trigger", "")


def test_respaldo_descarga_una_base_valida(base, tmp_path):
    c = cliente(base, "dueno")
    r = c.get("/admin/respaldo")
    assert r.status_code == 200
    copia = tmp_path / "copia.db"
    copia.write_bytes(r.content)
    assert sqlite3.connect(copia).execute("SELECT COUNT(*) FROM productos").fetchone()[0] > 100


def test_csv_para_excel(base):
    c = cliente(base, "dueno")
    r = c.get("/reportes/ventas.csv")
    assert r.status_code == 200 and r.text.startswith("﻿Número;Fecha;Sede;Vendedor")


def test_kardex_cuadra_despues_de_todo(base):
    con = sqlite3.connect(base)
    descuadres = con.execute("""SELECT COUNT(*) FROM existencias e WHERE ABS(e.cantidad - COALESCE(
        (SELECT SUM(m.cantidad) FROM movimientos m WHERE m.producto_id = e.producto_id AND m.sede_id = e.sede_id), 0)) > 1e-6""").fetchone()[0]
    assert descuadres == 0


def test_asistente_por_la_web(base):
    from erp.asistente.conversacion import Asistente
    from tests.test_asistente import CerebroGuion
    c = cliente(base, "dueno")
    c.app.state.asistente = Asistente(CerebroGuion({
        "llegaron 3 bultos de yeso": {"accion": "entrada", "producto": "yeso", "cantidad": 3, "motivo": None}}))
    yeso = c.get("/api/productos?q=yeso").json()["productos"][0]
    r = c.post("/api/asistente/mensaje", json={"texto": "llegaron 3 bultos de yeso"}).json()
    tarjeta = r["mensajes"][-1]
    assert tarjeta["propuesta"]["estado"] == "pendiente" and r["pendiente_id"] == tarjeta["id"]
    r = c.post("/api/asistente/boton", json={"tipo": "confirmar", "mensaje_id": tarjeta["id"]}).json()
    assert "Listo" in r["mensajes"][-1]["texto"] and r["pendiente_id"] is None
    assert c.get("/api/productos?q=yeso").json()["productos"][0]["existencia"] == yeso["existencia"] + 3
    # Confirmar dos veces la misma tarjeta no suma dos veces
    r = c.post("/api/asistente/boton", json={"tipo": "confirmar", "mensaje_id": tarjeta["id"]})
    assert r.status_code == 422 and "ya no está pendiente" in r.json()["error"]
    assert c.get("/api/productos?q=yeso").json()["productos"][0]["existencia"] == yeso["existencia"] + 3
    assert c.post("/api/asistente/nueva").json()["mensajes"] == []


def test_asistente_sin_modelo_no_rompe_la_pagina(base):
    c = cliente(base, "caja.centro")
    assert "modelo de lenguaje local" in c.get("/asistente").text
    r = c.post("/api/asistente/mensaje", json={"texto": "¿cuánto cemento hay?"}).json()
    assert "sin voz" in r["mensajes"][-1]["texto"]


def test_en_la_web_la_sesion_sobrevive_a_una_instancia_nueva(tmp_path):
    """En Vercel cada instancia arma su propia base: la cookie firmada debe seguir sirviendo."""
    import erp.auth
    from erp.app import crear_app
    original = erp.db.RUTA_DB
    try:
        bases = []
        for k in range(2):
            ruta = tmp_path / f"instancia{k}.db"
            con = erp.db.conectar(ruta)
            demo.generar(con, dias=3, aviso=lambda *_: None)
            con.close()
            bases.append(ruta)
        erp.db.RUTA_DB = bases[0]
        primera = TestClient(crear_app(nube=True), base_url="https://testserver")
        token = primera.post("/ingresar", data={"usuario": "dueno", "clave": demo.CLAVE_DEMO}, follow_redirects=False).cookies["sesion"]
        erp.db.RUTA_DB = bases[1]
        segunda = TestClient(crear_app(nube=True), base_url="https://testserver")
        segunda.cookies.set("sesion", token)
        assert segunda.get("/panel", follow_redirects=False).status_code == 200
        segunda.cookies.set("sesion", token[:-3] + "000")
        assert segunda.get("/panel", follow_redirects=False).status_code == 303
    finally:
        erp.db.RUTA_DB = original
        erp.auth.SESIONES_FIRMADAS = False
