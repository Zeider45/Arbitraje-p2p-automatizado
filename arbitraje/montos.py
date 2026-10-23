"""Estudio por monto: ¿con cuántos bolívares por operación rinde más tu vuelta?

Binance P2P permite pedir solo los anuncios que aceptan un monto. Con eso se mide, para cada tramo, el mejor
precio de compra y de venta, y con tus costos (incluido el mínimo fijo del interbancario, que pesa más en
montos chicos) se calcula el neto por vuelta. Así se elige el monto con mejor rendimiento en vez de suponerlo.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, replace

import httpx

from . import mercado
from .config import Config, Costos
from .mercado import POCOS_ANUNCIOS, CacheTTL, buscar_ambos_lados
from .simulador import una_vuelta
from .utilidades import formato_numero

TRAMOS_BS = [20_000, 50_000, 100_000, 250_000, 500_000, 1_000_000, 2_500_000]
_CACHE = CacheTTL()


@dataclass
class CotizacionMonto:
    monto_bs: float
    precio_compra: float | None
    precio_venta: float | None
    anuncios_compra: int = 0
    anuncios_venta: int = 0


def tramos_para(capital_bs: float) -> list[float]:
    """Tramos que puedes operar con tu capital (el último es todo el capital)."""
    tramos = [float(t) for t in TRAMOS_BS if t < capital_bs]
    if capital_bs > 0:
        tramos.append(float(round(capital_bs, -3)) or float(capital_bs))
    return tramos


async def consultar(http: httpx.AsyncClient, cfg: Config, banco_compra: str, banco_venta: str,
                    tramos: list[float]) -> list[CotizacionMonto]:
    clave = (banco_compra, banco_venta, tuple(tramos), cfg.binance.url_p2p, cfg.mercado.min_ordenes_mes, cfg.mercado.min_tasa_completado)
    guardado = _CACHE.obtener(clave)
    if guardado is not None:
        return guardado
    sem = asyncio.Semaphore(3)

    async def uno(monto: float) -> CotizacionMonto | None:
        cfg_m = replace(cfg, mercado=replace(cfg.mercado, banco_compra=banco_compra, banco_venta=banco_venta, monto_bs=monto))
        try:
            async with sem:
                compra, venta = await buscar_ambos_lados(http, cfg_m)
        except (httpx.HTTPError, mercado.ErrorMercado):
            return None
        return CotizacionMonto(monto, compra[0].precio if compra else None, venta[0].precio if venta else None,
                               len(compra), len(venta))

    resultado = [c for c in await asyncio.gather(*(uno(t) for t in tramos)) if c]
    if resultado:
        _CACHE.guardar(clave, resultado)
    return resultado


def simuladas(cotizacion: dict | None, tramos: list[float], semilla: int = 5) -> list[CotizacionMonto]:
    """Datos inventados para el modo demo sin red: la brecha se cierra un poco al subir el monto."""
    if not cotizacion or not cotizacion.get("precio_compra") or not cotizacion.get("precio_venta"):
        return []
    rnd = random.Random(semilla)
    out = []
    for i, t in enumerate(tramos):
        ajuste = 0.0035 * (len(tramos) - i) / max(len(tramos), 1)
        out.append(CotizacionMonto(
            t, round(cotizacion["precio_compra"] * (1 + ajuste + rnd.uniform(-0.0005, 0.0005)), 2),
            round(cotizacion["precio_venta"] * (1 - ajuste + rnd.uniform(-0.0005, 0.0005)), 2),
            rnd.randint(2, 15), rnd.randint(2, 15)))
    return out


def analizar(cotizaciones: list[CotizacionMonto], costos: Costos, ruta: str, tick: float, margen_minimo_pct: float | None = None) -> dict:
    filas = []
    for q in cotizaciones:
        if not q.precio_compra or not q.precio_venta:
            continue
        compra, venta = q.precio_venta + tick, q.precio_compra - tick   # tus precios como anunciante
        fin = una_vuelta(q.monto_bs, "VES", compra, venta, costos, ruta, "anunciante")
        ganancia = fin - q.monto_bs
        filas.append({
            "monto_bs": q.monto_bs, "monto_usdt": q.monto_bs / compra,
            "precio_compra": compra, "precio_venta": venta,
            "neto_pct": ganancia / q.monto_bs * 100, "ganancia_bs": ganancia,
            "ganancia_usdt": ganancia / venta,
            "anuncios_compra": q.anuncios_compra, "anuncios_venta": q.anuncios_venta,
            "poca_liquidez": min(q.anuncios_compra, q.anuncios_venta) < POCOS_ANUNCIOS,
        })
    filas.sort(key=lambda f: f["monto_bs"])
    solidas = [f for f in filas if not f["poca_liquidez"]] or filas
    cumplen = [f for f in solidas if margen_minimo_pct is not None and f["neto_pct"] >= margen_minimo_pct]
    if cumplen:
        # entre los montos que cumplen tu margen mínimo, el que más Bs deja por vuelta (aprovecha mejor tu capital)
        mejor = max(cumplen, key=lambda f: f["ganancia_bs"])
    else:
        mejor = max(solidas, key=lambda f: f["neto_pct"]) if solidas else None
    for f in filas:
        f["mejor"] = mejor is not None and f is mejor
    return {"tramos": filas, "mejor_monto_bs": mejor["monto_bs"] if mejor else None,
            "cumple_minimo": bool(cumplen), "margen_minimo_pct": margen_minimo_pct,
            "recomendacion": _recomendar(filas, mejor, margen_minimo_pct, bool(cumplen))}


def _recomendar(filas: list[dict], mejor: dict | None, minimo: float | None = None, cumple: bool = True) -> str:
    if not filas or mejor is None:
        return "Todavía no hay datos por monto. Espera unos minutos."
    chico, grande = filas[0], filas[-1]
    partes = [f"Mejor rendimiento ahora: operaciones de ~Bs {formato_numero(mejor['monto_bs'], 0)} ({mejor['neto_pct']:+.2f} % por vuelta)."]
    if minimo is not None and not cumple:
        partes.append(f"Ojo: ningún monto alcanza tu margen mínimo de {minimo:g} %; operar así deja poco o pierde.")
    if len(filas) > 1 and chico is not grande:
        if chico["neto_pct"] > grande["neto_pct"] + 0.05:
            partes.append(f"Sí se nota: con Bs {formato_numero(grande['monto_bs'], 0)} baja a {grande['neto_pct']:+.2f} %. "
                          "Conviene repartir el capital en operaciones más chicas.")
        elif grande["neto_pct"] > chico["neto_pct"] + 0.05:
            partes.append(f"Ahora los montos grandes rinden más ({grande['neto_pct']:+.2f} % con Bs {formato_numero(grande['monto_bs'], 0)}).")
        else:
            partes.append("Por ahora el monto casi no cambia el rendimiento.")
    mayor_ganancia = max(filas, key=lambda f: f["ganancia_bs"])
    if mayor_ganancia is not mejor and mayor_ganancia["ganancia_bs"] > 0:
        partes.append(f"En bolívares ganas más por vuelta con Bs {formato_numero(mayor_ganancia['monto_bs'], 0)} "
                      f"(+Bs {formato_numero(mayor_ganancia['ganancia_bs'], 0)}), aunque el porcentaje sea menor.")
    return " ".join(partes)
