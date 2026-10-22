"""Contabilidad del arbitraje: ciclos FIFO, ganancia realizada y KPIs del panel.

Cada orden completada se convierte en un flujo neto:
  - COMPRA: entran (cantidad - comisión) USDT y salen (total + costo bancario) Bs.
  - VENTA:  salen (cantidad + comisión) USDT y entran (total - costo bancario) Bs.
Compras y ventas se emparejan por orden de llegada (FIFO), sin importar si
empezaste comprando o vendiendo. Cada pareja es un "ciclo":
  ganancia_bs   = USDT emparejados x (precio efectivo de venta - precio efectivo de compra)
  ganancia_usdt = ganancia_bs convertida al precio de la orden que cerró el ciclo
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import timedelta
from zoneinfo import ZoneInfo

from .config import RUTA_BDV, RUTA_INTERBANCARIA, Costos
from .utilidades import fecha_local

EPS = 1e-9
COMPLETADA = "COMPLETED"
EN_CURSO = {"PENDING", "TRADING", "BUYER_PAYED", "DISTRIBUTING", "IN_APPEAL"}


@dataclass
class Flujo:
    numero: str
    tipo: str
    ts_ms: int
    usdt: float
    bs: float

    @property
    def precio(self) -> float:
        return self.bs / self.usdt


@dataclass
class Ciclo:
    apertura: str
    cierre: str
    sentido: str  # "COMPRA→VENTA" o "VENTA→COMPRA"
    usdt: float
    precio_compra: float
    precio_venta: float
    ganancia_bs: float
    ganancia_usdt: float
    spread_pct: float
    ts_apertura: int
    ts_cierre: int


@dataclass
class Lote:
    numero: str
    tipo: str
    ts_ms: int
    usdt: float
    precio: float


@dataclass
class Posicion:
    usdt: float  # > 0: USDT comprados sin vender; < 0: USDT vendidos sin recomprar
    precio_promedio: float | None
    lotes: int


def ruta_de(orden: dict, ruta_por_defecto: str) -> str:
    return orden.get("ruta_pago") or ruta_por_defecto


def costo_bancario(orden: dict, costos: Costos, ruta_por_defecto: str) -> float:
    """Comisión del banco en Bs. Si la orden trae un costo cargado a mano, se usa ese."""
    if orden.get("costo_banco_bs") is not None:
        return float(orden["costo_banco_bs"])
    if orden["tipo"] == "COMPRA":
        ruta = ruta_de(orden, ruta_por_defecto)
        pct = costos.costo_compra_pct(ruta)
        if pct <= 0:
            return 0.0
        minimo = costos.costo_interbancario_min_bs if ruta == RUTA_INTERBANCARIA else 0.0
        return max(orden["total_bs"] * pct / 100, minimo)
    return orden["total_bs"] * costos.costo_banco_venta_pct / 100


def a_flujo(orden: dict, costos: Costos, ruta_por_defecto: str) -> Flujo | None:
    banco = costo_bancario(orden, costos, ruta_por_defecto)
    if orden["tipo"] == "COMPRA":
        usdt = orden["cantidad"] - orden["comision"]
        bs = orden["total_bs"] + banco
    else:
        usdt = orden["cantidad"] + orden["comision"]
        bs = orden["total_bs"] - banco
    if usdt <= EPS or bs <= 0:
        return None
    return Flujo(orden["numero"], orden["tipo"], orden["creada_ms"], usdt, bs)


def emparejar(ordenes: list[dict], costos: Costos, ruta_por_defecto: str) -> tuple[list[Ciclo], Posicion]:
    """Empareja compras y ventas completadas (FIFO). Devuelve los ciclos y lo que quedó abierto."""
    abiertos: deque[Lote] = deque()
    ciclos: list[Ciclo] = []
    completadas = sorted(
        (o for o in ordenes if o["estado"] == COMPLETADA), key=lambda o: (o["creada_ms"], o["numero"])
    )
    for orden in completadas:
        flujo = a_flujo(orden, costos, ruta_por_defecto)
        if flujo is None:
            continue
        restante = flujo.usdt
        while restante > EPS and abiertos and abiertos[0].tipo != flujo.tipo:
            lote = abiertos[0]
            usdt = min(restante, lote.usdt)
            if flujo.tipo == "VENTA":
                precio_compra, precio_venta, precio_cierre = lote.precio, flujo.precio, flujo.precio
            else:
                precio_compra, precio_venta, precio_cierre = flujo.precio, lote.precio, flujo.precio
            ganancia_bs = usdt * (precio_venta - precio_compra)
            ciclos.append(
                Ciclo(
                    apertura=lote.numero,
                    cierre=flujo.numero,
                    sentido=f"{lote.tipo}→{flujo.tipo}",
                    usdt=usdt,
                    precio_compra=precio_compra,
                    precio_venta=precio_venta,
                    ganancia_bs=ganancia_bs,
                    ganancia_usdt=ganancia_bs / precio_cierre,
                    spread_pct=(precio_venta / precio_compra - 1) * 100,
                    ts_apertura=lote.ts_ms,
                    ts_cierre=flujo.ts_ms,
                )
            )
            lote.usdt -= usdt
            restante -= usdt
            if lote.usdt <= EPS:
                abiertos.popleft()
        if restante > EPS:
            abiertos.append(Lote(flujo.numero, flujo.tipo, flujo.ts_ms, restante, flujo.precio))

    total = sum(lote.usdt for lote in abiertos)
    if total <= EPS:
        return ciclos, Posicion(0.0, None, 0)
    signo = 1 if abiertos[0].tipo == "COMPRA" else -1
    promedio = sum(lote.usdt * lote.precio for lote in abiertos) / total
    return ciclos, Posicion(signo * total, promedio, len(abiertos))


def valorar_posicion(posicion: Posicion, cotizacion: dict | None) -> dict:
    """Ganancia o pérdida si cerraras hoy la posición abierta como tomador."""
    valor = {"usdt": posicion.usdt, "precio_promedio": posicion.precio_promedio, "lotes": posicion.lotes,
             "precio_mercado": None, "no_realizado_bs": None, "no_realizado_usdt": None}
    if abs(posicion.usdt) <= EPS or not posicion.precio_promedio or not cotizacion:
        return valor
    if posicion.usdt > 0:  # tienes USDT: los venderías al mejor comprador
        precio = cotizacion.get("precio_venta")
        if precio:
            valor["no_realizado_bs"] = posicion.usdt * (precio - posicion.precio_promedio)
    else:  # tienes Bs: recomprarías USDT al mejor vendedor
        precio = cotizacion.get("precio_compra")
        if precio:
            valor["no_realizado_bs"] = -posicion.usdt * (posicion.precio_promedio - precio)
    if valor["no_realizado_bs"] is not None:
        valor["precio_mercado"] = precio
        valor["no_realizado_usdt"] = valor["no_realizado_bs"] / precio
    return valor


def resumen(
    ordenes: list[dict],
    ciclos: list[Ciclo],
    posicion: Posicion,
    *,
    capital_usdt: float,
    tz: ZoneInfo,
    desde_ms: int | None,
    hasta_ms: int,
    ahora_ms: int,
    ruta_por_defecto: str,
    limites_bs: dict[str, float],
    cotizacion: dict | None = None,
) -> dict:
    """KPIs del período [desde_ms, hasta_ms] (desde_ms = None: todo el historial)."""

    def en_periodo(ts: int) -> bool:
        return (desde_ms is None or ts >= desde_ms) and ts <= hasta_ms

    cs = [c for c in ciclos if en_periodo(c.ts_cierre)]
    os_ = [o for o in ordenes if en_periodo(o["creada_ms"])]
    completadas = [o for o in os_ if o["estado"] == COMPLETADA]

    ganancia_usdt = sum(c.ganancia_usdt for c in cs)
    usdt_emparejado = sum(c.usdt for c in cs)
    ganancia_anterior = None
    if desde_ms is not None:
        duracion = hasta_ms - desde_ms
        ganancia_anterior = sum(c.ganancia_usdt for c in ciclos if desde_ms - duracion <= c.ts_cierre < desde_ms)

    primer_ts = desde_ms if desde_ms is not None else min((o["creada_ms"] for o in ordenes), default=hasta_ms)
    dias = (fecha_local(hasta_ms, tz) - fecha_local(primer_ts, tz)).days + 1
    roi_pct = ganancia_usdt / capital_usdt * 100

    por_dia: dict = defaultdict(float)
    for c in cs:
        por_dia[fecha_local(c.ts_cierre, tz)] += c.ganancia_usdt
    mejor = max(por_dia.items(), key=lambda kv: kv[1], default=None)
    peor = min(por_dia.items(), key=lambda kv: kv[1], default=None)

    hoy = fecha_local(ahora_ms, tz)
    ciclos_hoy = [c for c in ciclos if fecha_local(c.ts_cierre, tz) == hoy]
    pagado_hoy = {RUTA_BDV: 0.0, RUTA_INTERBANCARIA: 0.0}
    for o in ordenes:
        if (
            o["tipo"] == "COMPRA"
            and (o["estado"] == COMPLETADA or o["estado"] in EN_CURSO)
            and fecha_local(o["creada_ms"], tz) == hoy
        ):
            pagado_hoy[ruta_de(o, ruta_por_defecto)] += o["total_bs"]

    return {
        "ganancia_usdt": ganancia_usdt,
        "ganancia_bs": sum(c.ganancia_bs for c in cs),
        "ganancia_anterior_usdt": ganancia_anterior,
        "ganancia_hoy_usdt": sum(c.ganancia_usdt for c in ciclos_hoy),
        "ganancia_hoy_bs": sum(c.ganancia_bs for c in ciclos_hoy),
        "ciclos_hoy": len(ciclos_hoy),
        "capital_usdt": capital_usdt,
        "roi_pct": roi_pct,
        "roi_diario_pct": roi_pct / dias,
        "dias": dias,
        "volumen_usdt": sum(o["cantidad"] for o in completadas),
        "volumen_bs": sum(o["total_bs"] for o in completadas),
        "ordenes": {
            "completadas": len(completadas),
            "compras": sum(1 for o in completadas if o["tipo"] == "COMPRA"),
            "ventas": sum(1 for o in completadas if o["tipo"] == "VENTA"),
            "canceladas": sum(1 for o in os_ if o["estado"].startswith("CANCEL")),
            "en_curso": sum(1 for o in os_ if o["estado"] in EN_CURSO),
        },
        "ciclos": len(cs),
        "usdt_emparejado": usdt_emparejado,
        "spread_promedio_pct": (
            sum(c.spread_pct * c.usdt for c in cs) / usdt_emparejado if usdt_emparejado > EPS else None
        ),
        "rotacion": usdt_emparejado / capital_usdt,
        "dias_operados": len(por_dia),
        "dias_positivos": sum(1 for v in por_dia.values() if v > 0),
        "mejor_dia": {"fecha": mejor[0].isoformat(), "ganancia_usdt": mejor[1]} if mejor else None,
        "peor_dia": {"fecha": peor[0].isoformat(), "ganancia_usdt": peor[1]} if peor else None,
        "posicion": valorar_posicion(posicion, cotizacion),
        "pagado_hoy": {
            ruta.lower(): {"usado_bs": pagado_hoy[ruta], "limite_bs": limites_bs.get(ruta, 0.0)}
            for ruta in (RUTA_BDV, RUTA_INTERBANCARIA)
        },
    }


def serie_diaria(
    ordenes: list[dict], ciclos: list[Ciclo], tz: ZoneInfo, desde_ms: int | None, hasta_ms: int
) -> list[dict]:
    """Una fila por día calendario del período (los días sin operaciones van en cero)."""
    if desde_ms is None:
        desde_ms = min((o["creada_ms"] for o in ordenes), default=None)
        if desde_ms is None:
            return []
    primer_dia, ultimo_dia = fecha_local(desde_ms, tz), fecha_local(hasta_ms, tz)
    filas = {}
    dia = primer_dia
    while dia <= ultimo_dia:
        filas[dia] = {"fecha": dia.isoformat(), "ganancia_usdt": 0.0, "ganancia_bs": 0.0, "ciclos": 0,
                      "volumen_usdt": 0.0, "ordenes": 0}
        dia += timedelta(days=1)
    for c in ciclos:
        fila = filas.get(fecha_local(c.ts_cierre, tz))
        if fila:
            fila["ganancia_usdt"] += c.ganancia_usdt
            fila["ganancia_bs"] += c.ganancia_bs
            fila["ciclos"] += 1
    for o in ordenes:
        if o["estado"] != COMPLETADA:
            continue
        fila = filas.get(fecha_local(o["creada_ms"], tz))
        if fila:
            fila["volumen_usdt"] += o["cantidad"]
            fila["ordenes"] += 1
    acumulada = 0.0
    for fila in filas.values():
        acumulada += fila["ganancia_usdt"]
        fila["acumulada_usdt"] = acumulada
    return list(filas.values())
