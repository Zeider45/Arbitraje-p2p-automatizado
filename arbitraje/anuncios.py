"""Repositor automático de anuncios en Binance P2P (lado anunciante del arbitraje).

Usa la API oficial "C2C Agent" de Binance (la misma del skill oficial de Binance):
  POST /sapi/v1/c2c/agent/ads/listWithPagination   -> tus anuncios
  POST /sapi/v1/c2c/agent/ads/getDetailByNo         -> detalle de un anuncio
  GET  /sapi/v1/c2c/agent/ads/getPayMethodByUserId  -> tus métodos de pago (payId)
  POST /sapi/v1/c2c/agent/ads/post                  -> publicar (escritura)
  POST /sapi/v1/c2c/agent/ads/update                -> actualizar (escritura, objeto completo)
  POST /sapi/v1/c2c/agent/ads/updateStatus          -> en línea / fuera de línea / cerrar (escritura)

Requiere una API key con permiso de escritura y cuenta de comerciante verificada.
Dos modos (anuncios.modo):
  - "simular": calcula y registra qué anuncio publicaría, pero NO toca Binance.
  - "real":    publica y mueve precios de verdad.
El repositor nunca publica por debajo de anuncios.margen_minimo_pct: si el mercado
se cierra, ensancha tus precios hasta conservar ese margen (aunque dejes de ser el
primero), en vez de perseguir al rival hacia una pérdida.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx

from .binance_api import ERRORES, ErrorBinance
from .config import Anuncios, Costos
from .mercado import Cotizacion, neto_entre_anuncios
from .utilidades import ahora_ms

log = logging.getLogger(__name__)

RUTA_LISTAR = "/sapi/v1/c2c/agent/ads/listWithPagination"
RUTA_DETALLE = "/sapi/v1/c2c/agent/ads/getDetailByNo"
RUTA_METODOS_USUARIO = "/sapi/v1/c2c/agent/ads/getPayMethodByUserId"
RUTA_PUBLICAR = "/sapi/v1/c2c/agent/ads/post"
RUTA_ACTUALIZAR = "/sapi/v1/c2c/agent/ads/update"
RUTA_ESTADO = "/sapi/v1/c2c/agent/ads/updateStatus"
USER_AGENT = "arbitraje-p2p/1.0 (panel)"

TRADE_TYPE = {"COMPRA": "0", "VENTA": "1"}  # Binance: 0 = BUY, 1 = SELL
EN_LINEA, FUERA_LINEA, CERRADO = 1, 3, 4
ESTADO_TEXTO = {EN_LINEA: "En línea", FUERA_LINEA: "Fuera de línea", CERRADO: "Cerrado"}


@dataclass
class PlanLado:
    """Lo que el repositor quiere que sea tu anuncio de un lado (COMPRA o VENTA)."""
    lado: str
    publicar: bool
    precio: float | None = None
    competitivo: bool = False  # True si quedas de primero
    motivo: str = ""


@dataclass
class Plan:
    compra: PlanLado | None = None
    venta: PlanLado | None = None
    margen_pct: float | None = None   # margen entre los dos precios propuestos
    competitivo: bool = False         # lideras ambos lados y aun así das el margen mínimo

    def lado(self, lado: str) -> PlanLado | None:
        return self.compra if lado == "COMPRA" else self.venta


def _ratio_requerido(costos: Costos, cfg: Anuncios) -> float:
    """Relación precio_venta / precio_compra necesaria para el margen mínimo."""
    fa = costos.comision_anunciante_pct / 100
    banco = (1 - costos.costo_banco_venta_pct / 100) / (1 + costos.costo_compra_pct(cfg.ruta) / 100)
    return (1 + cfg.margen_minimo_pct / 100) / ((1 - fa) / (1 + fa) * banco)


def planear(cotizacion: Cotizacion, costos: Costos, cfg: Anuncios) -> Plan:
    """Decide los precios de tus anuncios a partir del mercado actual (función pura).

    - Precio competitivo de COMPRA = mejor comprador + k ticks (quedas de primero comprando).
    - Precio competitivo de VENTA  = mejor vendedor  − k ticks (quedas de primero vendiendo).
    Si con esos precios el margen neto cae por debajo del mínimo, ensancha ambos
    alrededor de su punto medio geométrico hasta recuperar justo el margen mínimo.
    """
    pc, pv = cotizacion.precio_compra, cotizacion.precio_venta  # pc: comprar USDT · pv: vender USDT
    plan = Plan()
    if not pc or not pv:
        motivo = "Sin anuncios de referencia en el mercado todavía."
        plan.compra = PlanLado("COMPRA", False, motivo=motivo)
        plan.venta = PlanLado("VENTA", False, motivo=motivo)
        return plan

    desfase = cfg.desfase_tick * costos.tick_bs
    compra_comp = pv + desfase   # tu anuncio de compra, 1 tick sobre el mejor comprador
    venta_comp = pc - desfase    # tu anuncio de venta, 1 tick bajo el mejor vendedor
    margen_comp = neto_entre_anuncios(compra_comp, venta_comp, costos, cfg.ruta)

    if margen_comp >= cfg.margen_minimo_pct and venta_comp > compra_comp:
        precio_compra, precio_venta, competitivo = compra_comp, venta_comp, True
    else:
        # ensancha simétricamente alrededor del punto medio geométrico hasta el margen mínimo
        medio = math.sqrt(compra_comp * venta_comp)
        raiz = math.sqrt(_ratio_requerido(costos, cfg))
        precio_compra = round(medio / raiz, 2)
        precio_venta = round(medio * raiz, 2)
        competitivo = False

    margen = neto_entre_anuncios(precio_compra, precio_venta, costos, cfg.ruta)
    plan.margen_pct = margen
    plan.competitivo = competitivo and set(cfg.lados) <= {"COMPRA", "VENTA"}

    motivo = (
        "Lideras ambos lados con el margen mínimo." if competitivo
        else f"Mercado ajustado: ensancho para conservar {cfg.margen_minimo_pct:g} % de margen."
    )
    if "COMPRA" in cfg.lados:
        plan.compra = PlanLado("COMPRA", precio_compra < pc, round(precio_compra, 2), competitivo, motivo)
        if precio_compra >= pc:  # comprar por encima del mejor vendedor sería absurdo
            plan.compra.publicar = False
            plan.compra.motivo = "El precio con margen quedaría por encima del mercado; no publico compra."
    if "VENTA" in cfg.lados:
        plan.venta = PlanLado("VENTA", precio_venta > pv, round(precio_venta, 2), competitivo, motivo)
        if precio_venta <= pv:
            plan.venta.publicar = False
            plan.venta.motivo = "El precio con margen quedaría por debajo del mercado; no publico venta."
    return plan


@dataclass
class AnuncioVivo:
    numero: str
    lado: str
    precio: float
    estado: int
    asset: str
    fiat: str
    disponible: float


class ClienteAgente:
    """Cliente firmado para la API C2C Agent de Binance (escritura de anuncios)."""

    def __init__(self, api_key: str, api_secret: str, url_base: str, http: httpx.AsyncClient):
        self.api_key = api_key
        self.api_secret = api_secret
        self.url_base = url_base.rstrip("/")
        self.http = http
        self._desfase_ms: int | None = None

    async def _hora(self) -> int:
        if self._desfase_ms is None:
            try:
                r = await self.http.get(f"{self.url_base}/api/v3/time", timeout=10)
                self._desfase_ms = int(r.json()["serverTime"]) - ahora_ms()
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                return ahora_ms()
        return ahora_ms() + self._desfase_ms

    async def _pedir(self, metodo: str, ruta: str, params: dict | None = None, cuerpo: dict | None = None) -> dict:
        # La firma SAPI NO ordena los parámetros: se respeta el orden de inserción.
        params = {**(params or {}), "timestamp": await self._hora(), "recvWindow": 10_000}
        consulta = urlencode(params)
        firma = hmac.new(self.api_secret.encode(), consulta.encode(), hashlib.sha256).hexdigest()
        url = f"{self.url_base}{ruta}?{consulta}&signature={firma}"
        cabeceras = {"X-MBX-APIKEY": self.api_key, "User-Agent": USER_AGENT, "Content-Type": "application/json"}
        r = await self.http.request(metodo, url, headers=cabeceras, content=json.dumps(cuerpo) if cuerpo is not None else None, timeout=20)
        try:
            datos = r.json()
        except ValueError:
            datos = {}
        if r.status_code != 200:
            codigo = datos.get("code") if isinstance(datos, dict) else None
            if codigo == -1021:
                self._desfase_ms = None
            raise ErrorBinance(ERRORES.get(codigo) or (datos.get("msg") or datos.get("message") or f"HTTP {r.status_code}"))
        if isinstance(datos, dict) and datos.get("success") is False:
            raise ErrorBinance(datos.get("message") or f"código {datos.get('code')}")
        return datos

    async def listar(self) -> list[AnuncioVivo]:
        datos = await self._pedir("POST", RUTA_LISTAR, cuerpo={"page": 1, "rows": 20})
        vivos = []
        for a in datos.get("data") or []:
            vivos.append(
                AnuncioVivo(
                    numero=str(a.get("advNo")),
                    lado="COMPRA" if str(a.get("tradeType")).upper() in ("BUY", "0") else "VENTA",
                    precio=float(a.get("price") or 0),
                    estado=int(a.get("advStatus") or 0),
                    asset=a.get("asset") or "",
                    fiat=a.get("fiatUnit") or a.get("fiat") or "",
                    disponible=float(a.get("surplusAmount") or 0),
                )
            )
        return vivos

    async def detalle(self, numero: str) -> dict:
        datos = await self._pedir("POST", RUTA_DETALLE, params={"advNo": numero})
        return datos.get("data") or {}

    async def metodos_pago(self) -> list[dict]:
        datos = await self._pedir("GET", RUTA_METODOS_USUARIO)
        return datos.get("data") or []

    async def publicar(self, cuerpo: dict) -> str:
        datos = await self._pedir("POST", RUTA_PUBLICAR, cuerpo=cuerpo)
        return str(datos.get("data") or "")

    async def actualizar_precio(self, numero: str, precio: float) -> bool:
        # Binance valida el objeto completo: traemos el detalle, cambiamos el precio y lo reenviamos.
        d = await self.detalle(numero)
        if not d:
            raise ErrorBinance(f"No pude leer el anuncio {numero}")
        cuerpo = {
            "advNo": numero,
            "priceType": d.get("priceType") or 1,
            "price": round(precio, 2),
            "initAmount": d.get("initAmount"),
            "maxSingleTransAmount": d.get("maxSingleTransAmount"),
            "minSingleTransAmount": d.get("minSingleTransAmount"),
            "payTimeLimit": d.get("payTimeLimit"),
            "buyerKycLimit": d.get("buyerKycLimit"),
            "remarks": d.get("remarks"),
            "autoReplyMsg": d.get("autoReplyMsg"),
            "tradeMethods": [
                {k: m[k] for k in ("payId", "identifier", "payType") if m.get(k) is not None}
                for m in d.get("tradeMethods") or []
            ],
            "advStatus": EN_LINEA,
        }
        datos = await self._pedir("POST", RUTA_ACTUALIZAR, cuerpo={k: v for k, v in cuerpo.items() if v is not None})
        return bool(datos.get("data", True))

    async def cambiar_estado(self, numeros: list[str], estado: int) -> dict:
        return await self._pedir("POST", RUTA_ESTADO, cuerpo={"advNos": numeros, "advStatus": estado})
