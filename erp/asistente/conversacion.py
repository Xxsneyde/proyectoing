"""La conversación: memoria, confirmaciones, opciones y el paso por el cerebro.

Orden de cada turno:
1. Reglas fijas para lo que no puede fallar: «sí»/«no» a una confirmación, «la segunda»,
   el número que se pidió, «¿qué puedes hacer?».
2. Si no aplica, el LLM local decide la acción (con la conversación reciente y la memoria).
3. Se revisa lo que dijo el LLM: números que la persona no dijo se descartan, «súmale» no
   puede ser «déjalo en», el código de barras se toma del texto.
4. La herramienta responde con datos reales. Los cambios quedan como propuesta hasta confirmar.
"""

import json
import re
from datetime import date, datetime, timedelta

import requests

from erp.asistente import herramientas as h
from erp.asistente.buscar import codigo_en, descomponer, numeros_en, producto_en_texto, puntuar, resolver_producto
from erp.asistente.cerebro import CAMPOS
from erp.asistente.hablado import para_voz
from erp.db import ErrorNegocio, ahora, hoy, todos, transaccion, uno, valor
from voz.comandos import normalizar

DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
VIGENCIA = timedelta(minutes=15)
HISTORIA_MAX = 10  # al pasar de aquí se recorta a los últimos 4 (no en cada turno: así el modelo reusa su caché)
NOTAS_MAX = 25

SI = re.compile(r"^(?:si+|sip|claro|dale|hagale|hagalo|hazlo|confirm\w*|listo|correcto|ok|okay|de una|perfecto|exacto|"
                r"asi es|eso es|eso|va|vale|adelante|guardal\w|guarda\w*|bueno|por supuesto|obvio|afirmativo|registral\w)\b")
NO = re.compile(r"^(?:no|nada|negativo|cancel\w*|olvid\w*|mejor no|dejalo|deja asi|para|espera)\b")
AYUDA = re.compile(r"\b(?:que (?:mas )?(?:puedes|sabes) hacer|en que me (?:puedes )?ayud\w*|para que sirves|como te uso|"
                   r"que te puedo (?:preguntar|pedir)|^ayuda$)")
ORDINALES = {"primero": 1, "primera": 1, "primer": 1, "segundo": 2, "segunda": 2, "tercero": 3, "tercera": 3, "tercer": 3,
             "cuarto": 4, "cuarta": 4, "quinto": 5, "quinta": 5, "ultimo": -1, "ultima": -1}
PRONOMBRES = {"ese", "esa", "eso", "este", "esta", "esto", "lo", "la", "el mismo", "la misma", "el", "mismo", "ese producto",
              "el producto", "el anterior"}
SUMAR = re.compile(r"\b(?:sum\w*|agreg\w*|anad\w*|adicion\w*|llegaron|llego|entraron|entro|ingres\w*|metele|mas)\b")
DEJAR = re.compile(r"\b(?:deja\w*|ajust\w*|quedan? en|quede en|ponlo en|ponla en|corrig\w*|en total)\b")
CAMBIOS = {"crear_producto", "entrada", "ajustar", "danado", "precio", "minimo", "ubicar"}
NECESITA_PRODUCTO = {"existencia", "info_producto", "movimientos", "entrada", "ajustar", "danado", "precio", "minimo", "ubicar"}
VARIOS_OK = {"existencia", "info_producto"}
CAMPOS_NUMERO = ("cantidad", "precio", "costo", "minimo", "maximo")
CONSULTAS = {
    "bajo_minimo": lambda s, i: h.bajo_minimo(s, i), "agotados": lambda s, i: h.bajo_minimo(s, i, solo_agotados=True),
    "pedido": h.pedido, "ventas": h.ventas, "mas_vendidos": h.mas_vendidos, "quietos": h.quietos, "cartera": h.cartera_,
    "pagos": h.pagos, "pendientes": h.pendientes, "caja": h.caja,
}
NO_ENTENDI = "No te entendí bien. ¿Me lo repites con otras palabras?"


