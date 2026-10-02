# Ferretería IA — ERP multi-sede con inventario por voz (local, sin internet)

ERP para ferretería que corre en el PC de la sede y se usa desde el PC y los
celulares de la misma WiFi. El asistente de voz (Whisper + reglas + LLM pequeño
+ Piper) también corre en el PC: nada sale a internet y no hay costo por uso de IA.

## Probar en la web (versión de prueba, sin IA)

Para mostrarlo a un cliente: el ERP completo con datos de demostración de 3 sedes y 90 días.
La voz y el asistente no van (necesitan el modelo de IA del PC de la tienda).

**Render, plan gratis:**
1. Entrar a render.com con la cuenta de GitHub.
2. *New* → *Blueprint* → elegir este repositorio → *Apply* (usa `render.yaml`).
3. En unos minutos queda publicado en `https://ferreteria-erp.onrender.com` (o el nombre que asigne Render).

Tener en cuenta: el plan gratis se duerme tras 15 minutos sin visitas (la primera visita tarda
cerca de un minuto en despertarlo) y cada arranque vuelve a los datos de demostración, así que lo que
el cliente registre se borra. Usuarios de prueba: ver la tabla de abajo (contraseña `demo2026`).

Con Docker en cualquier otro hosting: `docker build -t ferreteria . && docker run -p 8000:8000 ferreteria`.
Sin Docker: `pip install -r requirements.txt && python servidor_erp.py --nube` (puerto de la variable `PORT`).

## Instalar en el PC de la tienda (con voz y asistente)

    python3.10 -m venv .venv
    .venv/bin/pip install -r requirements-voz.txt     # -r requirements-dev.txt para pruebas

Los modelos de IA no están en el repositorio (~3 GB). Van en estas rutas:

| Archivo | Qué es |
|---|---|
| `models/llm/qwen3-4b-2507-q4.gguf` | Qwen3-4B-Instruct-2507 en GGUF Q4_K_M (Hugging Face) |
| `models/voices/es_MX-claude-high.onnx` y `.onnx.json` | Voz en español de Piper (piper-voices, Hugging Face) |
| `tools/llama-cpu/llama-b11321/llama-server` | llama.cpp, versión b11321 para Linux x64 (CPU) |
| `models/whisper/` | Whisper *small*: se descarga solo la primera vez |

## Arrancar el ERP

    cd ~/Escritorio/ferreteria-ia
    .venv/bin/python servidor_erp.py              # con voz y asistente (~4,5 GB de RAM; listo en ~1 minuto)
    .venv/bin/python servidor_erp.py --sin-voz    # sin voz ni asistente: arranca rápido, conteos con escáner/teclado
    .venv/bin/python servidor_erp.py --puerto 8446 --base copia.db   # probar sobre una copia de la base

- En el PC: `https://localhost:8443` · en el celular: la URL que imprime el servidor.
- Una sola vez, abre el puerto solo para la red local:
  `sudo ufw allow from 192.168.0.0/22 to any port 8443 proto tcp comment 'ferreteria-ia'`
- El certificado es local: Safari/Chrome avisan la primera vez. «Mostrar detalles» → «visitar este sitio web».
- Safari solo permite micrófono y cámara en https; por eso el servidor usa certificado propio.

### Usuarios de demostración (contraseña `demo2026`)

| Usuario | Rol | Entra a |
|---|---|---|
| `dueno` | Dueño: todo, todas las sedes | Panel de monitoreo |
| `admin.norte` | Administradora de la sede Norte | Panel de su sede |
| `caja.centro`, `caja.norte`, `caja.sur` | Cajeros | Punto de venta |
| `bodega.centro`, `bodega.norte` | Bodegueros | Conteos |

Datos de demostración: 3 sedes, 156 productos, 90 días de operación simulada.
Para regenerarlos (borra la base): `.venv/bin/python -m erp.demo --reiniciar`.
**Antes de usarlo en la tienda real** se debe arrancar con una base vacía y crear
productos, usuarios y sedes reales.

## Asistente (menú «Asistente» o el ícono ✦ de la barra superior)

Se le habla o se le escribe como a un empleado: «¿cuánto cemento gris hay?», «¿y en el Sur?»,
«¿qué hay que pedirle a Eléctricos del Valle?», «¿quién nos debe?», «llegaron 20 bultos de cemento»,
«se rompieron dos bombillos de 9 vatios», «registra un producto nuevo: silicona negra, nos cuesta 9 mil»,
«recuerda que el de cemento entrega los martes».

- **Consulta**: existencias por sede, precio y ficha, faltantes y agotados, pedido sugerido por proveedor,
  ventas, lo más vendido, mercancía quieta, cartera, pagos a proveedores, caja y pendientes del día.
- **Cambia** (siempre con tarjeta de confirmación): registrar productos (la tarjeta se puede corregir y
  escanear el código), entradas, ajustes, dañados, precio, mínimo/máximo y ubicación.
