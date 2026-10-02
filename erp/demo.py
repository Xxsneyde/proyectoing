"""Datos de demostración: catálogo de ferretería y 90 días de operación simulada.

La historia se genera llamando a las mismas funciones de negocio que usa la
tienda (vender, recibir compras, trasladar, contar), con un reloj simulado.
Así los datos son coherentes y la simulación sirve también de prueba.

Uso: .venv/bin/python -m erp.demo [--dias 90] [--reiniciar]
"""

import argparse
import math
import random
from datetime import datetime, timedelta

from erp import caja, cartera, compras, conteos, traslados, ventas
from erp.auth import hash_clave
from erp.db import (RUTA_DB, ErrorNegocio, conectar, fijar_reloj, guardar_config, inicializar,
                    todos, transaccion, uno, valor)
from erp.inventario import existencia, mover

CLAVE_DEMO = "demo2026"

SEDES = [("Centro", "Cra. 7 # 12-40", "601 555 0101", 1.0),
         ("Norte", "Av. 19 # 140-22", "601 555 0102", 0.65),
         ("Sur", "Cll. 40 Sur # 24-10", "601 555 0103", 0.45)]

USUARIOS = [  # usuario, nombre, rol, sede (None = todas)
    ("dueno", "Dueño de la ferretería", "admin", None),
    ("admin.norte", "Laura Méndez", "administrador", "Norte"),
    ("caja.centro", "Camilo Ríos", "cajero", "Centro"),
    ("caja.norte", "Valentina Ortiz", "cajero", "Norte"),
    ("caja.sur", "Julián Pardo", "cajero", "Sur"),
    ("bodega.centro", "Andrés Muñoz", "bodeguero", "Centro"),
    ("bodega.norte", "Sergio Patiño", "bodeguero", "Norte"),
]

PROVEEDORES = [  # nit, nombre, contacto, plazo
    ("900123456-1", "Distribuidora Andina de Tubos S.A.S.", "Marcela Rojas", 30),
    ("900234567-2", "Eléctricos del Valle Ltda.", "Hernán Salazar", 30),
    ("900345678-3", "Fijaciones y Tornillos S.A.S.", "Diana Cárdenas", 45),
    ("900456789-4", "Herramientas Pro Colombia S.A.S.", "Óscar Beltrán", 60),
    ("900567890-5", "Pinturas Tropical S.A.", "Natalia Vargas", 30),
    ("900678901-6", "Agregados y Cementos del Centro S.A.S.", "Fabio Guzmán", 15),
    ("900789012-7", "Grifería Nacional S.A.S.", "Paola Restrepo", 30),
    ("900890123-8", "Seguridad y Jardín Industrial Ltda.", "Ricardo Peña", 30),
]

