"""Cliente firmado de la API de Binance, SOLO LECTURA, para el historial P2P (C2C).

Endpoint: GET /sapi/v1/c2c/orderMatch/listUserOrderHistory
  - máximo 30 días entre startTimestamp y endTimestamp
  - Binance solo guarda los últimos 6 meses
  - 100 filas por página como máximo
Basta una API key con el permiso "Enable Reading". Nunca actives retiros ni trading.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from urllib.parse import urlencode

import httpx

from .config import RUTA_BDV, RUTA_INTERBANCARIA, Config
from .db import BaseDatos
from .utilidades import DIA_MS, HORA_MS, ahora_ms

RUTA_HISTORIAL = "/sapi/v1/c2c/orderMatch/listUserOrderHistory"
MAX_VENTANA_MS = 30 * DIA_MS
MAX_HISTORIAL_DIAS = 180
FILAS_POR_PAGINA = 100
SOLAPAMIENTO_MS = 3 * DIA_MS  # se releen los últimos días para captar cambios de estado

ERRORES = {
    -2015: "API key inválida, IP no autorizada o sin permiso de lectura. Revisa la key y su lista de IPs.",
    -2014: "El formato de la API key no es válido.",
    -1022: "Firma inválida: revisa binance.api_secret.",
    -1021: "La hora de este equipo está desfasada con Binance; se corrige sola en el próximo intento.",
}


class ErrorBinance(Exception):
    """Binance rechazó la consulta."""


def firmar(consulta: str, secreto: str) -> str:
    return hmac.new(secreto.encode(), consulta.encode(), hashlib.sha256).hexdigest()


def explicar_error(estado_http: int, datos) -> str:
    codigo = datos.get("code") if isinstance(datos, dict) else None
    if codigo in ERRORES:
        return ERRORES[codigo]
    if estado_http == 451:
        return "Binance no da servicio desde la ubicación de este servidor (p. ej. EE. UU.): usa un VPS en otra región."
    if estado_http in (418, 429):
        return "Demasiadas consultas: Binance limitó temporalmente esta IP. Sube binance.intervalo_sync_min."
    mensaje = datos.get("msg") or datos.get("message") if isinstance(datos, dict) else None
    return f"HTTP {estado_http}: {mensaje or 'respuesta inesperada'}"


class ClienteBinance:
    def __init__(self, api_key: str, api_secret: str, url_base: str, http: httpx.AsyncClient):
        self.api_key = api_key
        self.api_secret = api_secret
        self.url_base = url_base.rstrip("/")
        self.http = http
        self._desfase_ms: int | None = None

    async def _hora_binance(self) -> int:
        if self._desfase_ms is None:
            try:
                r = await self.http.get(f"{self.url_base}/api/v3/time", timeout=10)
                self._desfase_ms = int(r.json()["serverTime"]) - ahora_ms()
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                return ahora_ms()
        return ahora_ms() + self._desfase_ms

    async def _get_firmado(self, ruta: str, params: dict) -> dict:
        params = {**params, "recvWindow": 10_000, "timestamp": await self._hora_binance()}
        consulta = urlencode(params)
        url = f"{self.url_base}{ruta}?{consulta}&signature={firmar(consulta, self.api_secret)}"
        r = await self.http.get(url, headers={"X-MBX-APIKEY": self.api_key}, timeout=20)
        try:
            datos = r.json()
        except ValueError:
            datos = {}
        if r.status_code != 200:
            if isinstance(datos, dict) and datos.get("code") == -1021:
                self._desfase_ms = None
            raise ErrorBinance(explicar_error(r.status_code, datos))
        return datos

    async def historial_c2c(self, tipo: str, desde_ms: int, hasta_ms: int) -> list[dict]:
        """Órdenes P2P de un lado ('BUY' = tus compras, 'SELL' = tus ventas) en una ventana <= 30 días."""
        resultado: list[dict] = []
        for pagina in range(1, 101):
            datos = await self._get_firmado(
                RUTA_HISTORIAL,
                {"tradeType": tipo, "startTimestamp": desde_ms, "endTimestamp": hasta_ms,
                 "page": pagina, "rows": FILAS_POR_PAGINA},
            )
            if datos.get("success") is False or str(datos.get("code", "000000")) != "000000":
                raise ErrorBinance(datos.get("message") or f"código {datos.get('code')}")
            filas = datos.get("data") or []
            resultado.extend(filas)
            total = int(datos.get("total") or 0)
            if len(filas) < FILAS_POR_PAGINA or len(resultado) >= total:
                break
        return resultado


def ventanas(desde_ms: int, hasta_ms: int, tam_ms: int = MAX_VENTANA_MS) -> list[tuple[int, int]]:
    """Parte un rango en tramos de como máximo `tam_ms` (Binance no acepta más de 30 días)."""
    tramos = []
    inicio = desde_ms
    while inicio < hasta_ms:
        fin = min(inicio + tam_ms - 1, hasta_ms)
        tramos.append((inicio, fin))
        inicio = fin + 1
    return tramos


def metodo_de_pago(o: dict) -> str | None:
    for clave in ("payMethodName", "payMethod", "tradeMethodName", "payType"):
        valor = o.get(clave)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    return None


def ruta_desde_metodo(metodo: str | None) -> str | None:
    """BDV si pagaste a una cuenta del Banco de Venezuela, interbancario si fue a otro banco."""
    if not metodo:
        return None
    normal = re.sub(r"[^a-z]", "", metodo.lower())
    if "bancodevenezuela" in normal or normal == "bdv":
        return RUTA_BDV
    if normal in ("bank", "banktransfer", "specificbank", "transferenciabancaria"):
        return None  # genérico: no dice a qué banco
    return RUTA_INTERBANCARIA


def convertir_orden(o: dict) -> dict:
    tipo = "COMPRA" if o["tradeType"] == "BUY" else "VENTA"
    metodo = metodo_de_pago(o)
    return {
        "numero": str(o["orderNumber"]),
        "origen": "binance",
        "tipo": tipo,
        "activo": o.get("asset") or "USDT",
        "fiat": o.get("fiat") or "VES",
        "cantidad": float(o["amount"]),
        "precio": float(o["unitPrice"]),
        "total_bs": float(o["totalPrice"]),
        "comision": float(o.get("commission") or 0),
        "estado": o.get("orderStatus") or "DESCONOCIDO",
        "rol": o.get("advertisementRole"),
        "contraparte": o.get("counterPartNickName"),
        "metodo_pago": metodo,
        "ruta_pago": ruta_desde_metodo(metodo) if tipo == "COMPRA" else None,
        "creada_ms": int(o["createTime"]),
        "crudo": json.dumps(o, ensure_ascii=False),
    }


async def sincronizar(db: BaseDatos, cfg: Config, cliente: ClienteBinance, ahora: int | None = None) -> tuple[int, int]:
    """Descarga tus órdenes P2P del par configurado. Devuelve (nuevas, actualizadas)."""
    hasta = ahora if ahora is not None else ahora_ms()
    anterior = db.leer_estado("sync_hasta_ms")
    if anterior:
        desde = int(anterior) - SOLAPAMIENTO_MS
    else:
        desde = hasta - cfg.binance.dias_historial * DIA_MS
    desde = max(desde, hasta - MAX_HISTORIAL_DIAS * DIA_MS + HORA_MS)
    filas = []
    for inicio, fin in ventanas(desde, hasta):
        for tipo in ("BUY", "SELL"):
            for o in await cliente.historial_c2c(tipo, inicio, fin):
                if (o.get("asset") or cfg.mercado.activo) == cfg.mercado.activo and (
                    o.get("fiat") or cfg.mercado.fiat
                ) == cfg.mercado.fiat:
                    filas.append(convertir_orden(o))
    resultado = db.guardar_ordenes(filas)
    db.escribir_estado("sync_hasta_ms", hasta)
    return resultado