class Asistente:
    def __init__(self, cerebro):
        self.cerebro = cerebro

    # --- Memoria -----------------------------------------------------------------------

    def _estado(self, s: h.Sesion) -> dict:
        crudo = valor(s.con, "SELECT datos FROM asistente_estado WHERE usuario_id = ?", s.usuario["id"])
        return json.loads(crudo) if crudo else {}

    def _guardar_estado(self, s: h.Sesion, e: dict):
        s.con.execute("INSERT INTO asistente_estado (usuario_id, datos) VALUES (?, ?) "
                      "ON CONFLICT(usuario_id) DO UPDATE SET datos = excluded.datos", (s.usuario["id"], json.dumps(e)))

    def _mensaje(self, s: h.Sesion, rol: str, texto: str, datos: dict | None = None) -> int:
        return s.con.execute("INSERT INTO asistente_mensajes (usuario_id, fecha, rol, texto, datos) VALUES (?, ?, ?, ?, ?)",
                             (s.usuario["id"], ahora(), rol, texto, json.dumps(datos or {}, ensure_ascii=False))).lastrowid

    def _actualizar_mensaje(self, s: h.Sesion, mensaje_id: int, cambio):
        crudo = valor(s.con, "SELECT datos FROM asistente_mensajes WHERE id = ? AND usuario_id = ?", mensaje_id, s.usuario["id"])
        if crudo:
            datos = json.loads(crudo)
            cambio(datos)
            s.con.execute("UPDATE asistente_mensajes SET datos = ? WHERE id = ?", (json.dumps(datos, ensure_ascii=False), mensaje_id))

    def mensajes(self, s: h.Sesion, n: int = 40) -> list[dict]:
        inicio = self._estado(s).get("inicio", 0)
        filas = todos(s.con, "SELECT id, fecha, rol, texto, datos FROM asistente_mensajes WHERE usuario_id = ? AND id > ? "
                             "ORDER BY id DESC LIMIT ?", s.usuario["id"], inicio, n)
        return [_publico(f) for f in reversed(filas)]

    def notas(self, con) -> list[dict]:
        return todos(con, """SELECT n.id, n.texto, n.fecha, n.usuario_id, u.nombre AS autor FROM asistente_notas n
                             LEFT JOIN usuarios u ON u.id = n.usuario_id ORDER BY n.id DESC LIMIT ?""", NOTAS_MAX)

    def borrar_nota(self, s: h.Sesion, nota_id: int):
        n = uno(s.con, "SELECT usuario_id FROM asistente_notas WHERE id = ?", nota_id)
        if not n:
            raise ErrorNegocio("Esa nota ya no existe.")
        if n["usuario_id"] != s.usuario["id"] and not s.gestion:
            raise ErrorNegocio("Solo quien escribió la nota o la administración pueden borrarla.")
        s.con.execute("DELETE FROM asistente_notas WHERE id = ?", (nota_id,))

    def reiniciar(self, s: h.Sesion):
        ultimo = valor(s.con, "SELECT MAX(id) FROM asistente_mensajes WHERE usuario_id = ?", s.usuario["id"], defecto=0)
        self._guardar_estado(s, {"inicio": ultimo, "corte": ultimo})

    def contexto(self, s: h.Sesion) -> str:
        d = date.fromisoformat(hoy())
        sedes = ", ".join(x["nombre"].lower() for x in h.sedes_activas(s.con))
        donde = (f"Puede ver todas las sedes; ahora está en la sede {s.sede_nombre}." if s.usuario["todas_las_sedes"]
                 else f"Trabaja en la sede {s.sede_nombre}.")
        texto = (f"Sedes: {sedes}.\nQuien habla: {s.usuario['nombre']}, {s.usuario['rol_nombre']}. {donde}\n"
                 f"Hoy: {DIAS[d.weekday()]} {d.day} de {h.MESES_LARGOS[d.month - 1]} de {d.year}.")
        notas = self.notas(s.con)
        if notas:
            texto += "\nMemoria de la ferretería:\n" + "\n".join(f"- {n['texto']} ({n['autor'] or 'alguien'})" for n in reversed(notas))
        return texto

    def _historia(self, s: h.Sesion, e: dict, antes_de: int) -> list[dict]:
        filas = todos(s.con, "SELECT rol, texto FROM asistente_mensajes WHERE usuario_id = ? AND id > ? AND id < ? "
                             "ORDER BY id DESC LIMIT ?", s.usuario["id"], e.get("corte", 0), antes_de, HISTORIA_MAX + 2)
        return [{"rol": f["rol"], "texto": f["texto"][:220]} for f in reversed(filas)]

    def _prefijo(self, s: h.Sesion, e: dict) -> str:
        partes = []
        if e.get("foco"):
            nombre = valor(s.con, "SELECT nombre FROM productos WHERE id = ?", e["foco"])
            if nombre:
                partes.append(f"(Producto del que venían hablando: {nombre})")
        p = e.get("pendiente")
        if p and p["tipo"] == "propuesta":
            partes.append(f"(Pendiente de confirmar: {p['propuesta']['titulo']}"
                          + (f"; falta {h.ETIQUETA_FALTA.get(p['propuesta']['falta'], p['propuesta']['falta'])})"
                             if p["propuesta"].get("falta") else ")"))
        elif p and p["tipo"] == "falta":
            partes.append(f"(Le preguntaste: {p['pregunta']})")
        elif p and p["tipo"] == "elegir":
            partes.append("(Opciones que le diste: " + ", ".join(f"{k + 1}) {o['nombre']}" for k, o in enumerate(p["opciones"])) + ")")
        return "\n".join(partes) + ("\n" if partes else "")

    # --- Un turno -------------------------------------------------------------------------

    def procesar(self, s: h.Sesion, texto: str) -> dict:
        """Devuelve {'usuario': mensaje, 'asistente': mensaje, 'metricas': {...}}."""
        texto = (texto or "").strip()
        if not texto:
            r = h.respuesta("No te escuché bien. Toca el micrófono y habla cerca del teléfono.", tipo="aviso")
            return {"usuario": None, "asistente": {"id": None, "rol": "asistente", "fecha": ahora(), **_vista(r)}, "metricas": {}}
        with transaccion(s.con):
            uid = self._mensaje(s, "usuario", texto)
        e = self._estado(s)
        _vencer(e)
        metricas: dict = {}
        try:
            r = self._reglas(s, e, texto)
            if r is None:
                if not self.cerebro:
                    raise ErrorNegocio("El asistente no está activo en este servidor (se inició sin voz).")
                intencion, metricas = self.cerebro.decidir(self._prefijo(s, e) + texto, self.contexto(s),
                                                           self._historia(s, e, uid), [x["nombre"].lower() for x in h.sedes_activas(s.con)])
                metricas["intencion"] = dict(intencion)
                r = self._ejecutar(s, e, self._revisar(intencion, texto, e), texto)
        except ErrorNegocio as exc:
            r = h.respuesta(str(exc), tipo="error")
        except requests.RequestException:
            r = h.respuesta("No pude pensar la respuesta: el modelo local no responde. Intenta otra vez en un momento.", tipo="error")
        with transaccion(s.con):
            mensaje = self._cerrar_turno(s, e, r)
        return {"usuario": {"id": uid, "rol": "usuario", "texto": texto, "fecha": ahora()}, "asistente": mensaje, "metricas": metricas}

    def boton(self, s: h.Sesion, tipo: str, mensaje_id: int, opcion: int | None = None, campos: dict | None = None) -> dict:
        """Confirmar, cancelar o elegir desde la pantalla."""
        e = self._estado(s)
        _vencer(e)
        p = e.get("pendiente")
        if not p or p.get("mensaje_id") != mensaje_id:
            raise ErrorNegocio("Eso ya no está pendiente. Vuelve a pedirlo si hace falta.")
        try:
            if tipo == "confirmar":
                r = self._confirmar(s, e, campos)
            elif tipo == "cancelar":
                r = self._cancelar(s, e)
            elif tipo == "elegir" and opcion is not None:
                r = self._elegir(s, e, opcion)
            else:
                raise ErrorNegocio("Acción desconocida.")
        except ErrorNegocio as exc:
            r = h.respuesta(str(exc), tipo="error")
        with transaccion(s.con):
            mensaje = self._cerrar_turno(s, e, r)
        return {"usuario": None, "asistente": mensaje, "metricas": {}}

    def _cerrar_turno(self, s: h.Sesion, e: dict, r: dict) -> dict:
        if r.get("foco"):
            e["foco"] = r["foco"]
        nuevo_pendiente = r.pop("_pendiente", None)
        anterior = e.get("pendiente")
        if nuevo_pendiente and anterior and anterior["tipo"] == "propuesta" and anterior.get("mensaje_id"):
            self._marcar(s, anterior["mensaje_id"], "reemplazada")
        mid = self._mensaje(s, "asistente", r["texto"], _vista(r))
        if nuevo_pendiente:
            nuevo_pendiente.update(mensaje_id=mid, hora=ahora())
            e["pendiente"] = nuevo_pendiente
        elif r.get("_limpiar") or (anterior and anterior["tipo"] != "propuesta"):
            e.pop("pendiente", None)
        e["ultimo"] = mid
        total = valor(s.con, "SELECT COUNT(*) FROM asistente_mensajes WHERE usuario_id = ? AND id > ?", s.usuario["id"], e.get("corte", 0))
        if total > HISTORIA_MAX:
            e["corte"] = valor(s.con, "SELECT id FROM asistente_mensajes WHERE usuario_id = ? ORDER BY id DESC LIMIT 1 OFFSET 4",
                               s.usuario["id"])
        self._guardar_estado(s, e)
        return {"id": mid, "rol": "asistente", "fecha": ahora(), **_vista(r)}

    def _marcar(self, s: h.Sesion, mensaje_id: int, estado: str):
        def cambio(datos):
            if datos.get("propuesta"):
                datos["propuesta"]["estado"] = estado
            if datos.get("opciones"):
                datos["opciones_cerradas"] = True
        self._actualizar_mensaje(s, mensaje_id, cambio)

    # --- Reglas fijas ------------------------------------------------------------------------

    def _reglas(self, s: h.Sesion, e: dict, texto: str) -> dict | None:
        t = normalizar(texto)
        palabras = t.split()
        if AYUDA.search(t) and len(palabras) <= 8:
            return h.ayuda(s)
        p = e.get("pendiente")
        if not p:
            return None
        inmediato = p.get("mensaje_id") == e.get("ultimo")
        es_no = bool(NO.match(t)) and len(palabras) <= 5
        es_si = bool(SI.match(t)) and not es_no and " no " not in f" {t} " and len(palabras) <= 5
        if p["tipo"] == "propuesta" and inmediato:
            if es_no:
                return self._cancelar(s, e)
            if es_si:
                return self._confirmar(s, e)
            falta = p["propuesta"].get("falta")
            if falta in CAMPOS_NUMERO and not re.search(r"\b(?:costo|cuesta|minimo|maximo|codigo|precio de compra)\b", t):
                nums = numeros_en(texto)
                if nums:
                    return self._completar(s, e, {falta: max(nums)}, texto)
            return None
        if p["tipo"] == "falta" and inmediato:
            if es_no:
                return {**h.respuesta("Listo, no hago nada."), "_limpiar": True}
            campo = p["falta"]
            if campo in CAMPOS_NUMERO or campo == "minimo":
                nums = numeros_en(texto)
                if not nums:
                    return None
                datos = {campo: max(nums)} if campo != "minimo" else _min_max(texto, nums)
            elif campo == "ubicacion":
                datos = {"ubicacion": texto}
            elif campo == "nombre":
                datos = {"nombre": texto}
            else:
                return None
            return self._ejecutar(s, e, {**p["intencion"], **datos}, texto, producto_id=p.get("producto_id"))
        if p["tipo"] == "elegir" and inmediato:
            if es_no:
                return {**h.respuesta("Listo, lo dejo así."), "_limpiar": True}
            if es_si and len(p["opciones"]) == 1:
                return self._elegir(s, e, 1)
            n = _ordinal(t, len(p["opciones"]))
            if n is None:
                consulta = descomponer(texto, hablado=True)
                puntos = [puntuar(consulta, descomponer(o["nombre"]))[0] for o in p["opciones"]]
                mejor = max(puntos) if puntos else 0
                if mejor >= 0.75 and puntos.count(mejor) == 1:
                    n = puntos.index(mejor) + 1
            return self._elegir(s, e, n) if n else None
        if p["tipo"] == "producto" and inmediato:
            r = resolver_producto(s.con, texto)
            if r["unico"]:
                return self._ejecutar(s, e, p["intencion"], texto, producto_id=r["unico"]["id"])
            if r["candidatos"]:
                return self._opciones(p["intencion"], r["candidatos"])
        return None

    def _confirmar(self, s: h.Sesion, e: dict, campos: dict | None = None) -> dict:
        p = e.get("pendiente")
        if not p or p["tipo"] != "propuesta":
            return h.respuesta("No tengo nada pendiente por confirmar.")
        prop = p["propuesta"]
        if prop.get("falta") and not (campos and campos.get(prop["falta"]) not in (None, "")):
            return self._propuesta(prop, {"accion": prop["accion"]})
        with transaccion(s.con):
            r = h.aplicar(s, prop, campos)
            self._marcar(s, p["mensaje_id"], "confirmada")
        e.pop("pendiente", None)
        return r

    def _cancelar(self, s: h.Sesion, e: dict) -> dict:
        p = e.pop("pendiente", None)
        if p and p.get("mensaje_id"):
            with transaccion(s.con):
                self._marcar(s, p["mensaje_id"], "cancelada")
        return {**h.respuesta("Listo, no cambié nada."), "_limpiar": True}

    def _elegir(self, s: h.Sesion, e: dict, n: int) -> dict:
        p = e.get("pendiente")
        if not p or p["tipo"] != "elegir":
            return h.respuesta("No te había dado opciones. ¿Qué necesitas?")
        if n == -1:
            n = len(p["opciones"])
        if not 1 <= n <= len(p["opciones"]):
            return {**h.respuesta(f"Elige un número del 1 al {len(p['opciones'])}."),
                    "_pendiente": {k: v for k, v in p.items() if k not in ("mensaje_id", "hora")}}
        with transaccion(s.con):
            self._marcar(s, p["mensaje_id"], "elegida")
        e.pop("pendiente", None)
        return self._ejecutar(s, e, p["intencion"], "", producto_id=p["opciones"][n - 1]["id"])

    def _completar(self, s: h.Sesion, e: dict, datos: dict, texto: str) -> dict:
        """Llena lo que faltaba en la propuesta pendiente (el precio del producto nuevo, por ejemplo)."""
        p = e["pendiente"]
        prop = p["propuesta"]
        if prop["accion"] == "crear_producto":
            return self._propuesta(h.proponer_producto(s, datos, texto, prop["borrador"]), {"accion": "crear_producto"})
        intencion = {"accion": prop["accion"], "cantidad": prop.get("cantidad"), "precio": prop.get("precio"),
                     "motivo": prop.get("motivo"), **datos}
        return self._ejecutar(s, e, intencion, texto, producto_id=prop["producto_id"])

    # --- Revisión de lo que dijo el modelo ------------------------------------------------------

    def _revisar(self, i: dict, texto: str, e: dict) -> dict:
        i = dict(i)
        if i.get("accion") not in CAMPOS:
            return {"accion": "responder", "texto": NO_ENTENDI}
        t = normalizar(texto)
        nums = numeros_en(texto)
        for campo in CAMPOS_NUMERO:
            v = i.get(campo)
            if v is not None and not any(abs(float(v) - n) < 1e-6 for n in nums):
                i[campo] = None  # el modelo no puede poner números que la persona no dijo
        if i["accion"] == "crear_producto":
            if i.get("precio") and i.get("costo") and abs(i["precio"] - i["costo"]) < 1e-6 and \
                    sum(1 for n in nums if abs(n - i["precio"]) < 1e-6) < 2:
                if re.search(r"\b(?:cuest\w*|costo|compr\w*|nos sale|pagamos|nos vale)\b", t):
                    i["precio"] = None
                elif re.search(r"\b(?:vend\w*|precio|venta|vale|cobr\w*)\b", t):
                    i["costo"] = None
                else:
                    i["precio"] = i["costo"] = None
            if i.get("iva") is not None and not re.search(r"\b(?:iva|exent\w*|exclui\w*|impuesto)\b", t):
                i["iva"] = None
            if i.get("categoria") and not all(w in t for w in normalizar(i["categoria"]).split() if len(w) > 3):
                i["categoria"] = None  # la categoría se toma del producto parecido si no la dijo
            if i.get("unidad") and not re.search(r"\b(?:por|en|de a|se vende(?:n)? (?:por|en|de a)) (?:unidad|metro|kilo|bulto|galon|caja|paquete|rollo)", t):
                i["unidad"] = None  # "cemento de 25 kilos" describe el producto; se vende por bultos
            codigo = codigo_en(texto)
            if codigo:
                i["codigo"] = codigo
            elif i.get("codigo") and re.sub(r"\D", "", str(i["codigo"])) not in re.sub(r"\D", "", texto):
                i["codigo"] = None
        if i["accion"] == "ajustar" and SUMAR.search(t) and not DEJAR.search(t):
            i["accion"] = "entrada"
        elif i["accion"] == "entrada" and DEJAR.search(t) and not SUMAR.search(t):
            i["accion"] = "ajustar"
        for campo in ("producto", "nombre", "cliente", "proveedor", "motivo", "ubicacion", "categoria"):
            if isinstance(i.get(campo), str) and _roto(i[campo], 70):
                i[campo] = None  # a veces el modelo se descarrila dentro de un texto
        if i["accion"] in ("responder", "recordar") and (not i.get("texto") or _roto(i["texto"], 400)):
            i = {"accion": "responder", "texto": NO_ENTENDI}
        if isinstance(i.get("producto"), str) and normalizar(i["producto"]) in PRONOMBRES:
            i["producto"] = None
        if i["accion"] == "confirmar" and not SI.search(t):
            i = {"accion": "responder", "texto": "¿Lo guardo? Dime «sí» o «no», o usa los botones."}
        return i

    # --- Ejecutar la acción -----------------------------------------------------------------

    def _ejecutar(self, s: h.Sesion, e: dict, i: dict, texto: str, producto_id: int | None = None) -> dict:
        a = i.get("accion")
        if a == "responder":
            return h.respuesta((i.get("texto") or NO_ENTENDI).strip())
        if a == "recordar":
            # Se guarda lo que dijo la persona, no la versión del modelo (que a veces le mete el producto en foco)
            propio = re.sub(r"^\W*(?:oye|oiga|por favor|porfa)?\W*(?:recuerda(?:me|lo)?|anota(?:me)?|apunta|no (?:te )?olvides(?: de)?|toma nota(?: de)?)"
                            r"(?: que| esto| lo siguiente)?\W+", "", texto, flags=re.I).strip()
            nota = (propio if propio and propio != texto.strip() else i.get("texto") or texto).strip().rstrip(".") + "."
            if len(nota) < 4:
                return h.respuesta("¿Qué quieres que recuerde?")
            nota = nota[:1].upper() + nota[1:]
            with transaccion(s.con):
                s.con.execute("INSERT INTO asistente_notas (texto, usuario_id, sede_id, fecha) VALUES (?, ?, ?, ?)",
                              (nota[:300], s.usuario["id"], s.sede_id, ahora()))
            return h.respuesta(f"Anotado: «{nota}». Lo tendré en cuenta para todos.", "Anotado.", tipo="ok", nota_nueva=True)
        if a == "confirmar":
            return self._confirmar(s, e)
        if a == "cancelar":
            return self._cancelar(s, e) if e.get("pendiente") else h.respuesta("Listo.")
        if a == "elegir":
            return self._elegir(s, e, int(i.get("numero") or 0))
        if a == "ayuda":
            return h.ayuda(s)
        if not h.permitido(s, a):
            return h.respuesta(h.mensaje_sin_permiso(a), tipo="aviso")

        # Algo que completa el producto nuevo en borrador: "ponle 40 mil", "el mínimo es 5"
        p = e.get("pendiente")
        borrador = p["propuesta"] if p and p["tipo"] == "propuesta" and p["propuesta"]["accion"] == "crear_producto" else None
        if borrador and producto_id is None:
            extra = {}
            otro_nombre = (a == "crear_producto" and i.get("nombre") and
                           puntuar(descomponer(i["nombre"]), descomponer(borrador["borrador"].get("nombre") or ""))[0] < 0.8)
            if a == "crear_producto" and not otro_nombre:
                extra = {k: v for k, v in i.items() if k != "accion" and v not in (None, "")}
            elif a in ("precio", "minimo", "entrada", "ajustar") and (
                    not i.get("producto")
                    or puntuar(descomponer(i["producto"], hablado=True), descomponer(borrador["borrador"].get("nombre") or ""))[0] >= 0.8):
                extra = {k: i.get(k) for k in ("precio", "minimo", "maximo", "cantidad") if i.get(k) is not None}
            if extra:
                return self._propuesta(h.proponer_producto(s, extra, texto, borrador["borrador"]), i)

        if a in CONSULTAS:
            return CONSULTAS[a](s, i)
        if a == "crear_producto":
            return self._propuesta(h.proponer_producto(s, i, texto), i)
        if a not in NECESITA_PRODUCTO:
            return h.respuesta(NO_ENTENDI)

        seguro = True
        if producto_id:
            prods = [uno(s.con, "SELECT id, codigo, nombre, unidad FROM productos WHERE id = ?", producto_id)]
        else:
            prods, seguro = self._productos(s, e, i, texto)
            if prods is None:
                return {**h.respuesta("¿De qué producto?"), "_pendiente": {"tipo": "producto", "intencion": i}}
            if not prods:
                return h.no_encontrado(s, i.get("producto") or texto)
        if a not in VARIOS_OK and (len(prods) > 1 or not seguro):
            return self._opciones(i, prods)  # para cambiar datos no se adivina: se pregunta
        if a == "existencia":
            return h.existencia(s, i, prods)
        if a == "info_producto":
            return h.info_producto(s, i, prods)
        if a == "movimientos":
            return h.movimientos(s, i, prods[0])
        return self._propuesta(h.proponer(s, a, i, prods[0], texto), i, prods[0])

    def _productos(self, s: h.Sesion, e: dict, i: dict, texto: str) -> tuple[list[dict] | None, bool]:
        """(productos a los que se refiere, si la coincidencia es segura). None si no hay forma de saberlo."""
        nombre = i.get("producto")
        foco = uno(s.con, "SELECT id, codigo, nombre, unidad FROM productos WHERE id = ?", e["foco"]) if e.get("foco") else None
        if nombre:
            r = resolver_producto(s.con, nombre)
            if r["unico"]:
                return [r["unico"]], True
            if r["candidatos"]:
                # Si el producto en foco está entre los candidatos, es ese ("el cemento" después de hablar del gris)
                if foco and any(c["id"] == foco["id"] for c in r["candidatos"]):
                    return [foco], True
                return r["candidatos"][:6], False
            if foco and len(normalizar(nombre).split()) <= 2 and not descomponer(nombre)[0]:
                return [foco], True
            return [], False
        if foco:
            return [foco], True
        r = producto_en_texto(s.con, texto)
        if r["unico"]:
            return [r["unico"]], r["unico"]["puntaje"] >= 0.75
        return (r["candidatos"][:6] or None), False

    def _opciones(self, i: dict, prods: list[dict]) -> dict:
        prods = prods[:5]
        if len(prods) == 1:
            texto = voz = f"¿Te refieres a {prods[0]['nombre']}?"
        else:
            texto = "¿Cuál? " + ", ".join(f"{k + 1}) {p['nombre']}" for k, p in enumerate(prods)) + "."
            voz = "Encontré varios. " + ". ".join(f"{k + 1}: {p['nombre']}" for k, p in enumerate(prods)) + ". ¿Cuál?"
        return {**h.respuesta(texto, voz, opciones=[{"n": k + 1, "texto": p["nombre"]} for k, p in enumerate(prods)]),
                "_pendiente": {"tipo": "elegir", "intencion": i, "opciones": [{"id": p["id"], "nombre": p["nombre"]} for p in prods]}}

    def _propuesta(self, prop: dict, i: dict, producto: dict | None = None) -> dict:
        if prop.get("falta") and "campos" not in prop:
            pend = {"tipo": "falta", "falta": prop["falta"], "pregunta": prop["pregunta"], "intencion": i,
                    "producto_id": producto["id"] if producto else None}
            return {**h.respuesta(prop["pregunta"]), "_pendiente": pend, **({"foco": producto["id"]} if producto else {})}
        prop["estado"] = "pendiente"
        if prop.get("falta"):
            texto = f"{prop['titulo']}. {prop['pregunta']}"
            voz = f"Listo el borrador de {prop['borrador']['nombre']}. {prop['pregunta']}"
        else:
            texto = f"{prop['titulo']}. ¿Confirmo?"
            voz = f"{prop['titulo']}. " + " ".join(prop.get("avisos", [])[:1]) + " ¿Lo confirmo?"
        return {**h.respuesta(texto, voz, propuesta=prop), "_pendiente": {"tipo": "propuesta", "propuesta": prop},
                **({"foco": producto["id"]} if producto else {})}