# (categoría, ubicación, proveedor, [(nombre, unidad, precio, popularidad, código fijo)])
CATALOGO = [
    ("Tubería y accesorios PVC", "pasillo 1", 0, [
        ("Tubo PVC presión 1/2 pulgada", "unidades", 14900, 6, "7702001000123"),
        ("Tubo PVC presión 3/4 pulgada", "unidades", 21900, 4, None),
        ("Tubo PVC presión 1 pulgada", "unidades", 32900, 2, None),
        ("Tubo PVC sanitario 2 pulgadas", "unidades", 28900, 3, None),
        ("Tubo PVC sanitario 3 pulgadas", "unidades", 45900, 2, None),
        ("Tubo PVC sanitario 4 pulgadas", "unidades", 62900, 2, None),
        ("Codo PVC presión 1/2 pulgada", "unidades", 900, 9, None),
        ("Codo PVC presión 3/4 pulgada", "unidades", 1500, 6, None),
        ("Codo PVC presión 1 pulgada", "unidades", 2600, 3, None),
        ("Tee PVC presión 1/2 pulgada", "unidades", 1200, 7, None),
        ("Tee PVC presión 3/4 pulgada", "unidades", 1900, 4, None),
        ("Unión PVC presión 1/2 pulgada", "unidades", 800, 7, None),
        ("Unión PVC presión 3/4 pulgada", "unidades", 1300, 4, None),
        ("Adaptador macho PVC 1/2 pulgada", "unidades", 900, 6, None),
        ("Adaptador macho PVC 3/4 pulgada", "unidades", 1400, 4, None),
        ("Codo sanitario PVC 2 pulgadas", "unidades", 3900, 4, None),
        ("Codo sanitario PVC 3 pulgadas", "unidades", 6900, 2, None),
        ("Codo sanitario PVC 4 pulgadas", "unidades", 9900, 2, None),
        ("Soldadura líquida PVC 1/32 galón", "unidades", 9900, 5, None),
        ("Limpiador PVC 1/32 galón", "unidades", 7900, 3, None),
    ]),
    ("Eléctricos", "pasillo 2", 1, [
        ("Cable THHN 14 AWG", "metros", 2400, 6, None),
        ("Cable THHN 12 AWG", "metros", 3200, 7, "7704003000789"),
        ("Cable THHN 10 AWG", "metros", 4900, 3, None),
        ("Cable dúplex 2x14", "metros", 3800, 4, None),
        ("Bombillo LED 9 W", "unidades", 6500, 8, None),
        ("Bombillo LED 12 W", "unidades", 8900, 6, None),
        ("Bombillo LED 18 W", "unidades", 14900, 3, None),
        ("Toma doble con polo a tierra", "unidades", 7900, 6, None),
        ("Interruptor sencillo", "unidades", 6900, 6, None),
        ("Interruptor doble", "unidades", 9900, 3, None),
        ("Cinta aislante 3M negra", "unidades", 3500, 9, None),
        ("Breaker enchufable 15 A", "unidades", 18900, 2, None),
        ("Breaker enchufable 20 A", "unidades", 18900, 2, None),
        ("Breaker enchufable 30 A", "unidades", 21900, 1, None),
        ("Caja octagonal metálica", "unidades", 2900, 4, None),
        ("Caja rectangular plástica", "unidades", 2400, 4, None),
        ("Tubo conduit PVC 1/2 pulgada x 3 m", "unidades", 5900, 4, None),
        ("Clavija con polo a tierra", "unidades", 4900, 4, None),
        ("Extensión eléctrica 5 m", "unidades", 24900, 2, None),
        ("Reflector LED 50 W", "unidades", 59900, 1, None),
    ]),
    ("Tornillería y fijación", "pasillo 3", 2, [
        ("Tornillo drywall 6x1 (caja x100)", "cajas", 9500, 6, "7703002000456"),
        ("Tornillo drywall 6x1-1/2 (caja x100)", "cajas", 11500, 4, None),
        ("Tornillo drywall 8x2 (caja x100)", "cajas", 18900, 2, None),
        ("Tornillo autoperforante 8x1/2 (caja x100)", "cajas", 14900, 3, None),
        ("Puntilla 1 pulgada con cabeza", "kilos", 10500, 3, None),
        ("Puntilla 1-1/2 pulgadas con cabeza", "kilos", 10200, 4, None),
        ("Puntilla 2 pulgadas con cabeza", "kilos", 9800, 7, "7705004000111"),
        ("Puntilla 2-1/2 pulgadas con cabeza", "kilos", 9800, 5, None),
        ("Puntilla 3 pulgadas con cabeza", "kilos", 9800, 4, None),
        ("Chazo plástico 1/4 (bolsa x100)", "paquetes", 4500, 6, None),
        ("Chazo plástico 5/16 (bolsa x100)", "paquetes", 5900, 4, None),
        ("Tuerca hexagonal 3/8 (bolsa x50)", "paquetes", 6900, 2, None),
        ("Perno expansivo 3/8 x 3 pulgadas", "unidades", 1900, 4, None),
        ("Grapa para cable 7 mm (caja x100)", "cajas", 4500, 3, None),
        ("Alambre negro calibre 18", "kilos", 8900, 5, None),
    ]),
    ("Herramientas manuales", "pasillo 4", 3, [
        ("Martillo de uña 16 oz", "unidades", 28000, 1.5, None),
        ("Destornillador de pala 1/4", "unidades", 9900, 2, None),
        ("Destornillador de estrella 1/4", "unidades", 9900, 2, None),
        ("Juego de destornilladores 6 piezas", "unidades", 32900, 1, None),
        ("Alicate universal 8 pulgadas", "unidades", 24900, 1.5, None),
        ("Pinza punta larga 6 pulgadas", "unidades", 21900, 1, None),
        ("Llave expansiva 10 pulgadas", "unidades", 34900, 1, None),
        ("Llave para tubo 14 pulgadas", "unidades", 49900, 0.6, None),
        ("Flexómetro 5 m", "unidades", 15900, 2.5, None),
        ("Flexómetro 8 m", "unidades", 24900, 1.2, None),
        ("Nivel de burbuja 24 pulgadas", "unidades", 29900, 0.8, None),
        ("Segueta con marco", "unidades", 22900, 0.8, None),
        ("Hoja de segueta", "unidades", 3900, 3, None),
        ("Espátula 3 pulgadas", "unidades", 6900, 2, None),
        ("Llana metálica", "unidades", 18900, 1, None),
        ("Palustre", "unidades", 16900, 1, None),
        ("Pica", "unidades", 49900, 0.5, None),
        ("Pala cuadrada", "unidades", 39900, 0.7, None),
        ("Carretilla", "unidades", 189000, 0.3, None),
        ("Machete 24 pulgadas", "unidades", 22900, 0.6, None),
        ("Escuadra 12 pulgadas", "unidades", 15900, 0.6, None),
        ("Bisturí", "unidades", 4900, 2.5, None),
    ]),
    ("Herramientas eléctricas", "vitrina", 3, [
        ("Taladro percutor 1/2 pulgada 650 W", "unidades", 239000, 0.4, None),
        ("Pulidora 4-1/2 pulgadas 750 W", "unidades", 219000, 0.4, None),
        ("Atornillador inalámbrico 12 V", "unidades", 289000, 0.25, None),
        ("Sierra circular 7-1/4 pulgadas", "unidades", 399000, 0.15, None),
        ("Caladora 500 W", "unidades", 199000, 0.2, None),
        ("Broca para concreto 1/4", "unidades", 5900, 5, None),
        ("Broca para concreto 5/16", "unidades", 6900, 4, None),
        ("Broca para concreto 3/8", "unidades", 7900, 3, None),
        ("Juego de brocas para metal 13 piezas", "unidades", 39900, 0.8, None),
        ("Disco de corte metal 4-1/2", "unidades", 3900, 6, None),
        ("Disco diamantado 4-1/2", "unidades", 24900, 1.5, None),
    ]),
    ("Pinturas", "pasillo 6", 4, [
        ("Pintura vinilo tipo 1 blanco", "galones", 58000, 3, None),
        ("Pintura vinilo tipo 2 blanco", "galones", 39000, 3, None),
        ("Pintura vinilo color beige", "galones", 62000, 1.2, None),
        ("Pintura vinilo blanco cuñete 5 galones", "unidades", 189000, 0.6, None),
        ("Esmalte sintético blanco", "galones", 79000, 1, None),
        ("Esmalte sintético negro 1/4 galón", "unidades", 26900, 1.2, None),
        ("Anticorrosivo gris", "galones", 69000, 1, None),
        ("Thinner", "galones", 22900, 2.5, None),
        ("Brocha 1 pulgada", "unidades", 3500, 3, None),
        ("Brocha 2 pulgadas", "unidades", 5500, 4, None),
        ("Brocha 3 pulgadas", "unidades", 7500, 3, None),
        ("Brocha 4 pulgadas", "unidades", 9500, 2, None),
        ("Rodillo 9 pulgadas", "unidades", 12900, 2, None),
        ("Bandeja para rodillo", "unidades", 7900, 1.5, None),
        ("Lija de agua número 80", "unidades", 1800, 5, None),
        ("Lija de agua número 120", "unidades", 1800, 6, None),
        ("Lija de agua número 220", "unidades", 1800, 4, None),
        ("Masilla para drywall", "galones", 32900, 1.5, None),
        ("Estuco plástico", "galones", 28900, 1.5, None),
        ("Cinta de enmascarar 1 pulgada", "unidades", 4900, 5, None),
    ]),
    ("Construcción", "bodega", 5, [
        ("Cemento gris 50 kg", "bultos", 33500, 8, "7706005000222"),
        ("Cemento blanco 20 kg", "bultos", 48900, 1, None),
        ("Arena de río", "bultos", 9000, 5, None),
        ("Gravilla", "bultos", 10500, 3, None),
        ("Varilla corrugada 3/8 x 6 m", "unidades", 19900, 3, None),
        ("Varilla corrugada 1/2 x 6 m", "unidades", 33900, 2, None),
        ("Bloque número 5", "unidades", 2300, 3, None),
        ("Ladrillo tolete", "unidades", 1100, 3, None),
        ("Pegante cerámico 25 kg", "bultos", 22900, 3, None),
        ("Boquilla blanca 2 kg", "unidades", 8900, 2.5, None),
        ("Teja fibrocemento número 6", "unidades", 49900, 0.8, None),
        ("Impermeabilizante", "galones", 69900, 1, None),
        ("Yeso 25 kg", "bultos", 24900, 1, None),
        ("Placa drywall 1/2 pulgada", "unidades", 32900, 2, None),
        ("Perfil drywall omega 3 m", "unidades", 8900, 2.5, None),
    ]),
    ("Plomería y grifería", "pasillo 7", 6, [
        ("Llave de paso 1/2 pulgada", "unidades", 18000, 2.5, None),
        ("Llave terminal 1/2 pulgada", "unidades", 14900, 2, None),
        ("Grifería lavamanos sencilla", "unidades", 49900, 0.8, None),
        ("Grifería lavaplatos cuello de ganso", "unidades", 79900, 0.6, None),
        ("Ducha sencilla", "unidades", 22900, 1.2, None),
        ("Sifón para lavamanos", "unidades", 9900, 2, None),
        ("Manguera para lavadora", "unidades", 9900, 1.5, None),
        ("Cinta teflón 1/2 pulgada", "unidades", 1500, 8, None),
        ("Cinta teflón 3/4 pulgada", "unidades", 2500, 4, None),
        ("Flotador para tanque", "unidades", 14900, 1.2, None),
        ("Kit de reparación para sanitario", "unidades", 39900, 0.8, None),
    ]),
    ("Cerrajería", "pasillo 8", 3, [
        ("Candado 40 mm", "unidades", 24000, 1.2, None),
        ("Candado 50 mm", "unidades", 32900, 0.8, None),
        ("Cerradura de pomo", "unidades", 49900, 0.7, None),
        ("Cerradura de seguridad", "unidades", 89900, 0.4, None),
        ("Bisagra 3 pulgadas (par)", "unidades", 6900, 2.5, None),
        ("Pasador 4 pulgadas", "unidades", 5900, 1.5, None),
    ]),
    ("Adhesivos y selladores", "pasillo 6", 4, [
        ("Silicona transparente 280 ml", "unidades", 12900, 4, None),
        ("Adhesivo epóxico", "unidades", 15900, 1.5, None),
        ("Espuma expansiva 300 ml", "unidades", 24900, 1, None),
        ("Pegante de contacto 1/4 galón", "unidades", 22900, 1.2, None),
        ("Cinta doble faz", "unidades", 9900, 1.5, None),
    ]),
    ("Seguridad industrial", "pasillo 8", 7, [
        ("Guantes de carnaza (par)", "unidades", 8500, 4, None),
        ("Guantes de nitrilo (par)", "unidades", 6900, 3, None),
        ("Gafas de seguridad", "unidades", 7900, 2.5, None),
        ("Casco de seguridad", "unidades", 22900, 1, None),
        ("Tapabocas N95", "unidades", 4900, 3, None),
        ("Botas de caucho", "unidades", 39900, 0.8, None),
        ("Chaleco reflectivo", "unidades", 14900, 0.8, None),
    ]),
    ("Jardinería", "pasillo 9", 7, [
        ("Manguera de jardín 1/2 pulgada x 15 m", "unidades", 39900, 0.8, None),
        ("Pistola de riego", "unidades", 14900, 1, None),
        ("Tijera de podar", "unidades", 29900, 0.6, None),
        ("Rastrillo", "unidades", 24900, 0.6, None),
    ]),
]

