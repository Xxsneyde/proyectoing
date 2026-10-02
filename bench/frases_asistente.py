"""Frases de prueba del asistente conversacional, con la acción que se espera.

En "esperado", los campos de texto se revisan por palabras clave (todas deben
aparecer, sin tildes; "a|b" acepta cualquiera) y los números por igualdad.
"accion" puede ser una tupla cuando hay más de una lectura razonable.
"""

CONTEXTO = """Sedes: centro, norte, sur.
Quien habla: Laura Méndez, Administrador de sede. Trabaja en la sede Norte.
Hoy: jueves 1 de octubre de 2026.
Memoria de la ferretería:
- El proveedor de cemento entrega los martes. (Andrés Muñoz)
- Don Carlos Ruiz paga los viernes. (Laura Méndez)"""

FOCO_CEMENTO = "(Producto del que venían hablando: Cemento gris 50 kg)"

CASOS = [
    # Existencias
    {"texto": "¿Cuánto cemento gris hay?", "esperado": {"accion": "existencia", "producto": "cemento", "sede": None}},
    {"texto": "oiga cuántos bultos de cemento nos quedan", "esperado": {"accion": "existencia", "producto": "cemento"}},
    {"texto": "¿Hay tubo de media pulgada en Norte?", "esperado": {"accion": "existencia", "producto": "tubo media|1/2", "sede": "norte"}},
    {"texto": "¿Dónde está el thinner?", "esperado": {"accion": "existencia", "producto": "thinner"}},
    {"texto": "cuántas brochas de dos pulgadas tenemos en todas las sedes", "esperado": {"accion": "existencia", "producto": "brocha", "sede": "todas"}},
    {"texto": "revisa cuánto cable doce hay en el sur", "esperado": {"accion": "existencia", "producto": "cable", "sede": "sur"}},
    {"texto": "eh este cuánto tubo pvc de tres cuartos queda", "esperado": {"accion": "existencia", "producto": "tubo"}},
    # Ficha del producto
    {"texto": "¿A cómo está el taladro percutor?", "esperado": {"accion": "info_producto", "producto": "taladro"}},
    {"texto": "¿Cuál es el código de barras del candado de 50?", "esperado": {"accion": "info_producto", "producto": "candado"}},
    {"texto": "¿Quién nos vende la pintura tipo uno?", "esperado": {"accion": "info_producto", "producto": "pintura"}},
    # Faltantes y pedidos
    {"texto": "¿Qué hay que pedir?", "esperado": {"accion": ("bajo_minimo", "pedido")}},
    {"texto": "qué productos se están acabando en norte", "esperado": {"accion": "bajo_minimo", "sede": "norte"}},
    {"texto": "ayúdame con el inventario del sur, qué falta allá", "esperado": {"accion": "bajo_minimo", "sede": "sur"}},
    {"texto": "¿Qué está agotado?", "esperado": {"accion": "agotados"}},
    {"texto": "Arma el pedido para Eléctricos del Valle", "esperado": {"accion": "pedido", "proveedor": "electric|valle"}},
    {"texto": "¿Qué le pedimos al proveedor de pinturas?", "esperado": {"accion": "pedido", "proveedor": "pintura"}},
    # Ventas y gestión
    {"texto": "¿Cuánto hemos vendido hoy?", "esperado": {"accion": "ventas", "periodo": "hoy"}},
    {"texto": "cuánto se vendió ayer en el centro", "esperado": {"accion": "ventas", "periodo": "ayer", "sede": "centro"}},
    {"texto": "¿Cómo nos fue este mes en ventas?", "esperado": {"accion": "ventas", "periodo": "mes"}},
    {"texto": "ventas del mes pasado en todas las sedes", "esperado": {"accion": "ventas", "periodo": "mes_pasado", "sede": "todas"}},
    {"texto": "¿Qué es lo que más se vende esta semana?", "esperado": {"accion": "mas_vendidos", "periodo": "semana"}},
    {"texto": "¿Qué mercancía no se está moviendo?", "esperado": {"accion": "quietos"}},
    {"texto": "¿Quién nos debe plata?", "esperado": {"accion": "cartera", "cliente": None}},
    {"texto": "¿Cuánto debe Carlos Ruiz?", "esperado": {"accion": "cartera", "cliente": "carlos|ruiz"}},
    {"texto": "¿Qué facturas de proveedores hay que pagar esta semana?", "esperado": {"accion": "pagos"}},
    {"texto": "¿Qué movimientos ha tenido la pulidora?", "esperado": {"accion": "movimientos", "producto": "pulidora"}},
    {"texto": "¿Cómo vamos hoy?", "esperado": {"accion": ("pendientes", "ventas")}},
    {"texto": "qué tengo pendiente", "esperado": {"accion": "pendientes"}},
    {"texto": "¿Cuánta plata hay en la caja?", "esperado": {"accion": "caja"}},
    # Registrar productos
    {"texto": "Registra un producto nuevo: cemento blanco de 25 kilos, lo vendemos a 38 mil y nos cuesta 30 mil",
     "esperado": {"accion": "crear_producto", "nombre": "cemento blanco", "precio": 38000, "costo": 30000}},
    {"texto": "crea el producto silicona negra 280 mililitros, precio 15500, código 7701234567890",
     "esperado": {"accion": "crear_producto", "nombre": "silicona negra", "precio": 15500, "codigo": "7701234567890"}},
    {"texto": "agrega un producto nuevo que se llama guantes de látex, se vende a 4500 y llegaron 50",
     "esperado": {"accion": "crear_producto", "nombre": "guantes", "precio": 4500, "cantidad": 50}},
    # Cambios de inventario
    {"texto": "Llegaron 20 bultos de cemento gris", "esperado": {"accion": "entrada", "producto": "cemento", "cantidad": 20}},
    {"texto": "súmale cinco al martillo de uña", "esperado": {"accion": "entrada", "producto": "martillo", "cantidad": 5}},
    {"texto": "Ajusta la existencia del yeso a 12, se había contado mal", "esperado": {"accion": "ajustar", "producto": "yeso", "cantidad": 12}},
    {"texto": "deja el thinner en 8 galones", "esperado": {"accion": "ajustar", "producto": "thinner", "cantidad": 8}},
    {"texto": "Se rompieron dos bombillos de 9 vatios", "esperado": {"accion": "danado", "producto": "bombillo", "cantidad": 2}},
    {"texto": "hay tres galones de esmalte blanco vencidos", "esperado": {"accion": "danado", "producto": "esmalte", "cantidad": 3}},
    {"texto": "Sube el precio de la arena de río a 9500", "esperado": {"accion": "precio", "producto": "arena", "precio": 9500}},
    {"texto": "cámbiale el precio al candado de 40 a veinticinco mil", "esperado": {"accion": "precio", "producto": "candado", "precio": 25000}},
    {"texto": "Pon el mínimo de la gravilla en 10 y el máximo en 40", "esperado": {"accion": "minimo", "producto": "gravilla", "minimo": 10, "maximo": 40}},
    {"texto": "el flexómetro de 5 metros ahora va en el pasillo 4 estante c", "esperado": {"accion": "ubicar", "producto": "flexometro", "ubicacion": "pasillo"}},
    # Memoria y conversación
    {"texto": "Recuerda que el proveedor de tornillos viene los lunes", "esperado": {"accion": "recordar", "texto": "tornillo lunes"}},
    {"texto": "anota que la señora Martha viene el viernes por el pedido de tubería", "esperado": {"accion": "recordar", "texto": "martha viernes"}},
    {"texto": "Hola, buenos días", "esperado": {"accion": "responder"}},
    {"texto": "¿Qué puedes hacer?", "esperado": {"accion": "responder"}},
    {"texto": "Muchas gracias", "esperado": {"accion": "responder"}},
    {"texto": "¿Qué diferencia hay entre el tubo de presión y el sanitario?", "esperado": {"accion": "responder"}},
    {"texto": "¿Qué día entrega el de cemento?", "esperado": {"accion": "responder", "texto": "martes"}},
    # Conversación de varios turnos
    {"texto": "¿y en el norte?", "estado": FOCO_CEMENTO,
     "historia": [{"rol": "usuario", "texto": "¿Cuánto cemento gris hay?"},
                  {"rol": "asistente", "texto": "Cemento gris 50 kg: 45 bultos en Centro."}],
     "esperado": {"accion": "existencia", "producto": (None, "cemento"), "sede": "norte"}},
    {"texto": "¿y a cómo lo vendemos?", "estado": FOCO_CEMENTO,
     "historia": [{"rol": "usuario", "texto": "¿Cuánto cemento gris hay?"},
                  {"rol": "asistente", "texto": "Cemento gris 50 kg: 45 bultos en Centro."}],
     "esperado": {"accion": "info_producto", "producto": (None, "cemento")}},
    {"texto": "sí, hágale", "estado": "(Pendiente de confirmar: entrada de 20 bultos de Cemento gris 50 kg)",
     "historia": [{"rol": "usuario", "texto": "Llegaron 20 bultos de cemento gris"},
                  {"rol": "asistente", "texto": "Voy a sumar 20 bultos a Cemento gris 50 kg en Norte: quedan 65. ¿Confirmo?"}],
     "esperado": {"accion": "confirmar"}},
    {"texto": "no, mejor no", "estado": "(Pendiente de confirmar: entrada de 20 bultos de Cemento gris 50 kg)",
     "historia": [{"rol": "usuario", "texto": "Llegaron 20 bultos de cemento gris"},
                  {"rol": "asistente", "texto": "Voy a sumar 20 bultos a Cemento gris 50 kg en Norte: quedan 65. ¿Confirmo?"}],
     "esperado": {"accion": "cancelar"}},
    {"texto": "la segunda", "estado": "(Opciones que le diste: 1) Brocha 1 pulgada, 2) Brocha 2 pulgadas, 3) Brocha 3 pulgadas)",
     "historia": [{"rol": "usuario", "texto": "se dañó una brocha"},
                  {"rol": "asistente", "texto": "¿Cuál brocha? 1) Brocha 1 pulgada, 2) Brocha 2 pulgadas, 3) Brocha 3 pulgadas."}],
     "esperado": {"accion": "elegir", "numero": 2}},
    {"texto": "a treinta y dos mil quinientos", "estado": "(Producto nuevo en borrador: Cemento blanco 25 kg; falta el precio de venta)",
     "historia": [{"rol": "usuario", "texto": "registra el cemento blanco de 25 kilos, cuesta 26 mil"},
                  {"rol": "asistente", "texto": "Listo el borrador de Cemento blanco 25 kg. ¿A qué precio lo vendes?"}],
     "esperado": {"accion": "crear_producto", "precio": 32500}},
]
