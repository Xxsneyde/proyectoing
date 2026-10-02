"""Sesión de conteo de inventario: aplica las órdenes interpretadas a los productos."""

PRODUCTOS = [
    {"codigo": "7702001000123", "nombre": "Tubo PVC presión 1/2 pulgada", "sistema": 30, "unidad": "unidades"},
    {"codigo": "7703002000456", "nombre": "Tornillo drywall 6x1 (caja x100)", "sistema": 45, "unidad": "cajas"},
    {"codigo": "7704003000789", "nombre": "Cable THHN 12 AWG", "sistema": 250, "unidad": "metros"},
    {"codigo": "7705004000111", "nombre": "Puntilla 2 pulgadas con cabeza", "sistema": 18, "unidad": "kilos"},
    {"codigo": "7706005000222", "nombre": "Cemento gris 50 kg", "sistema": 64, "unidad": "bultos"},
]
OTRAS_SEDES = {"norte": 12, "sur": 7, "centro": 21}


def numero_hablado(n) -> str:
    return str(int(n)) if float(n).is_integer() else str(n).replace(".", ",")


class Sesion:
    def __init__(self):
        self.conteo: dict[str, float] = {}
        self.danados: dict[str, float] = {}
        self.ubicacion: dict[str, str] = {}
        self.notas: dict[str, list[str]] = {}
        self.historial: list[tuple] = []

    def aplicar(self, prod: dict, orden: dict) -> str:
        cod, acc, cant = prod["codigo"], orden["accion"], orden.get("cantidad")
        actual = self.conteo.get(cod)
        if acc in ("conteo", "corregir") and cant is not None:
            self.historial.append(("conteo", cod, actual))
            self.conteo[cod] = cant
            dif = cant - prod["sistema"]
            extra = "" if dif == 0 else f". Diferencia de {numero_hablado(dif)} contra el sistema"
            verbo = "Corregido a" if acc == "corregir" else "Registradas"
            return f"{verbo} {numero_hablado(cant)}{extra}."
        if acc in ("sumar", "restar") and cant is not None:
            self.historial.append(("conteo", cod, actual))
            nuevo = (actual or 0) + (cant if acc == "sumar" else -cant)
            self.conteo[cod] = nuevo
            return f"Total {numero_hablado(nuevo)}."
        if acc == "completo":
            self.historial.append(("conteo", cod, actual))
            self.conteo[cod] = prod["sistema"]
            return f"Completo: {numero_hablado(prod['sistema'])}, igual al sistema."
        if acc == "danado":
            self.historial.append(("danado", cod, self.danados.get(cod)))
            self.danados[cod] = self.danados.get(cod, 0) + (cant or 1)
            return f"{numero_hablado(cant or 1)} marcado como dañado."
        if acc == "ubicacion" and orden.get("ubicacion"):
            self.historial.append(("ubicacion", cod, self.ubicacion.get(cod)))
            self.ubicacion[cod] = orden["ubicacion"]
            return f"Ubicación guardada: {orden['ubicacion']}."
        if acc == "nota":
            self.notas.setdefault(cod, []).append(orden.get("texto", ""))
            return "Nota guardada."
        if acc == "deshacer":
            # Solo deshace lo del producto que está en pantalla, nunca uno que no se ve
            propios = [i for i, h in enumerate(self.historial) if h[1] == cod]
            if not propios:
                return "No hay nada para deshacer en este producto."
            campo, c, previo = self.historial.pop(propios[-1])
            destino = {"conteo": self.conteo, "danado": self.danados, "ubicacion": self.ubicacion}[campo]
            if previo is None:
                destino.pop(c, None)
            else:
                destino[c] = previo
            return "Listo, deshice el último registro."
        if acc == "consulta":
            sede = orden.get("sede")
            if sede in OTRAS_SEDES:
                return f"En la sede {sede} hay {OTRAS_SEDES[sede]}."
            return f"El sistema dice {prod['sistema']} y has contado {numero_hablado(actual) if actual is not None else 'nada todavía'}."
        if not orden.get("texto"):
            return "No te escuché, repite por favor."
        return "No entendí, repite por favor."

    def estado(self, prod: dict) -> dict:
        cod = prod["codigo"]
        return {
            **prod,
            "contado": self.conteo.get(cod),
            "danados": self.danados.get(cod, 0),
            "ubicacion": self.ubicacion.get(cod),
            "notas": self.notas.get(cod, []),
        }