CLIENTES = [  # documento, nombre, teléfono, cupo, plazo, paga (probabilidad de pagar a tiempo)
    ("901112223-4", "Constructora Los Pinos S.A.S.", "310 555 2001", 8_000_000, 30, 0.95),
    ("901223334-5", "Remodelaciones Hogar Ideal S.A.S.", "311 555 2002", 4_000_000, 30, 0.9),
    ("901334445-6", "Mantenimientos Integrales JR", "312 555 2003", 3_000_000, 30, 0.6),
    ("79456123", "Carlos Ruiz (maestro de obra)", "313 555 2004", 1_500_000, 15, 0.85),
    ("52789456", "Martha Lucía Torres", "314 555 2005", 800_000, 15, 0.9),
    ("80123987", "Jhon Fredy Castaño (electricista)", "315 555 2006", 1_200_000, 30, 0.4),
    ("901445556-7", "Conjunto Residencial Altos del Río", "316 555 2007", 5_000_000, 45, 0.85),
    ("1014567890", "Diego Alejandro Moreno", "317 555 2008", 600_000, 15, 0.3),
]
CLIENTES_CONTADO = [
    ("1020304050", "Ana María Ospina", "318 555 3001"), ("71234567", "Luis Eduardo Gil", "319 555 3002"),
    ("43987654", "Gloria Patricia Henao", "320 555 3003"), ("1098765432", "Kevin Andrés Rojas", "321 555 3004"),
    ("39456789", "Rosa Elena Quintero", "322 555 3005"), ("901556667-8", "Taller Mecánico El Pistón", "323 555 3006"),
]

