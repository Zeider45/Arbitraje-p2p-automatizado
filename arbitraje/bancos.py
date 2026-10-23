"""Comparador de bancos: en qué banco conviene operar y con cuál obtienes más rendimiento.

Consulta el mercado P2P público de Binance banco por banco (precio más barato para comprar USDT y más
caro para venderlo) y calcula, con tus costos, cuánto te deja cada vuelta según el banco que tengas tú:
pagar a otra persona de tu mismo banco no cuesta, pagar a otro banco sí (costo interbancario).
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, replace

import httpx

from . import mercado
from .config import RUTA_BDV, RUTA_INTERBANCARIA, Config, Costos
from .mercado import neto_entre_anuncios

# identificadores de Binance P2P -> nombre para mostrar
BANCOS = {
    "BancoDeVenezuela": "Banco de Venezuela",
    "Banesco": "Banesco",
    "Mercantil": "Mercantil",
    "Provincial": "Provincial",
    "BNCBancoNacional": "BNC",
    "Bancamiga": "Bancamiga",
    "Bancaribe": "Bancaribe",
    "Banplus": "Banplus",
    "BancoDelTesoro": "Banco del Tesoro",
    "BancoPlaza": "Banco Plaza",
    "BFC": "BFC",
    "PagoMovil": "Pago Móvil",
}
# identificador de Binance -> código bancario (para "Pegar Datos" cuando no sabemos el banco de la contraparte)
CODIGOS = {
    "BancoDeVenezuela": "0102", "Banesco": "0134", "Mercantil": "0105", "Provincial": "0108",
    "BNCBancoNacional": "0191", "Bancamiga": "0172", "Bancaribe": "0114", "Banplus": "0174",
    "BancoDelTesoro": "0163", "BancoPlaza": "0138", "BFC": "0151",
}
POCOS_ANUNCIOS = 3  # menos que esto: el precio puede no ser real


@dataclass
class CotizacionBanco:
    banco: str
    precio_compra: float | None   # lo más barato a lo que puedes comprar USDT con ese banco
    precio_venta: float | None    # lo más caro a lo que puedes venderlos con ese banco
    anuncios_compra: int = 0
    anuncios_venta: int = 0
    liquidez_compra: float = 0.0  # USDT disponibles en los mejores anuncios de compra
    liquidez_venta: float = 0.0


def ruta_con(banco: str, mi_banco: str) -> str:
    """Pagar a alguien de tu mismo banco es gratis; a otro banco (o por Pago Móvil) es interbancario."""
    return RUTA_BDV if banco == mi_banco and banco != "PagoMovil" else RUTA_INTERBANCARIA


_CACHE: dict = {}
TTL_SEG = 240


async def consultar(http: httpx.AsyncClient, cfg: Config, bancos: list[str], concurrencia: int = 4) -> list[CotizacionBanco]:
    """Mejor precio de compra y de venta de cada banco. Un banco que falla se omite, no rompe a los demás.

    Se guarda unos minutos en memoria para que el modo real y la simulación no repitan las mismas consultas.
    """
    clave = (tuple(bancos), cfg.binance.url_p2p, cfg.mercado.min_ordenes_mes, cfg.mercado.min_tasa_completado)
    guardado = _CACHE.get(clave)
    if guardado and time.monotonic() - guardado[0] < TTL_SEG:
        return guardado[1]
    sem = asyncio.Semaphore(concurrencia)

    async def uno(banco: str) -> CotizacionBanco | None:
        cfg_b = replace(cfg, mercado=replace(cfg.mercado, metodos_pago=[banco]))
        try:
            async with sem:
                compra, venta = await asyncio.gather(
                    mercado.buscar(http, cfg_b, mercado.COMPRAR), mercado.buscar(http, cfg_b, mercado.VENDER)
                )
        except (httpx.HTTPError, mercado.ErrorMercado):
            return None
        return CotizacionBanco(
            banco, compra[0].precio if compra else None, venta[0].precio if venta else None,
            len(compra), len(venta),
            sum(a.disponible for a in compra[:5]), sum(a.disponible for a in venta[:5]),
        )

    resultado = [c for c in await asyncio.gather(*(uno(b) for b in bancos)) if c]
    if resultado:
        _CACHE[clave] = (time.monotonic(), resultado)
    return resultado


def simuladas(cotizacion: dict | None, bancos: list[str], semilla: int = 11) -> list[CotizacionBanco]:
    """Cotizaciones inventadas alrededor del mercado actual (solo para el modo demo sin red)."""
    if not cotizacion or not cotizacion.get("precio_compra") or not cotizacion.get("precio_venta"):
        return []
    rnd = random.Random(semilla)
    out = []
    for b in bancos:
        d = rnd.uniform(-0.004, 0.004)
        out.append(CotizacionBanco(
            b, round(cotizacion["precio_compra"] * (1 + d + rnd.uniform(0, 0.004)), 2),
            round(cotizacion["precio_venta"] * (1 + d - rnd.uniform(0, 0.004)), 2),
            rnd.randint(2, 18), rnd.randint(2, 18), rnd.uniform(200, 6000), rnd.uniform(200, 6000),
        ))
    return out


def analizar(cotizaciones: list[CotizacionBanco], costos: Costos, mi_banco: str, tick: float) -> dict:
    """Ranking de bancos y mejores combinaciones (comprar con uno y vender con otro) para TU banco."""
    fa = costos.comision_anunciante_pct / 100
    recibir = 1 - costos.costo_banco_venta_pct / 100
    filas = []
    for q in cotizaciones:
        if not q.precio_compra or not q.precio_venta:
            continue
        ruta = ruta_con(q.banco, mi_banco)
        neto = neto_entre_anuncios(q.precio_venta + tick, q.precio_compra - tick, costos, ruta)
        filas.append({
            "banco": q.banco, "nombre": BANCOS.get(q.banco, q.banco),
            "precio_compra": q.precio_compra, "precio_venta": q.precio_venta,
            "spread_pct": (q.precio_compra / q.precio_venta - 1) * 100,
            "neto_pct": neto, "ruta": ruta,
            "anuncios_compra": q.anuncios_compra, "anuncios_venta": q.anuncios_venta,
            "liquidez_usdt": min(q.liquidez_compra, q.liquidez_venta),
            "poca_liquidez": min(q.anuncios_compra, q.anuncios_venta) < POCOS_ANUNCIOS,
            "es_tu_banco": q.banco == mi_banco,
        })
    filas.sort(key=lambda f: f["neto_pct"], reverse=True)

    # comprar a un banco y vender a otro: el costo de pagar depende del banco donde compras
    combos = []
    for a in cotizaciones:          # donde compras (pagas)
        for b in cotizaciones:      # donde vendes (cobras)
            if not a.precio_venta or not b.precio_compra:
                continue
            compra = a.precio_venta + tick       # tu anuncio de compra, un tick sobre el mejor comprador de ese banco
            venta = b.precio_compra - tick       # tu anuncio de venta, un tick bajo el mejor vendedor de ese banco
            ruta = ruta_con(a.banco, mi_banco)
            factor = venta / compra * (1 - fa) / (1 + fa) * recibir / (1 + costos.costo_compra_pct(ruta) / 100)
            combos.append({
                "compra_banco": a.banco, "compra_nombre": BANCOS.get(a.banco, a.banco), "precio_compra": compra,
                "venta_banco": b.banco, "venta_nombre": BANCOS.get(b.banco, b.banco), "precio_venta": venta,
                "ruta": ruta, "neto_pct": (factor - 1) * 100,
                "poca_liquidez": min(a.anuncios_venta, b.anuncios_compra) < POCOS_ANUNCIOS,
            })
    combos.sort(key=lambda c: c["neto_pct"], reverse=True)
    combos = combos[:5]

    return {
        "mi_banco": mi_banco, "mi_banco_nombre": BANCOS.get(mi_banco, mi_banco),
        "bancos": filas, "combinaciones": combos,
        "recomendacion": _recomendar(filas, combos, mi_banco),
    }


def _recomendar(filas: list[dict], combos: list[dict], mi_banco: str) -> str:
    if not filas:
        return "Todavía no hay datos de los bancos. Espera unos minutos."
    solidas = [f for f in filas if not f["poca_liquidez"]] or filas
    mejor = solidas[0]
    mio = next((f for f in filas if f["es_tu_banco"]), None)
    partes = [f"El banco con más rendimiento ahora es {mejor['nombre']}: {mejor['neto_pct']:+.2f} % por vuelta."]
    if mio:
        if mio is mejor:
            partes.append("Es tu banco: opera ahí.")
        else:
            dif = mejor["neto_pct"] - mio["neto_pct"]
            partes.append(f"Con tu banco ({mio['nombre']}) ganas {mio['neto_pct']:+.2f} %; "
                          f"cambiar a {mejor['nombre']} mejoraría {dif:.2f} puntos"
                          + (", pero pagarías costo interbancario." if mejor["ruta"] == RUTA_INTERBANCARIA else "."))
    if combos and combos[0]["neto_pct"] > (mejor["neto_pct"] + 0.05):
        c = combos[0]
        partes.append(f"Mejor combinación: comprar con {c['compra_nombre']} y vender con {c['venta_nombre']} "
                      f"({c['neto_pct']:+.2f} %).")
    return " ".join(partes)
