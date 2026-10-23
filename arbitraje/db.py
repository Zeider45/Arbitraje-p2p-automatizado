"""Base de datos SQLite: un solo archivo, sin servidor."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .utilidades import ahora_ms

ESQUEMA = """
CREATE TABLE IF NOT EXISTS ordenes (
    numero          TEXT PRIMARY KEY,
    origen          TEXT NOT NULL DEFAULT 'binance',   -- binance | manual | demo
    tipo            TEXT NOT NULL CHECK (tipo IN ('COMPRA', 'VENTA')),
    activo          TEXT NOT NULL DEFAULT 'USDT',
    fiat            TEXT NOT NULL DEFAULT 'VES',
    cantidad        REAL NOT NULL,                     -- USDT de la orden
    precio          REAL NOT NULL,                     -- Bs por USDT
    total_bs        REAL NOT NULL,
    comision        REAL NOT NULL DEFAULT 0,           -- comisión de Binance, en USDT
    costo_banco_bs  REAL,                              -- NULL = calcularlo con [costos]
    ruta_pago       TEXT,                              -- BDV | INTERBANCARIO (solo compras)
    metodo_pago     TEXT,
    estado          TEXT NOT NULL,                     -- COMPLETED, CANCELLED, TRADING...
    rol             TEXT,                              -- MAKER (anunciante) | TAKER (tomador)
    contraparte     TEXT,
    creada_ms       INTEGER NOT NULL,
    actualizada_ms  INTEGER NOT NULL,
    nota            TEXT,
    crudo           TEXT                               -- JSON original de Binance
);
CREATE INDEX IF NOT EXISTS idx_ordenes_creada ON ordenes (creada_ms);

CREATE TABLE IF NOT EXISTS mercado (
    ts_ms               INTEGER NOT NULL,
    precio_compra       REAL,      -- lo más barato a lo que puedes COMPRAR USDT
    precio_venta        REAL,      -- lo más caro a lo que puedes VENDER USDT
    spread_pct          REAL,
    neto_anunciante_pct REAL,
    neto_tomador_pct    REAL,
    anuncios_compra     INTEGER NOT NULL DEFAULT 0,
    anuncios_venta      INTEGER NOT NULL DEFAULT 0,
    detalle             TEXT
);
CREATE INDEX IF NOT EXISTS idx_mercado_ts ON mercado (ts_ms);

CREATE TABLE IF NOT EXISTS movimientos_banco (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms         INTEGER NOT NULL,
    tipo          TEXT NOT NULL CHECK (tipo IN ('ENTRADA', 'SALIDA')),
    monto_bs      REAL NOT NULL,
    referencia    TEXT,
    contraparte   TEXT,
    texto         TEXT,
    origen        TEXT NOT NULL DEFAULT 'manual',      -- sms | manual | demo
    orden_numero  TEXT,
    UNIQUE (tipo, referencia, monto_bs)
);
CREATE INDEX IF NOT EXISTS idx_movimientos_ts ON movimientos_banco (ts_ms);