# --- Utilidades ------------------------------------------------------------------------------

def _vista(r: dict) -> dict:
    """Lo que se guarda y se manda a la pantalla (sin las claves internas)."""
    salida = {k: v for k, v in r.items() if not k.startswith("_") and k not in ("foco",)}
    salida["voz"] = para_voz(r.get("voz") or r.get("texto") or "")
    return salida


def _publico(fila: dict) -> dict:
    datos = json.loads(fila["datos"] or "{}")
    return {"id": fila["id"], "rol": fila["rol"], "fecha": fila["fecha"], "texto": fila["texto"], **datos}


def _vencer(e: dict):
    p = e.get("pendiente")
    if p and p.get("hora") and datetime.strptime(p["hora"], "%Y-%m-%d %H:%M:%S") < datetime.strptime(ahora(), "%Y-%m-%d %H:%M:%S") - VIGENCIA:
        e.pop("pendiente", None)


def _roto(texto: str, largo: int) -> bool:
    return len(texto) > largo or bool(re.search(r"[{}\[\]<>]|':|\":|\bnull\b|\baccion\b", texto))


def _ordinal(t: str, total: int) -> int | None:
    for palabra, n in ORDINALES.items():
        if re.search(rf"\b{palabra}\b", t):
            return n
    m = re.search(r"\b(?:opcion|numero|la|el)\s+(\d+|uno|una|dos|tres|cuatro|cinco)\b", t) or re.fullmatch(r"(\d+|uno|dos|tres|cuatro|cinco)", t)
    if m:
        palabra = m.group(1)
        n = int(palabra) if palabra.isdigit() else {"uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5}[palabra]
        return n if 1 <= n <= total else None
    return None


def _min_max(texto: str, nums: list[float]) -> dict:
    t = normalizar(texto)
    m_min = re.search(r"minimo\D*?(\d+(?:[.,]\d+)?)", t)
    m_max = re.search(r"maximo\D*?(\d+(?:[.,]\d+)?)", t)
    if m_min or m_max:
        return {"minimo": float(m_min.group(1).replace(",", ".")) if m_min else None,
                "maximo": float(m_max.group(1).replace(",", ".")) if m_max else None}
    distintos = sorted(set(nums))
    return {"minimo": distintos[0], "maximo": distintos[-1] if len(distintos) > 1 else None}
