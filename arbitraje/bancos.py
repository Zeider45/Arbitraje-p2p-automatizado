"""Modalidades de operación: con qué banco compras USDT y con cuál los vendes.

Consulta el mercado P2P público de Binance banco por banco (precio más barato para comprar USDT y más
caro para venderlo) y calcula, con tus costos, cuánto te deja cada modalidad (banco de compra + banco de venta).

Costos: comprar y vender con el MISMO banco no cuesta nada. Si compras con un banco y vendes con otro, el dinero
tiene que pasar de una de tus cuentas a la otra: eso paga el costo interbancario. Pago Móvil también paga ese costo.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, replace

import httpx

from . import mercado
from .config import RUTA_BDV, RUTA_INTERBANCARIA, Config, Costos
from .mercado import POCOS_ANUNCIOS, CacheTTL, buscar_ambos_lados, neto_entre_anuncios

# identificador de Binance P2P -> nombre para mostrar
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
MAX_COMBINACIONES = 6


@dataclass
class CotizacionBanco:
    banco: str
    precio_compra: float | None   # lo más barato a lo que puedes comprar USDT con ese banco
    precio_venta: float | None    # lo más caro a lo que puedes venderlos con ese banco
    anuncios_compra: int = 0
    anuncios_venta: int = 0
    liquidez_compra: float = 0.0  # USDT disponibles en los mejores anuncios de compra
    liquidez_venta: float = 0.0


def ruta_modalidad(banco_compra: str, banco_venta: str) -> str:
    """Mismo banco en ambos lados = sin costo; bancos distintos o Pago Móvil = costo interbancario."""
    return RUTA_BDV if banco_compra == banco_venta and banco_compra != "PagoMovil" else RUTA_INTERBANCARIA


_CACHE = CacheTTL()


async def consultar(http: httpx.AsyncClient, cfg: Config, bancos: list[str], concurrencia: int = 4) -> list[CotizacionBanco]:
    """Mejor precio de compra y de venta de cada banco. Un banco que falla se omite, no rompe a los demás.

    Se guarda unos minutos en memoria para que el modo real y la simulación no repitan las mismas consultas.
    """
    clave = (tuple(bancos), cfg.binance.url_p2p, cfg.mercado.min_ordenes_mes, cfg.mercado.min_tasa_completado,
             cfg.mercado.monto_bs)
    guardado = _CACHE.obtener(clave)
    if guardado is not None:
        return guardado
    sem = asyncio.Semaphore(concurrencia)

    async def uno(banco: str) -> CotizacionBanco | None:
        cfg_b = replace(cfg, mercado=replace(cfg.mercado, metodos_pago=[banco], banco_compra="", banco_venta=""))
        try:
            async with sem:
                compra, venta = await buscar_ambos_lados(http, cfg_b)
        except (httpx.HTTPError, mercado.ErrorMercado):
            return None
        return CotizacionBanco(
            banco, compra[0].precio if compra else None, venta[0].precio if venta else None,
            len(compra), len(venta),
            sum(a.disponible for a in compra[:5]), sum(a.disponible for a in venta[:5]),
        )

    resultado = [c for c in await asyncio.gather(*(uno(b) for b in bancos)) if c]
    if resultado:
        _CACHE.guardar(clave, resultado)
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


def _fila(a: CotizacionBanco, b: CotizacionBanco, costos: Costos, tick: float) -> dict:
    """Modalidad 'compro con a, vendo con b' como anunciante."""
    compra = a.precio_venta + tick   # tu anuncio de compra: un tick sobre el mejor comprador de ese banco
    venta = b.precio_compra - tick   # tu anuncio de venta: un tick bajo el mejor vendedor de ese banco
    ruta = ruta_modalidad(a.banco, b.banco)
    return {
        "compra_banco": a.banco, "compra_nombre": BANCOS.get(a.banco, a.banco), "precio_compra": compra,
        "venta_banco": b.banco, "venta_nombre": BANCOS.get(b.banco, b.banco), "precio_venta": venta,
        "ruta": ruta, "neto_pct": neto_entre_anuncios(compra, venta, costos, ruta),
        "poca_liquidez": min(a.anuncios_venta, b.anuncios_compra) < POCOS_ANUNCIOS,
    }


def analizar(cotizaciones: list[CotizacionBanco], costos: Costos, tick: float,
             banco_compra: str, banco_venta: str) -> dict:
    """Ranking por banco (comprando y vendiendo con él), mejores modalidades y cómo rinde la que tienes elegida."""
    validas = [q for q in cotizaciones if q.precio_compra and q.precio_venta]
    porbanco = {q.banco: q for q in cotizaciones}

    filas = []
    for q in validas:
        f = _fila(q, q, costos, tick)
        f.update(banco=q.banco, nombre=BANCOS.get(q.banco, q.banco),
                 spread_pct=(q.precio_compra / q.precio_venta - 1) * 100,
                 anuncios_compra=q.anuncios_compra, anuncios_venta=q.anuncios_venta,
                 liquidez_usdt=min(q.liquidez_compra, q.liquidez_venta),
                 en_uso=q.banco == banco_compra == banco_venta)
        filas.append(f)
    # primero los bancos con anuncios suficientes: un precio sostenido por 1 solo anuncio no es operable
    filas.sort(key=lambda f: (f["poca_liquidez"], -f["neto_pct"]))

    combos = [_fila(a, b, costos, tick) for a in validas for b in validas]
    combos.sort(key=lambda c: (c["poca_liquidez"], -c["neto_pct"]))
    combos = combos[:MAX_COMBINACIONES]
    for c in combos:
        c["en_uso"] = c["compra_banco"] == banco_compra and c["venta_banco"] == banco_venta

    actual = None
    if banco_compra in porbanco and banco_venta in porbanco:
        a, b = porbanco[banco_compra], porbanco[banco_venta]
        if a.precio_venta and b.precio_compra:
            actual = _fila(a, b, costos, tick)
    return {
        "banco_compra": banco_compra, "banco_venta": banco_venta,
        "bancos": filas, "combinaciones": combos, "actual": actual,
        "recomendacion": _recomendar(combos, actual),
    }


def _recomendar(combos: list[dict], actual: dict | None) -> str:
    solidas = [c for c in combos if not c["poca_liquidez"]]
    if not solidas:
        return "Todavía no hay datos suficientes de los bancos. Espera unos minutos."
    mejor = solidas[0]
    texto = (f"La mejor modalidad ahora: comprar con {mejor['compra_nombre']} y vender con {mejor['venta_nombre']} "
             f"({mejor['neto_pct']:+.2f} % por vuelta).")
    if mejor["ruta"] == RUTA_INTERBANCARIA and mejor["compra_banco"] != mejor["venta_banco"]:
        texto += " Mover el dinero entre tus dos cuentas cuesta interbancario; ya está descontado."
    if actual is None:
        return texto
    if actual["compra_banco"] == mejor["compra_banco"] and actual["venta_banco"] == mejor["venta_banco"]:
        return texto + " Es la que tienes elegida."
    dif = mejor["neto_pct"] - actual["neto_pct"]
    return (texto + f" La tuya (compras con {actual['compra_nombre']}, vendes con {actual['venta_nombre']}) rinde "
            f"{actual['neto_pct']:+.2f} %: cambiar mejoraría {dif:.2f} puntos.")