CREATE TABLE IF NOT EXISTS eventos (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms    INTEGER NOT NULL,
    nivel    TEXT NOT NULL,          -- info | oportunidad | pago | decision | error
    mensaje  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contrapartes (
    clave          TEXT PRIMARY KEY,   -- apodo de Binance en minúsculas
    nick           TEXT NOT NULL,
    nombre         TEXT,               -- nombre verificado en el perfil de Binance
    cedula         TEXT,
    telefonos      TEXT,               -- separados por coma, ya normalizados
    bancos         TEXT,
    confianza      TEXT NOT NULL DEFAULT 'nueva',   -- nueva | verificada | sospechosa
    nota           TEXT,
    actualizado_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sesiones (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    inicio_ms       INTEGER NOT NULL,
    capital_inicial REAL NOT NULL,
    moneda_inicial  TEXT NOT NULL CHECK (moneda_inicial IN ('VES', 'USDT')),
    precio_inicial  REAL,                       -- Bs/USDT de referencia al abrir
    nota            TEXT,
    estado          TEXT NOT NULL DEFAULT 'abierta',   -- abierta | cerrada
    cierre_ms       INTEGER,
    capital_final   REAL,                       -- valuación al cerrar, en moneda_inicial
    nota_cierre     TEXT
);
CREATE INDEX IF NOT EXISTS idx_sesiones_inicio ON sesiones (inicio_ms);

CREATE TABLE IF NOT EXISTS estado (
    clave  TEXT PRIMARY KEY,
    valor  TEXT
);
"""

COLUMNAS_ORDEN = (
    "numero, origen, tipo, activo, fiat, cantidad, precio, total_bs, comision, costo_banco_bs, "
    "ruta_pago, metodo_pago, estado, rol, contraparte, creada_ms, actualizada_ms, nota"
)
CAMPOS_EDITABLES = {"ruta_pago", "costo_banco_bs", "nota"}


class BaseDatos:
    def __init__(self, ruta: str):
        self.ruta = ruta
        Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        with self.conexion() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(ESQUEMA)

    @contextmanager
    def conexion(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.ruta, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ---- órdenes -------------------------------------------------------------------

    def guardar_ordenes(self, filas: list[dict]) -> tuple[int, int]:
        """Inserta o actualiza órdenes. Devuelve (nuevas, actualizadas).

        Al actualizar se respetan los datos que tú editaste (ruta de pago,
        costo bancario y nota).
        """
        if not filas:
            return 0, 0
        ahora = ahora_ms()
        completas = []
        for f in filas:
            fila = {
                "origen": "binance", "activo": "USDT", "fiat": "VES", "comision": 0.0,
                "costo_banco_bs": None, "ruta_pago": None, "metodo_pago": None, "rol": None,
                "contraparte": None, "nota": None, "crudo": None, **f,
            }
            fila["actualizada_ms"] = ahora
            completas.append(fila)
        with self.conexion() as c:
            numeros = [f["numero"] for f in completas]
            existentes = set()
            for i in range(0, len(numeros), 500):
                lote = numeros[i:i + 500]
                marcas = ",".join("?" * len(lote))
                existentes.update(
                    r[0] for r in c.execute(f"SELECT numero FROM ordenes WHERE numero IN ({marcas})", lote)
                )
            c.executemany(
                """
                INSERT INTO ordenes (numero, origen, tipo, activo, fiat, cantidad, precio, total_bs, comision,
                                     costo_banco_bs, ruta_pago, metodo_pago, estado, rol, contraparte,
                                     creada_ms, actualizada_ms, nota, crudo)
                VALUES (:numero, :origen, :tipo, :activo, :fiat, :cantidad, :precio, :total_bs, :comision,
                        :costo_banco_bs, :ruta_pago, :metodo_pago, :estado, :rol, :contraparte,
                        :creada_ms, :actualizada_ms, :nota, :crudo)
                ON CONFLICT (numero) DO UPDATE SET
                    cantidad = excluded.cantidad,
                    precio = excluded.precio,
                    total_bs = excluded.total_bs,
                    comision = excluded.comision,
                    estado = excluded.estado,
                    rol = excluded.rol,
                    contraparte = excluded.contraparte,
                    metodo_pago = COALESCE(excluded.metodo_pago, ordenes.metodo_pago),
                    ruta_pago = COALESCE(ordenes.ruta_pago, excluded.ruta_pago),
                    actualizada_ms = excluded.actualizada_ms,
                    crudo = excluded.crudo
                """,
                completas,
            )
        nuevas = len(set(numeros) - existentes)
        return nuevas, len(set(numeros)) - nuevas

    def ordenes(self, desde_ms: int | None = None, hasta_ms: int | None = None) -> list[dict]:
        sql = f"SELECT {COLUMNAS_ORDEN} FROM ordenes WHERE 1 = 1"
        params: list = []
        if desde_ms is not None:
            sql += " AND creada_ms >= ?"
            params.append(desde_ms)
        if hasta_ms is not None:
            sql += " AND creada_ms <= ?"
            params.append(hasta_ms)
        sql += " ORDER BY creada_ms, numero"
        with self.conexion() as c:
            return [dict(r) for r in c.execute(sql, params)]

    def orden(self, numero: str) -> dict | None:
        with self.conexion() as c:
            r = c.execute(f"SELECT {COLUMNAS_ORDEN} FROM ordenes WHERE numero = ?", (numero,)).fetchone()
        return dict(r) if r else None

    def actualizar_orden(self, numero: str, **campos) -> bool:
        campos = {k: v for k, v in campos.items() if k in CAMPOS_EDITABLES}
        if not campos:
            return False
        asignaciones = ", ".join(f"{k} = :{k}" for k in campos)
        with self.conexion() as c:
            cur = c.execute(
                f"UPDATE ordenes SET {asignaciones}, actualizada_ms = :ahora WHERE numero = :numero",
                {**campos, "numero": numero, "ahora": ahora_ms()},
            )
            return cur.rowcount > 0

    def borrar_orden_manual(self, numero: str) -> bool:
        with self.conexion() as c:
            cur = c.execute("DELETE FROM ordenes WHERE numero = ? AND origen = 'manual'", (numero,))
            c.execute("UPDATE movimientos_banco SET orden_numero = NULL WHERE orden_numero = ?", (numero,))
            return cur.rowcount > 0

    # ---- mercado -------------------------------------------------------------------

    def guardar_cotizacion(self, fila: dict) -> None:
        with self.conexion() as c:
            c.execute(
                """
                INSERT INTO mercado (ts_ms, precio_compra, precio_venta, spread_pct, neto_anunciante_pct,
                                     neto_tomador_pct, anuncios_compra, anuncios_venta, detalle)
                VALUES (:ts_ms, :precio_compra, :precio_venta, :spread_pct, :neto_anunciante_pct,
                        :neto_tomador_pct, :anuncios_compra, :anuncios_venta, :detalle)
                """,
                {"detalle": None, **fila},
            )

    def guardar_cotizaciones(self, filas: list[dict]) -> None:
        with self.conexion() as c:
            c.executemany(
                """
                INSERT INTO mercado (ts_ms, precio_compra, precio_venta, spread_pct, neto_anunciante_pct,
                                     neto_tomador_pct, anuncios_compra, anuncios_venta, detalle)
                VALUES (:ts_ms, :precio_compra, :precio_venta, :spread_pct, :neto_anunciante_pct,
                        :neto_tomador_pct, :anuncios_compra, :anuncios_venta, :detalle)
                """,
                [{"detalle": None, **f} for f in filas],
            )

    def ultima_cotizacion(self) -> dict | None:
        with self.conexion() as c:
            r = c.execute("SELECT * FROM mercado ORDER BY ts_ms DESC LIMIT 1").fetchone()
        if not r:
            return None
        fila = dict(r)
        fila["detalle"] = json.loads(fila["detalle"]) if fila["detalle"] else None
        return fila

    def primera_cotizacion_ms(self) -> int | None:
        with self.conexion() as c:
            return c.execute("SELECT MIN(ts_ms) FROM mercado").fetchone()[0]

    def serie_mercado(self, desde_ms: int, hasta_ms: int, cubeta_ms: int) -> list[dict]:
        """Promedios por intervalo (cubeta) para que el gráfico no cargue miles de puntos."""
        with self.conexion() as c:
            filas = c.execute(
                """
                SELECT (ts_ms / :cubeta) * :cubeta AS ts_ms,
                       AVG(precio_compra) AS precio_compra,
                       AVG(precio_venta) AS precio_venta,
                       AVG(spread_pct) AS spread_pct,
                       AVG(neto_anunciante_pct) AS neto_anunciante_pct,
                       AVG(neto_tomador_pct) AS neto_tomador_pct
                FROM mercado
                WHERE ts_ms >= :desde AND ts_ms <= :hasta
                GROUP BY 1
                ORDER BY 1
                """,
                {"cubeta": cubeta_ms, "desde": desde_ms, "hasta": hasta_ms},
            )
            return [dict(r) for r in filas]

    def podar_mercado(self, antes_de_ms: int) -> int:
        with self.conexion() as c:
            return c.execute("DELETE FROM mercado WHERE ts_ms < ?", (antes_de_ms,)).rowcount

    # ---- banco ---------------------------------------------------------------------

    def guardar_movimiento(self, fila: dict) -> int | None:
        """Guarda un pago del banco. Devuelve su id, o None si ya estaba registrado."""
        with self.conexion() as c:
            cur = c.execute(
                """
                INSERT OR IGNORE INTO movimientos_banco
                    (ts_ms, tipo, monto_bs, referencia, contraparte, texto, origen, orden_numero)
                VALUES (:ts_ms, :tipo, :monto_bs, :referencia, :contraparte, :texto, :origen, :orden_numero)
                """,
                {"referencia": None, "contraparte": None, "texto": None, "origen": "manual",
                 "orden_numero": None, **fila},
            )
            return cur.lastrowid if cur.rowcount else None

    def movimientos(self, desde_ms: int | None = None, hasta_ms: int | None = None) -> list[dict]:
        sql = "SELECT * FROM movimientos_banco WHERE 1 = 1"
        params: list = []
        if desde_ms is not None:
            sql += " AND ts_ms >= ?"
            params.append(desde_ms)
        if hasta_ms is not None:
            sql += " AND ts_ms <= ?"
            params.append(hasta_ms)
        sql += " ORDER BY ts_ms"
        with self.conexion() as c:
            return [dict(r) for r in c.execute(sql, params)]

    def movimiento(self, movimiento_id: int) -> dict | None:
        with self.conexion() as c:
            r = c.execute("SELECT * FROM movimientos_banco WHERE id = ?", (movimiento_id,)).fetchone()
        return dict(r) if r else None

    def asignar_movimiento(self, movimiento_id: int, numero: str) -> None:
        with self.conexion() as c:
            c.execute("UPDATE movimientos_banco SET orden_numero = ? WHERE id = ?", (numero, movimiento_id))

    # ---- contrapartes (identidad verificada) ---------------------------------------

    def guardar_contraparte(self, fila: dict) -> None:
        nick = (fila.get("nick") or "").strip()
        if not nick:
            raise ValueError("La contraparte necesita un apodo de Binance.")
        datos = {
            "clave": nick.lower(), "nick": nick, "nombre": fila.get("nombre"),
            "cedula": fila.get("cedula"), "telefonos": fila.get("telefonos"),
            "bancos": fila.get("bancos"), "confianza": fila.get("confianza") or "nueva",
            "nota": fila.get("nota"), "actualizado_ms": ahora_ms(),
        }
        with self.conexion() as c:
            c.execute(
                """
                INSERT INTO contrapartes (clave, nick, nombre, cedula, telefonos, bancos, confianza, nota, actualizado_ms)
                VALUES (:clave, :nick, :nombre, :cedula, :telefonos, :bancos, :confianza, :nota, :actualizado_ms)
                ON CONFLICT (clave) DO UPDATE SET
                    nick = excluded.nick, nombre = excluded.nombre, cedula = excluded.cedula,
                    telefonos = excluded.telefonos, bancos = excluded.bancos,
                    confianza = excluded.confianza, nota = excluded.nota, actualizado_ms = excluded.actualizado_ms
                """,
                datos,
            )

    def contraparte(self, nick: str) -> dict | None:
        if not nick:
            return None
        with self.conexion() as c:
            r = c.execute("SELECT * FROM contrapartes WHERE clave = ?", (nick.strip().lower(),)).fetchone()
        return dict(r) if r else None

    def contrapartes(self) -> list[dict]:
        with self.conexion() as c:
            return [dict(r) for r in c.execute("SELECT * FROM contrapartes ORDER BY nick")]

    def borrar_contraparte(self, nick: str) -> bool:
        with self.conexion() as c:
            return c.execute("DELETE FROM contrapartes WHERE clave = ?", (nick.strip().lower(),)).rowcount > 0

    # ---- sesiones de operación -----------------------------------------------------

    def abrir_sesion(self, fila: dict) -> dict:
        if self.sesion_abierta():
            raise ValueError("Ya tienes una sesión abierta. Ciérrala antes de abrir otra.")
        with self.conexion() as c:
            cur = c.execute(
                """
                INSERT INTO sesiones (inicio_ms, capital_inicial, moneda_inicial, precio_inicial, nota, estado)
                VALUES (:inicio_ms, :capital_inicial, :moneda_inicial, :precio_inicial, :nota, 'abierta')
                """,
                {"inicio_ms": fila.get("inicio_ms") or ahora_ms(), "capital_inicial": fila["capital_inicial"],
                 "moneda_inicial": fila["moneda_inicial"], "precio_inicial": fila.get("precio_inicial"),
                 "nota": fila.get("nota")},
            )
            sid = cur.lastrowid
        return self.sesion(sid)

    def sesion(self, sesion_id: int) -> dict | None:
        with self.conexion() as c:
            r = c.execute("SELECT * FROM sesiones WHERE id = ?", (sesion_id,)).fetchone()
        return dict(r) if r else None

    def sesion_abierta(self) -> dict | None:
        with self.conexion() as c:
            r = c.execute("SELECT * FROM sesiones WHERE estado = 'abierta' ORDER BY inicio_ms DESC LIMIT 1").fetchone()
        return dict(r) if r else None

    def sesiones(self, limite: int = 50) -> list[dict]:
        with self.conexion() as c:
            return [dict(r) for r in c.execute("SELECT * FROM sesiones ORDER BY inicio_ms DESC LIMIT ?", (limite,))]

    def cerrar_sesion(self, sesion_id: int, capital_final: float, nota_cierre: str | None = None) -> dict | None:
        with self.conexion() as c:
            c.execute(
                "UPDATE sesiones SET estado = 'cerrada', cierre_ms = ?, capital_final = ?, nota_cierre = ? "
                "WHERE id = ? AND estado = 'abierta'",
                (ahora_ms(), capital_final, nota_cierre, sesion_id),
            )
        return self.sesion(sesion_id)

    def vaciar(self) -> None:
        """Borra TODO el contenido (solo para la base de la simulación)."""
        with self.conexion() as c:
            for tabla in ("ordenes", "mercado", "movimientos_banco", "eventos", "contrapartes", "sesiones", "estado"):
                c.execute(f"DELETE FROM {tabla}")

    # ---- eventos y estado ----------------------------------------------------------

    def registrar_evento(self, nivel: str, mensaje: str, ts_ms: int | None = None) -> None:
        with self.conexion() as c:
            c.execute(
                "INSERT INTO eventos (ts_ms, nivel, mensaje) VALUES (?, ?, ?)",
                (ts_ms if ts_ms is not None else ahora_ms(), nivel, mensaje),
            )

    def eventos(self, limite: int = 50, nivel: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM eventos", []
        if nivel:
            sql += " WHERE nivel = ?"
            params.append(nivel)
        with self.conexion() as c:
            filas = c.execute(sql + " ORDER BY ts_ms DESC, id DESC LIMIT ?", (*params, limite))
            return [dict(r) for r in filas]

    def leer_estado(self, clave: str, defecto: str | None = None) -> str | None:
        with self.conexion() as c:
            r = c.execute("SELECT valor FROM estado WHERE clave = ?", (clave,)).fetchone()
        return r[0] if r else defecto

    def escribir_estado(self, clave: str, valor) -> None:
        with self.conexion() as c:
            c.execute(
                "INSERT INTO estado (clave, valor) VALUES (?, ?) "
                "ON CONFLICT (clave) DO UPDATE SET valor = excluded.valor",
                (clave, str(valor)),
            )