CANTIDADES = {
    "metros": [2, 3, 5, 5, 8, 10, 10, 15, 20, 25, 50], "kilos": [0.5, 1, 1, 1, 1.5, 2, 2, 3, 5],
    "bultos": [1, 1, 1, 2, 2, 3, 5, 10], "galones": [1, 1, 1, 2], "cajas": [1, 1, 1, 2, 3],
    "paquetes": [1, 1, 2, 3],
}


def ean13(base12: str) -> str:
    suma = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(base12))
    return base12 + str((10 - suma % 10) % 10)


def cantidad_venta(r: random.Random, unidad: str, precio: int) -> float:
    if unidad in CANTIDADES:
        return r.choice(CANTIDADES[unidad])
    if precio < 3000:
        return r.choice([1, 2, 2, 3, 4, 5, 6, 10, 12])
    if precio < 20000:
        return r.choice([1, 1, 1, 2, 2, 3])
    return 1


def generar(con, dias: int = 90, semilla: int = 2026, aviso=print):
    r = random.Random(semilla)
    inicializar(con)
    if valor(con, "SELECT COUNT(*) FROM productos", defecto=0):
        raise SystemExit("La base ya tiene datos. Usa --reiniciar para borrarla y generar la demostración.")
    fin = datetime.now()
    inicio = (fin - timedelta(days=dias)).replace(hour=7, minute=0, second=0, microsecond=0)
    fijar_reloj(inicio)

    with transaccion(con):
        guardar_config(con, "empresa_nombre", "Ferretería La Obra")
        guardar_config(con, "empresa_nit", "900999888-1")
        guardar_config(con, "demo", "1")
        sedes = {}
        for nombre, direccion, tel, _ in SEDES:
            sedes[nombre] = con.execute("INSERT INTO sedes (nombre, direccion, telefono) VALUES (?, ?, ?)",
                                        (nombre, direccion, tel)).lastrowid
        usuarios = {}
        clave = hash_clave(CLAVE_DEMO)
        for usuario, nombre, rol, sede in USUARIOS:
            usuarios[usuario] = con.execute(
                "INSERT INTO usuarios (nombre, usuario, clave_hash, rol, sede_id) VALUES (?, ?, ?, ?, ?)",
                (nombre, usuario, clave, rol, sedes.get(sede))).lastrowid
        proveedores = [con.execute("INSERT INTO proveedores (nit, nombre, contacto, telefono, email, plazo_dias) "
                                   "VALUES (?, ?, ?, ?, ?, ?)",
                                   (nit, nombre, contacto, f"601 444 {i:04d}", f"ventas{i}@proveedor.example", plazo)).lastrowid
                       for i, (nit, nombre, contacto, plazo) in enumerate(PROVEEDORES, 1)]
        productos = []
        n = 0
        for categoria, ubicacion, prov, items in CATALOGO:
            cat_id = con.execute("INSERT INTO categorias (nombre) VALUES (?)", (categoria,)).lastrowid
            for nombre, unidad, precio, pop, codigo in items:
                n += 1
                codigo = codigo or ean13(f"770999{n:06d}")
                costo = round(precio / 1.19 * r.uniform(0.62, 0.78))
                pid = con.execute(
                    "INSERT INTO productos (codigo, nombre, categoria_id, unidad, costo, precio, iva, proveedor_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, 19, ?)", (codigo, nombre, cat_id, unidad, costo, precio, proveedores[prov])).lastrowid
                productos.append({"id": pid, "nombre": nombre, "unidad": unidad, "precio": precio, "pop": pop,
                                  "ubicacion": ubicacion, "costo": costo})
        clientes_credito = []
        for doc, nombre, tel, cupo, plazo, paga in CLIENTES:
            cid = con.execute("INSERT INTO clientes (documento, nombre, telefono, cupo_credito, plazo_dias) "
                              "VALUES (?, ?, ?, ?, ?)", (doc, nombre, tel, cupo, plazo)).lastrowid
            clientes_credito.append({"id": cid, "paga": paga, "plazo": plazo})
        clientes_contado = [con.execute("INSERT INTO clientes (documento, nombre, telefono) VALUES (?, ?, ?)",
                                        (d, nm, t)).lastrowid for d, nm, t in CLIENTES_CONTADO]

        # Mínimos y máximos según la demanda esperada de cada sede
        peso_total = sum(p["pop"] for p in productos)
        for nombre_sede, _, _, factor in SEDES:
            ventas_dia = 34 * factor
            for i, p in enumerate(productos):
                cant_media = sum(CANTIDADES.get(p["unidad"], [1.6])) / len(CANTIDADES.get(p["unidad"], [1.6]))
                demanda = ventas_dia * 2.2 * p["pop"] / peso_total * cant_media
                minimo = max(1, math.ceil(demanda * 6))
                maximo = max(minimo + 2, math.ceil(demanda * 16))
                estante = "abcd"[i % 4]
                ubic = p["ubicacion"] if p["ubicacion"] in ("bodega", "vitrina") else f"{p['ubicacion']} estante {estante}"
                con.execute("INSERT INTO existencias (producto_id, sede_id, minimo, maximo, ubicacion) VALUES (?, ?, ?, ?, ?)",
                            (p["id"], sedes[nombre_sede], minimo, maximo, ubic))
                inicial = round(maximo * r.uniform(0.65, 1.0), 1 if p["unidad"] in ("kilos", "metros") else 0)
                mover(con, p["id"], sedes[nombre_sede], "inicial", inicial, nota="Inventario inicial")

    cajeros = {"Centro": usuarios["caja.centro"], "Norte": usuarios["caja.norte"], "Sur": usuarios["caja.sur"]}
    bodegueros = {"Centro": usuarios["bodega.centro"], "Norte": usuarios["bodega.norte"], "Sur": usuarios["dueno"]}
    admin = usuarios["dueno"]
    pesos_productos = [p["pop"] for p in productos]
    pagos_credito: list[tuple] = []  # (fecha de pago, venta_id, sede, cliente)
    recepciones: list[tuple] = []    # (fecha, compra_id, sede)
    recibir_traslados: list[tuple] = []

    dia = inicio
    total_ventas = 0
    while dia.date() <= fin.date():
        es_hoy = dia.date() == fin.date()
        dow = dia.weekday()  # 0 lunes ... 6 domingo
        if dia.day in (1, 15) or es_hoy:
            aviso(f"  simulando {dia:%Y-%m-%d}...")
        for nombre_sede, _, _, factor in SEDES:
            sede = sedes[nombre_sede]
            if dow == 6 and nombre_sede != "Centro":
                continue
            cajero = cajeros[nombre_sede]
            apertura = dia.replace(hour=7, minute=30)
            cierre_hora = 13 if dow == 6 else 18
            if es_hoy and fin < apertura:
                continue
            fijar_reloj(apertura)
            with transaccion(con):
                caja_id = caja.abrir(con, sede, cajero, 200_000)
            volumen = 34 * factor * (0.45 if dow == 6 else 0.85 if dow == 5 else 1.0)
            volumen *= 1.2 if dia.day in (15, 16, 30, 31, 1) else 1.0
            n_ventas = max(1, int(r.gauss(volumen, volumen * 0.18)))
            horas = sorted(apertura + timedelta(minutes=r.uniform(10, (cierre_hora - 7.5) * 60)) for _ in range(n_ventas))

            with transaccion(con):
                for momento in horas:
                    if es_hoy and momento > fin:
                        break
                    fijar_reloj(momento)
                    items = []
                    for p in r.choices(productos, weights=pesos_productos, k=r.choice([1, 1, 1, 2, 2, 3, 4])):
                        if any(i["producto_id"] == p["id"] for i in items):
                            continue
                        q = cantidad_venta(r, p["unidad"], p["precio"])
                        disponible = existencia(con, p["id"], sede)
                        if disponible <= 0:
                            continue
                        items.append({"producto_id": p["id"], "cantidad": min(q, disponible)})
                    if not items:
                        continue
                    medio = r.choices(["efectivo", "tarjeta", "transferencia", "credito"], [55, 20, 19, 6])[0]
                    cliente = None
                    if medio == "credito":
                        cli = r.choice(clientes_credito)
                        cliente = cli["id"]
                    elif r.random() < 0.08:
                        cliente = r.choice(clientes_contado)
                    try:
                        v = ventas.vender(con, sede_id=sede, usuario_id=cajero, items=items, medio_pago=medio,
                                          cliente_id=cliente)
                    except ErrorNegocio:  # sin cupo de crédito: paga en efectivo
                        v = ventas.vender(con, sede_id=sede, usuario_id=cajero, items=items, medio_pago="efectivo")
                        medio = "efectivo"
                    total_ventas += 1
                    if medio == "credito":
                        dias_pago = r.randint(5, cli["plazo"] + 5) if r.random() < cli["paga"] else r.randint(cli["plazo"] + 20, 200)
                        pagos_credito.append((momento + timedelta(days=dias_pago), v["id"], sede))
                    if r.random() < 0.006 and medio != "credito":
                        fijar_reloj(momento + timedelta(minutes=4))
                        ventas.anular(con, v["id"], cajero, sede, r.choice(["Error de digitación", "El cliente desistió"]))
                    elif r.random() < 0.004 and medio == "efectivo":
                        it = uno(con, "SELECT id, cantidad FROM venta_items WHERE venta_id = ?", v["id"])
                        fijar_reloj(momento + timedelta(minutes=30))
                        ventas.devolver(con, v["id"], {it["id"]: min(1, it["cantidad"])}, cajero, sede,
                                        "Producto no le sirvió al cliente")

                # Abonos de cartera que tocan hoy en esta sede
                fijar_reloj(dia.replace(hour=cierre_hora - 1, minute=15))
                for pago in [x for x in pagos_credito if x[2] == sede and x[0].date() <= dia.date()]:
                    if es_hoy and pago[0] > fin:
                        continue
                    pagos_credito.remove(pago)
                    saldo = ventas.saldo_venta(con, pago[1])
                    if saldo > 0:
                        cartera.abonar(con, pago[1], saldo, r.choice(["efectivo", "transferencia"]), cajero, sede)

                if r.random() < 0.25:
                    fijar_reloj(dia.replace(hour=12, minute=r.randint(0, 59)))
                    caja.movimiento(con, caja_id, "egreso", r.choice([8000, 12000, 15000, 25000]),
                                    r.choice(["Almuerzo domiciliario", "Transporte de mercancía", "Aseo", "Papelería"]), cajero)

            if not es_hoy:
                fijar_reloj(dia.replace(hour=cierre_hora, minute=40))
                with transaccion(con):
                    esperado = caja.resumen(con, caja_id)["esperado"]
                    ruido = 0 if r.random() < 0.85 else r.choice([-20000, -10000, -5000, -2000, 1000, 2000, 5000])
                    caja.cerrar(con, caja_id, esperado + ruido, cajero, "" if not ruido else "Diferencia en el arqueo")

            # Lunes: pedidos de lo que está bajo el mínimo; llegan el miércoles
            if dow == 0 and not es_hoy:
                fijar_reloj(dia.replace(hour=9))
                with transaccion(con):
                    for grupo in compras.sugerido_por_proveedor(con, sede):
                        if not grupo["proveedor_id"]:
                            continue
                        items = [{"producto_id": p["id"], "cantidad": p["sugerido"], "costo_unitario": p["costo"] * r.uniform(0.97, 1.05)}
                                 for p in grupo["items"]]
                        cid = compras.crear(con, grupo["proveedor_id"], sede, items, admin)
                        compras.enviar(con, cid, admin)
                        recepciones.append((dia + timedelta(days=2, hours=3), cid, sede, None))

            # Jueves: el Centro abastece a Norte y Sur; llega el viernes
            if dow == 3 and nombre_sede != "Centro" and not es_hoy:
                fijar_reloj(dia.replace(hour=15))
                faltan = [p for p in todos(con, """SELECT e.producto_id, e.maximo - e.cantidad AS falta, c.cantidad AS centro,
                                                     c.minimo AS centro_min FROM existencias e
                                                     JOIN existencias c ON c.producto_id = e.producto_id AND c.sede_id = ?
                                                     WHERE e.sede_id = ? AND e.cantidad <= e.minimo""", sedes["Centro"], sede)
                          if p["centro"] - p["falta"] > p["centro_min"]]
                items = [{"producto_id": p["producto_id"], "cantidad": round(p["falta"])} for p in faltan[:8] if round(p["falta"]) > 0]
                if items:
                    with transaccion(con):
                        tid = traslados.crear(con, sedes["Centro"], sede, items, bodegueros["Centro"], "Reabastecimiento semanal")
                        traslados.enviar(con, tid, bodegueros["Centro"])
                        recibir_traslados.append((dia + timedelta(days=1, hours=-6), tid, bodegueros[nombre_sede]))

            # Sábado: conteo cíclico de 12 productos
            if dow == 5 and not es_hoy:
                fijar_reloj(dia.replace(hour=11))
                with transaccion(con):
                    cid = conteos.crear(con, sede, f"Cíclico semana {dia:%V}", "ciclico",
                                        ",".join(str(p["id"]) for p in conteos.sugeridos_ciclicos(con, sede, 12)),
                                        True, bodegueros[nombre_sede])
                    for p in conteos.alcance(con, uno(con, "SELECT * FROM conteos WHERE id = ?", cid)):
                        real = p["existencia"]
                        azar = r.random()
                        if azar < 0.1:
                            real = max(0, real - r.choice([1, 1, 2, 3]))
                        elif azar < 0.14:
                            real += r.choice([1, 2])
                        conteos.registrar(con, cid, p["id"], real, bodegueros[nombre_sede], r.choice(["escaner", "voz", "manual"]))
                    conteos.aprobar(con, cid, admin)

        # Recepciones y traslados que llegan hoy
        for rec in [x for x in recepciones if x[0].date() <= dia.date() and (not es_hoy or x[0] <= fin)]:
            recepciones.remove(rec)
            fijar_reloj(rec[0])
            with transaccion(con):
                items = todos(con, "SELECT id, cantidad - recibido AS pendiente FROM compra_items WHERE compra_id = ?", rec[1])
                if rec[3] is None and r.random() < 0.1:  # el proveedor entrega incompleto; el resto llega después
                    cantidades = {i["id"]: (max(1, round(i["pendiente"] / 2)) if k == 0 else i["pendiente"]) for k, i in enumerate(items)}
                    resto = {i["id"]: i["pendiente"] - cantidades[i["id"]] for i in items if i["pendiente"] - cantidades[i["id"]] > 0}
                    if resto:
                        recepciones.append((rec[0] + timedelta(days=4), rec[1], rec[2], resto))
                else:
                    cantidades = rec[3] or {i["id"]: i["pendiente"] for i in items}
                compras.recibir(con, rec[1], cantidades, bodegueros[[n for n, s in sedes.items() if s == rec[2]][0]],
                                f"FV-{r.randint(10000, 99999)}")
        for tr in [x for x in recibir_traslados if x[0].date() <= dia.date() and (not es_hoy or x[0] <= fin)]:
            recibir_traslados.remove(tr)
            fijar_reloj(tr[0])
            with transaccion(con):
                traslados.recibir(con, tr[1], {}, tr[2])

        # Pago a proveedores: la mayoría al vencer; algunos se atrasan
        fijar_reloj(dia.replace(hour=16))
        with transaccion(con):
            for c in todos(con, "SELECT id, vence FROM compras WHERE estado IN ('parcial', 'recibida') AND pagada IS NULL AND vence <= ?",
                           (dia + timedelta(days=2)).strftime("%Y-%m-%d")):
                if r.random() < 0.85 and not es_hoy:
                    compras.pagar(con, c["id"], admin)
        dia += timedelta(days=1)

    # Estado "en vivo" para la demostración: un conteo abierto y un traslado en camino
    fijar_reloj(None)
    with transaccion(con):
        cid = conteos.crear(con, sedes["Centro"], "Pasillo 1: tubería PVC", "ubicacion", "pasillo 1", True, usuarios["bodega.centro"])
        for p in conteos.alcance(con, uno(con, "SELECT * FROM conteos WHERE id = ?", cid))[:7]:
            conteos.registrar(con, cid, p["id"], max(0, p["existencia"] - r.choice([0, 0, 0, 1])), usuarios["bodega.centro"], "escaner")
        en_camino = [p for p in todos(con, "SELECT producto_id, cantidad FROM existencias WHERE sede_id = ? AND cantidad > 20 LIMIT 4",
                                      sedes["Centro"])]
        if en_camino:
            tid = traslados.crear(con, sedes["Centro"], sedes["Sur"], [{"producto_id": p["producto_id"], "cantidad": 5} for p in en_camino],
                                  usuarios["bodega.centro"], "Pedido urgente de la sede Sur")
            traslados.enviar(con, tid, usuarios["bodega.centro"])
        # Un borrador de compra para mostrar el flujo de aprobación
        sug = compras.sugerido_por_proveedor(con, sedes["Norte"])
        if sug and sug[0]["proveedor_id"]:
            compras.crear(con, sug[0]["proveedor_id"], sedes["Norte"],
                          [{"producto_id": p["id"], "cantidad": p["sugerido"], "costo_unitario": p["costo"]} for p in sug[0]["items"]],
                          usuarios["admin.norte"], "Borrador sugerido por el sistema")
    aviso(f"Listo: {len(productos)} productos, {total_ventas} ventas en {dias} días, {len(sedes)} sedes.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=90)
    ap.add_argument("--reiniciar", action="store_true", help="borra la base actual")
    ap.add_argument("--db", default=str(RUTA_DB))
    a = ap.parse_args()
    from pathlib import Path
    ruta = Path(a.db)
    if a.reiniciar:
        for sufijo in ("", "-wal", "-shm"):
            Path(str(ruta) + sufijo).unlink(missing_ok=True)
    con = conectar(ruta)
    generar(con, a.dias)


if __name__ == "__main__":
    main()
