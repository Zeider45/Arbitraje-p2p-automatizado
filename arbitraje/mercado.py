"""Escáner del mercado P2P de Binance (consulta pública, no requiere claves).

Convención de nombres, desde TU punto de vista:
  - precio_compra: lo más barato a lo que puedes COMPRAR USDT (el mejor anuncio de venta).
  - precio_venta:  lo más caro a lo que puedes VENDER USDT (el mejor anuncio de compra).
Normalmente precio_compra > precio_venta; esa brecha es el spread que se captura
publicando anuncios en ambos lados.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field, replace

import httpx

from .config import RUTA_BDV, RUTA_INTERBANCARIA, Config, Costos, Mercado
from .utilidades import ahora_ms

RUTA_BUSQUEDA = "/bapi/c2c/v2/friendly/c2c/adv/search"
RUTA_FILTROS = "/bapi/c2c/v2/public/c2c/adv/filter-conditions"
FILAS_POR_PAGINA = 20  # máximo que acepta Binance en esta consulta
CABECERAS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36",
    "clienttype": "web",
    "lang": "es",
}
COMPRAR = "BUY"  # anuncios donde puedes comprar USDT (vendedores)
VENDER = "SELL"  # anuncios donde puedes vender USDT (compradores)


class ErrorMercado(Exception):
    """Binance P2P respondió algo inesperado."""


@dataclass
class Anuncio:
    numero: str
    precio: float
    disponible: float  # USDT disponibles en el anuncio
    min_bs: float
    max_bs: float
    metodos: list[str]
    anunciante: str
    ordenes_mes: int
    tasa_completado: float  # de 0 a 1
    comerciante: bool


@dataclass
class Cotizacion:
    ts_ms: int
    precio_compra: float | None
    precio_venta: float | None
    spread_pct: float | None = None
    neto_anunciante_pct: float | None = None
    neto_tomador_pct: float | None = None
    anuncios_compra: int = 0
    anuncios_venta: int = 0
    mejores_compra: list[Anuncio] = field(default_factory=list)
    mejores_venta: list[Anuncio] = field(default_factory=list)

    def como_fila(self) -> dict:
        fila = {k: v for k, v in asdict(self).items() if not k.startswith("mejores_")}
        fila["detalle"] = json.dumps(
            {"compra": [asdict(a) for a in self.mejores_compra], "venta": [asdict(a) for a in self.mejores_venta]},
            ensure_ascii=False,
        )
        return fila


def cuerpo_busqueda(cfg: Mercado, lado: str, pagina: int = 1) -> dict:
    cuerpo = {
        "asset": cfg.activo,
        "fiat": cfg.fiat,
        "tradeType": lado,
        "page": pagina,
        "rows": FILAS_POR_PAGINA,
        "payTypes": list(cfg.metodos_pago),
        "publisherType": "merchant" if cfg.solo_comerciantes else None,
        "countries": [],
        "proMerchantAds": False,
        "shieldMerchantAds": False,
        "filterType": "all",
        "periods": [],
        "additionalKycVerifyFilter": 0,
        "classifies": ["mass", "profession", "fiat_trade"],
    }
    if cfg.monto_bs > 0:
        # Binance devuelve solo anuncios que aceptan este monto
        cuerpo["transAmount"] = str(round(cfg.monto_bs, 2)).rstrip("0").rstrip(".")
    return cuerpo


def parsear_anuncios(datos: dict) -> list[Anuncio]:
    if not isinstance(datos, dict):
        raise ErrorMercado("respuesta inesperada de Binance P2P")
    if datos.get("success") is False or datos.get("code") not in (None, "000000"):
        detalle = datos.get("message") or datos.get("messageDetail") or f"código {datos.get('code')}"
        raise ErrorMercado(f"Binance P2P rechazó la consulta: {detalle}")
    anuncios = []
    for item in datos.get("data") or []:
        adv = item.get("adv") or {}
        anunciante = item.get("advertiser") or {}
        try:
            anuncios.append(
                Anuncio(
                    numero=str(adv.get("advNo") or ""),
                    precio=float(adv["price"]),
                    disponible=float(adv.get("surplusAmount") or adv.get("tradableQuantity") or 0),
                    min_bs=float(adv.get("minSingleTransAmount") or 0),
                    max_bs=float(adv.get("dynamicMaxSingleTransAmount") or adv.get("maxSingleTransAmount") or 0),
                    metodos=[
                        m.get("identifier") or m.get("tradeMethodName") or "?" for m in adv.get("tradeMethods") or []
                    ],
                    anunciante=anunciante.get("nickName") or "?",
                    ordenes_mes=int(anunciante.get("monthOrderCount") or 0),
                    tasa_completado=float(anunciante.get("monthFinishRate") or 0),
                    comerciante=anunciante.get("userType") == "merchant",
                )
            )
        except (KeyError, TypeError, ValueError):
            continue  # anuncio incompleto: se ignora
    return anuncios


def filtrar(anuncios: list[Anuncio], cfg: Mercado, propios: set[str] | frozenset[str] = frozenset()) -> list[Anuncio]:
    """Descarta anuncios poco confiables, los tuyos y los que no aceptan tu monto.

    `propios` son los números de tus anuncios: sin esto el bot de anuncios competiría
    contra sí mismo y subiría el precio en cada vuelta.
    """
    ignorar = {a.lower() for a in cfg.ignorar_apodos}
    tasa_minima = cfg.min_tasa_completado / 100
    validos = []
    for a in anuncios:
        if a.anunciante.lower() in ignorar or (a.numero and a.numero in propios):
            continue
        if a.ordenes_mes < cfg.min_ordenes_mes or a.tasa_completado < tasa_minima:
            continue
        if cfg.solo_comerciantes and not a.comerciante:
            continue
        if cfg.monto_bs > 0:
            if (a.min_bs and cfg.monto_bs < a.min_bs) or (a.max_bs and cfg.monto_bs > a.max_bs):
                continue
            if a.disponible * a.precio < cfg.monto_bs:
                continue
        validos.append(a)
    return validos


def calcular_spreads(
    precio_compra: float | None, precio_venta: float | None, costos: Costos, ruta: str = RUTA_INTERBANCARIA
) -> dict:
    """Ganancia estimada por cada USDT que das la vuelta, ya descontando costos.

    - neto_anunciante_pct: publicas un anuncio de compra 1 tick por encima del mejor
      comprador y uno de venta 1 tick por debajo del mejor vendedor (pagas comisión
      de anunciante en ambos lados).
    - neto_tomador_pct: compras al mejor vendedor y vendes al mejor comprador al
      instante. Casi siempre es negativo; si es positivo hay arbitraje inmediato.

    `ruta` indica cómo pagas tus compras: BDV -> BDV o interbancario (con comisión).
    """
    if not precio_compra or not precio_venta:
        return {"spread_pct": None, "neto_anunciante_pct": None, "neto_tomador_pct": None}
    ft = costos.comision_tomador_pct / 100
    neto_tomador = precio_venta / precio_compra * (1 - ft) / (1 + ft) * _factor_banco(costos, ruta) - 1
    return {
        "spread_pct": (precio_compra / precio_venta - 1) * 100,
        "neto_anunciante_pct": neto_entre_anuncios(
            precio_venta + costos.tick_bs, precio_compra - costos.tick_bs, costos, ruta
        ),
        "neto_tomador_pct": neto_tomador * 100,
    }


def _factor_banco(costos: Costos, ruta: str) -> float:
    return (1 - costos.costo_banco_venta_pct / 100) / (1 + costos.costo_compra_pct(ruta) / 100)


def neto_entre_anuncios(precio_anuncio_compra: float, precio_anuncio_venta: float, costos: Costos, ruta: str) -> float:
    """% neto por vuelta si se llenan tu anuncio de compra y tu anuncio de venta."""
    fa = costos.comision_anunciante_pct / 100
    bruto = precio_anuncio_venta / precio_anuncio_compra
    return (bruto * (1 - fa) / (1 + fa) * _factor_banco(costos, ruta) - 1) * 100


def netos_por_ruta(precio_compra: float | None, precio_venta: float | None, costos: Costos) -> dict:
    """Neto como anunciante pagando BDV -> BDV y pagando interbancario."""
    return {
        ruta.lower(): calcular_spreads(precio_compra, precio_venta, costos, ruta)["neto_anunciante_pct"]
        for ruta in (RUTA_BDV, RUTA_INTERBANCARIA)
    }


async def _pedir(http: httpx.AsyncClient, url: str, cuerpo: dict) -> dict:
    r = await http.post(url, json=cuerpo, headers=CABECERAS, timeout=15)
    if r.status_code != 200:
        raise ErrorMercado(f"Binance P2P respondió HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError as e:
        raise ErrorMercado("Binance P2P devolvió una respuesta que no es JSON") from e


async def buscar(http: httpx.AsyncClient, cfg: Config, lado: str, propios: frozenset[str] | set[str] = frozenset()) -> list[Anuncio]:
    """Anuncios válidos de un lado, del mejor precio al peor (excluye tus propios anuncios)."""
    url = cfg.binance.url_p2p + RUTA_BUSQUEDA
    todos: list[Anuncio] = []
    for pagina in (1, 2):
        pagina_anuncios = parsear_anuncios(await _pedir(http, url, cuerpo_busqueda(cfg.mercado, lado, pagina)))
        todos.extend(pagina_anuncios)
        if len(filtrar(todos, cfg.mercado, propios)) >= 3 or len(pagina_anuncios) < FILAS_POR_PAGINA:
            break
    validos = filtrar(todos, cfg.mercado, propios)
    validos.sort(key=lambda a: a.precio, reverse=(lado == VENDER))
    return validos


async def consultar(http: httpx.AsyncClient, cfg: Config, propios: frozenset[str] | set[str] = frozenset()) -> Cotizacion:
    compra, venta = await asyncio.gather(buscar(http, cfg, COMPRAR, propios), buscar(http, cfg, VENDER, propios))
    precio_compra = compra[0].precio if compra else None
    precio_venta = venta[0].precio if venta else None
    return Cotizacion(
        ts_ms=ahora_ms(),
        precio_compra=precio_compra,
        precio_venta=precio_venta,
        **calcular_spreads(precio_compra, precio_venta, cfg.costos, cfg.banco.ruta_por_defecto),
        anuncios_compra=len(compra),
        anuncios_venta=len(venta),
        mejores_compra=compra[:5],
        mejores_venta=venta[:5],
    )


async def listar_metodos(http: httpx.AsyncClient, cfg: Config) -> list[tuple[str, str]]:
    """Identificadores de métodos de pago para la moneda fiat: [(identificador, nombre)]."""
    try:
        datos = await _pedir(http, cfg.binance.url_p2p + RUTA_FILTROS, {"fiat": cfg.mercado.fiat})
        metodos = {
            m["identifier"]: m.get("tradeMethodName") or m["identifier"]
            for m in (datos.get("data") or {}).get("tradeMethods") or []
            if m.get("identifier")
        }
        if metodos:
            return sorted(metodos.items())
    except (httpx.HTTPError, ErrorMercado, AttributeError, TypeError):
        pass
    # Alternativa: reunir los métodos que aparecen en los anuncios publicados ahora mismo
    sin_filtro = replace(cfg.mercado, metodos_pago=[], monto_bs=0.0)
    vistos: dict[str, str] = {}
    for lado in (COMPRAR, VENDER):
        for pagina in (1, 2, 3):
            datos = await _pedir(http, cfg.binance.url_p2p + RUTA_BUSQUEDA, cuerpo_busqueda(sin_filtro, lado, pagina))
            for item in datos.get("data") or []:
                for m in (item.get("adv") or {}).get("tradeMethods") or []:
                    if m.get("identifier"):
                        vistos[m["identifier"]] = m.get("tradeMethodName") or m["identifier"]
    return sorted(vistos.items())
