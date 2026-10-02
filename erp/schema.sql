-- ERP ferretería: esquema SQLite.
-- Dinero en pesos colombianos enteros; cantidades REAL (kilos, metros).
-- El inventario es un libro de movimientos; existencias guarda el saldo para
-- consultarlo rápido y se actualiza en la misma transacción de cada movimiento.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS config (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sedes (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL UNIQUE,
    direccion TEXT NOT NULL DEFAULT '',
    telefono TEXT NOT NULL DEFAULT '',
    activa INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    usuario TEXT NOT NULL UNIQUE,
    clave_hash TEXT NOT NULL,
    rol TEXT NOT NULL CHECK (rol IN ('admin', 'administrador', 'cajero', 'bodeguero')),
    sede_id INTEGER REFERENCES sedes(id),          -- NULL = todas las sedes
    activo INTEGER NOT NULL DEFAULT 1,
    creado TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS sesiones (
    token TEXT PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    sede_id INTEGER NOT NULL REFERENCES sedes(id),  -- sede en la que está trabajando
    creada TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    expira TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categorias (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS proveedores (
    id INTEGER PRIMARY KEY,
    nit TEXT NOT NULL UNIQUE,
    nombre TEXT NOT NULL,
    contacto TEXT NOT NULL DEFAULT '',
    telefono TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    plazo_dias INTEGER NOT NULL DEFAULT 30,
    activo INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS productos (
    id INTEGER PRIMARY KEY,
    codigo TEXT NOT NULL UNIQUE,                     -- código de barras
    nombre TEXT NOT NULL,
    categoria_id INTEGER REFERENCES categorias(id),
    marca TEXT NOT NULL DEFAULT '',
    unidad TEXT NOT NULL DEFAULT 'unidades',
    costo REAL NOT NULL DEFAULT 0,                   -- costo promedio ponderado
    precio INTEGER NOT NULL DEFAULT 0,               -- precio de venta con IVA
    iva INTEGER NOT NULL DEFAULT 19 CHECK (iva IN (0, 5, 19)),
    proveedor_id INTEGER REFERENCES proveedores(id),
    activo INTEGER NOT NULL DEFAULT 1,
    creado TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    actualizado TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS ix_productos_nombre ON productos(nombre);

CREATE TABLE IF NOT EXISTS existencias (
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    sede_id INTEGER NOT NULL REFERENCES sedes(id),
    cantidad REAL NOT NULL DEFAULT 0,
    minimo REAL NOT NULL DEFAULT 0,
    maximo REAL NOT NULL DEFAULT 0,
    ubicacion TEXT NOT NULL DEFAULT '',
    ultimo_conteo TEXT,
    PRIMARY KEY (producto_id, sede_id)
);

CREATE TABLE IF NOT EXISTS movimientos (
    id INTEGER PRIMARY KEY,
    fecha TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    sede_id INTEGER NOT NULL REFERENCES sedes(id),
    tipo TEXT NOT NULL CHECK (tipo IN (
        'inicial', 'venta', 'anulacion_venta', 'devolucion', 'compra',
        'traslado_salida', 'traslado_entrada', 'ajuste', 'conteo', 'danado'
    )),
    cantidad REAL NOT NULL,                          -- + entra, - sale
    saldo REAL NOT NULL,                             -- existencia después del movimiento
    costo_unitario REAL NOT NULL DEFAULT 0,
    referencia TEXT NOT NULL DEFAULT '',             -- "venta:12", "compra:3"...
    usuario_id INTEGER REFERENCES usuarios(id),
    nota TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_mov_producto ON movimientos(producto_id, sede_id, fecha);
CREATE INDEX IF NOT EXISTS ix_mov_fecha ON movimientos(fecha);

CREATE TABLE IF NOT EXISTS clientes (
    id INTEGER PRIMARY KEY,
    documento TEXT NOT NULL UNIQUE,                  -- NIT o cédula
    nombre TEXT NOT NULL,
    telefono TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    direccion TEXT NOT NULL DEFAULT '',
    cupo_credito INTEGER NOT NULL DEFAULT 0,
    plazo_dias INTEGER NOT NULL DEFAULT 30,
    activo INTEGER NOT NULL DEFAULT 1,
    creado TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS cajas (
    id INTEGER PRIMARY KEY,
    sede_id INTEGER NOT NULL REFERENCES sedes(id),
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    abierta TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    cerrada TEXT,
    base INTEGER NOT NULL DEFAULT 0,
    efectivo_contado INTEGER,
    diferencia INTEGER,
    estado TEXT NOT NULL DEFAULT 'abierta' CHECK (estado IN ('abierta', 'cerrada')),
    nota TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS movimientos_caja (
    id INTEGER PRIMARY KEY,
    caja_id INTEGER NOT NULL REFERENCES cajas(id),
    fecha TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    tipo TEXT NOT NULL CHECK (tipo IN ('ingreso', 'egreso')),
    valor INTEGER NOT NULL CHECK (valor > 0),
    concepto TEXT NOT NULL,
    usuario_id INTEGER REFERENCES usuarios(id)
);

CREATE TABLE IF NOT EXISTS ventas (
    id INTEGER PRIMARY KEY,
    numero TEXT NOT NULL UNIQUE,                     -- consecutivo por sede: "N-000123"
    sede_id INTEGER NOT NULL REFERENCES sedes(id),
    fecha TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    usuario_id INTEGER REFERENCES usuarios(id),
    cliente_id INTEGER REFERENCES clientes(id),
    caja_id INTEGER REFERENCES cajas(id),
    subtotal INTEGER NOT NULL,                       -- base sin IVA
    descuento INTEGER NOT NULL DEFAULT 0,
    iva INTEGER NOT NULL,
    total INTEGER NOT NULL,
    costo_total INTEGER NOT NULL,
    medio_pago TEXT NOT NULL CHECK (medio_pago IN ('efectivo', 'tarjeta', 'transferencia', 'credito')),
    recibido INTEGER NOT NULL DEFAULT 0,             -- efectivo entregado por el cliente
    estado TEXT NOT NULL DEFAULT 'pagada' CHECK (estado IN ('pagada', 'credito', 'anulada')),
    factura_electronica TEXT NOT NULL DEFAULT 'pendiente',
    anulada_motivo TEXT NOT NULL DEFAULT '',
    anulada_por INTEGER REFERENCES usuarios(id),
    vence TEXT                                       -- fecha límite si es a crédito
);
CREATE INDEX IF NOT EXISTS ix_ventas_fecha ON ventas(sede_id, fecha);

CREATE TABLE IF NOT EXISTS venta_items (
    id INTEGER PRIMARY KEY,
    venta_id INTEGER NOT NULL REFERENCES ventas(id),
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    cantidad REAL NOT NULL CHECK (cantidad > 0),
    precio_unitario INTEGER NOT NULL,                -- con IVA, antes de descuento
    descuento INTEGER NOT NULL DEFAULT 0,
    iva_pct INTEGER NOT NULL,
    costo_unitario REAL NOT NULL,
    total INTEGER NOT NULL,
    devuelto REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_venta_items ON venta_items(venta_id);

CREATE TABLE IF NOT EXISTS devoluciones (
    id INTEGER PRIMARY KEY,
    venta_id INTEGER NOT NULL REFERENCES ventas(id),
    fecha TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    usuario_id INTEGER REFERENCES usuarios(id),
    caja_id INTEGER REFERENCES cajas(id),
    total INTEGER NOT NULL,
    motivo TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS abonos (
    id INTEGER PRIMARY KEY,
    venta_id INTEGER NOT NULL REFERENCES ventas(id),
    fecha TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    valor INTEGER NOT NULL CHECK (valor > 0),
    medio TEXT NOT NULL CHECK (medio IN ('efectivo', 'tarjeta', 'transferencia')),
    usuario_id INTEGER REFERENCES usuarios(id),
    caja_id INTEGER REFERENCES cajas(id)
);

CREATE TABLE IF NOT EXISTS compras (
    id INTEGER PRIMARY KEY,
    numero TEXT NOT NULL UNIQUE,
    proveedor_id INTEGER NOT NULL REFERENCES proveedores(id),
    sede_id INTEGER NOT NULL REFERENCES sedes(id),
    creada TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    estado TEXT NOT NULL DEFAULT 'borrador' CHECK (estado IN ('borrador', 'enviada', 'parcial', 'recibida', 'anulada')),
    usuario_id INTEGER REFERENCES usuarios(id),
    factura_proveedor TEXT NOT NULL DEFAULT '',
    recibida TEXT,
    vence TEXT,                                      -- fecha de pago al proveedor
    pagada TEXT,                                     -- fecha en que se pagó
    total INTEGER NOT NULL DEFAULT 0,
    nota TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS compra_items (
    id INTEGER PRIMARY KEY,
    compra_id INTEGER NOT NULL REFERENCES compras(id),
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    cantidad REAL NOT NULL CHECK (cantidad > 0),
    recibido REAL NOT NULL DEFAULT 0,
    costo_unitario REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS traslados (
    id INTEGER PRIMARY KEY,
    numero TEXT NOT NULL UNIQUE,
    origen_id INTEGER NOT NULL REFERENCES sedes(id),
    destino_id INTEGER NOT NULL REFERENCES sedes(id),
    estado TEXT NOT NULL DEFAULT 'borrador' CHECK (estado IN ('borrador', 'en_transito', 'recibido', 'anulado')),
    creado TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    enviado TEXT,
    recibido TEXT,
    usuario_envia INTEGER REFERENCES usuarios(id),
    usuario_recibe INTEGER REFERENCES usuarios(id),
    nota TEXT NOT NULL DEFAULT '',
    CHECK (origen_id <> destino_id)
);

CREATE TABLE IF NOT EXISTS traslado_items (
    id INTEGER PRIMARY KEY,
    traslado_id INTEGER NOT NULL REFERENCES traslados(id),
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    cantidad REAL NOT NULL CHECK (cantidad > 0),
    recibido REAL
);

CREATE TABLE IF NOT EXISTS conteos (
    id INTEGER PRIMARY KEY,
    sede_id INTEGER NOT NULL REFERENCES sedes(id),
    nombre TEXT NOT NULL,
    tipo TEXT NOT NULL CHECK (tipo IN ('general', 'categoria', 'ubicacion', 'ciclico')),
    filtro TEXT NOT NULL DEFAULT '',                 -- categoría o ubicación contada
    ciego INTEGER NOT NULL DEFAULT 1,                -- no mostrar la cantidad del sistema
    estado TEXT NOT NULL DEFAULT 'abierto' CHECK (estado IN ('abierto', 'aprobado', 'anulado')),
    creado TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    usuario_id INTEGER REFERENCES usuarios(id),
    aprobado TEXT,
    aprobado_por INTEGER REFERENCES usuarios(id)
);

CREATE TABLE IF NOT EXISTS conteo_items (
    id INTEGER PRIMARY KEY,
    conteo_id INTEGER NOT NULL REFERENCES conteos(id),
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    sistema REAL NOT NULL,                           -- existencia al momento de contarlo
    contado REAL NOT NULL,
    contado_anterior REAL,                           -- para "deshacer" el último registro
    danados_anterior REAL,
    ultima_accion TEXT NOT NULL DEFAULT 'contado',   -- qué deshace "deshacer": 'contado' o 'danados'
    danados REAL NOT NULL DEFAULT 0,
    metodo TEXT NOT NULL DEFAULT 'manual' CHECK (metodo IN ('manual', 'escaner', 'voz')),
    usuario_id INTEGER REFERENCES usuarios(id),
    fecha TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    nota TEXT NOT NULL DEFAULT '',
    UNIQUE (conteo_id, producto_id)
);

-- Asistente: conversación por usuario, su estado (producto en foco, lo pendiente de
-- confirmar) y la memoria compartida de la ferretería ("el de cemento entrega los martes")
CREATE TABLE IF NOT EXISTS asistente_mensajes (
    id INTEGER PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    fecha TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    rol TEXT NOT NULL CHECK (rol IN ('usuario', 'asistente')),
    texto TEXT NOT NULL,
    datos TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_asistente_mensajes_usuario ON asistente_mensajes(usuario_id, id);
CREATE TABLE IF NOT EXISTS asistente_estado (
    usuario_id INTEGER PRIMARY KEY REFERENCES usuarios(id),
    datos TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS asistente_notas (
    id INTEGER PRIMARY KEY,
    texto TEXT NOT NULL,
    usuario_id INTEGER REFERENCES usuarios(id),
    sede_id INTEGER REFERENCES sedes(id),
    fecha TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS auditoria (
    id INTEGER PRIMARY KEY,
    fecha TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    usuario_id INTEGER REFERENCES usuarios(id),
    sede_id INTEGER REFERENCES sedes(id),
    accion TEXT NOT NULL,
    detalle TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_auditoria_fecha ON auditoria(fecha);