- **Recuerda** lo que se le pide («recuerda que…») para todos los usuarios; se ve y se borra en la misma pantalla.
- Sigue la conversación («¿y en el Sur?» después de preguntar por un producto), ofrece opciones cuando
  hay varios productos parecidos («la segunda») y respeta los permisos de cada rol.
- **Manos libres**: corta solo cuando dejas de hablar, responde en voz alta y vuelve a escuchar.

Cómo funciona: Whisper (voz a texto) → reglas fijas para «sí», «no», «la segunda» y el número que faltaba →
el modelo local (Qwen3 4B) elige la acción en un JSON forzado → el sistema la ejecuta con datos reales →
Piper la lee. El modelo nunca da cifras: los números que la persona no dijo se descartan y ningún cambio se
guarda sin confirmación. Cada turno queda en `datos/asistente_registro.jsonl` (texto, sin audio).

Medir el modelo con 55 frases: `.venv/bin/python bench/bench_asistente.py qwen3-4b`
(última medición: 52/55 y las 3 restantes las resuelven las reglas; 2,9 s de promedio en un i5-1135G7).

## Módulos

- **Panel**: ventas de hoy contra la misma hora de la semana pasada, gráfico de 30 días por sede,
  alertas (agotados, bajo mínimo, cartera vencida, pagos a proveedores, descuadres de caja,
  traslados y compras pendientes), cajas abiertas, más vendidos, salud del inventario, actividad.
- **Vender (POS)**: lector USB, cámara del celular o búsqueda; decimales (kilos, metros);
  efectivo con cambio, tarjeta, transferencia y crédito con cupo.
- **Caja**: apertura con base, ingresos/egresos, arqueo y cierre con diferencia; historial de descuadres.
- **Ventas**: historial, anulación y devolución parcial (la mercancía vuelve al inventario).
- **Productos y existencias**: ficha con existencias por sede, mínimos/máximos, ubicación, kárdex,
  ajustes con motivo y dañados.
- **Conteos**: cíclicos sugeridos, por ubicación, categoría o sede completa; conteo ciego;
  pantalla para celular con escáner y voz; aprobación que ajusta solo la diferencia.
- **Traslados**: sugeridos según faltantes, en tránsito, recepción con faltantes.
- **Compras y proveedores**: pedido sugerido, recepción parcial, costo promedio, cuentas por pagar.
- **Clientes y cartera**: cupos, abonos, cartera por edades.
- **Reportes**: ventas, productos, inventario, merma y compras por periodo y sede; CSV para Excel.
- **Configuración**: usuarios y roles, sedes, datos de la empresa, respaldo de la base; auditoría.

Fuera de alcance por ahora: impresión, facturación electrónica DIAN (requiere proveedor
tecnológico autorizado) y sincronización por internet entre sedes.

## Pruebas

    .venv/bin/python -m pytest tests -q                 # reglas de negocio + web completa
    .venv/bin/python tests/capturas.py dueno /panel     # capturas con el servidor de desarrollo
    .venv/bin/uvicorn erp.app:crear_app --factory --reload --port 8444 \
        --ssl-certfile certs/servidor.crt --ssl-keyfile certs/servidor.key   # servidor de desarrollo

Las órdenes de voz quedan en `datos/voz_registro.jsonl` (texto, sin audio) para seguir
mejorando el intérprete con frases reales.

## Asistente de voz: demos y mediciones

    .venv/bin/python servidor_movil.py      # demo de voz sola, celular como micrófono
    .venv/bin/python demo_voz.py            # demo con el micrófono del PC
    .venv/bin/python bench/bench_pipeline.py   # intérprete completo con frases reales
    .venv/bin/python bench/informe.py          # -> bench/resultados/INFORME.md

## Estructura

- `erp/`: el ERP. `schema.sql` (base), módulos de negocio (`inventario`, `ventas`, `caja`,
  `cartera`, `compras`, `traslados`, `conteos`, `reportes`, `auth`), `rutas/` (web),
  `plantillas/` (HTML), `static/` (CSS, JS y librerías locales: htmx, Alpine, lector de códigos).
- `erp/asistente/`: el asistente. `cerebro.py` (prompt y acciones del LLM), `conversacion.py` (memoria,
  confirmaciones, reglas fijas), `herramientas.py` (consultas y cambios), `buscar.py` (encontrar productos
  como los nombra la gente), `hablado.py` (cifras a palabras para la voz).
- `voz/`: intérprete por reglas, LLM local, motor completo (Whisper → reglas → LLM → Piper).
- `datos/`: base SQLite (`ferreteria.db`) — respáldala desde Configuración → Empresa y sistema.
- `tests/`: pruebas de negocio y web, capturas.
- `bench/`: frases de prueba, grabaciones reales y mediciones del asistente de voz.
- `models/`, `tools/`: modelos y binarios de llama.cpp (~8 GB, no versionar).
