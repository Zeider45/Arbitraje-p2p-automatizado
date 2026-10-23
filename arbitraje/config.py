"""Configuración: config.toml + variables de entorno para los secretos."""
from __future__ import annotations

import logging
import os
import re
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)

# Ruta de un pago saliente: entre cuentas BDV o hacia otro banco
RUTA_BDV = "BDV"
RUTA_INTERBANCARIA = "INTERBANCARIO"
RUTAS = (RUTA_BDV, RUTA_INTERBANCARIA)


@dataclass
class General:
    zona_horaria: str = "America/Caracas"
    base_datos: str = "datos/arbitraje.db"
    capital_usdt: float = 1000.0


@dataclass
class Binance:
    api_key: str = ""
    api_secret: str = ""
    dias_historial: int = 30
    intervalo_sync_min: float = 5.0
    url_api: str = "https://api.binance.com"
    url_p2p: str = "https://p2p.binance.com"

    @property
    def tiene_claves(self) -> bool:
        return bool(self.api_key and self.api_secret)


@dataclass
class Mercado:
    activo: str = "USDT"
    fiat: str = "VES"
    metodos_pago: list[str] = field(default_factory=lambda: ["BancoDeVenezuela", "PagoMovil"])
    monto_bs: float = 0.0
    min_ordenes_mes: int = 50
    min_tasa_completado: float = 90.0  # en %, Binance la publica de 0 a 1
    solo_comerciantes: bool = False
    ignorar_apodos: list[str] = field(default_factory=list)
    intervalo_seg: float = 60.0


@dataclass
class Costos:
    comision_anunciante_pct: float = 0.25
    comision_tomador_pct: float = 0.0
    costo_mismo_banco_pct: float = 0.0  # BDV -> BDV
    costo_interbancario_pct: float = 0.30  # BDV -> otro banco (transferencia o Pago Móvil)
    costo_interbancario_min_bs: float = 14.0
    costo_banco_venta_pct: float = 0.0  # recibir pagos normalmente no cuesta
    tick_bs: float = 0.01

    def costo_compra_pct(self, ruta: str) -> float:
        return self.costo_mismo_banco_pct if ruta == RUTA_BDV else self.costo_interbancario_pct


@dataclass
class Anuncios:
    automatico: bool = False          # el repositor de precios actúa solo
    modo: str = "simular"             # simular (no toca Binance) | manual (te avisa y tú editas) | real
    aviso_cambio_pct: float = 0.1     # modo manual: avisa si el precio sugerido cambia más de este %
    aviso_cada_min: float = 10.0      # modo manual: mínimo de minutos entre avisos del mismo lado
    margen_minimo_pct: float = 0.8    # neto mínimo como anunciante para seguir publicado
    desfase_tick: int = 1             # cuántos ticks mejoras al mejor rival
    intervalo_seg: float = 45.0
    reponer_segundos: float = 0.0     # republica cada N s aunque el precio no cambie (0 = no)
    lados: list[str] = field(default_factory=lambda: ["COMPRA", "VENTA"])
    monto_compra_usdt: float = 0.0    # tamaño del anuncio de compra (0 = capital)
    monto_venta_usdt: float = 0.0     # tamaño del anuncio de venta (0 = capital)
    min_bs: float = 0.0
    max_bs: float = 0.0
    pay_time_limit: int = 15
    classify: str = "mass"
    pay_ids_venta: list[int] = field(default_factory=list)   # payId de tus métodos (anuncios de VENTA)
    metodos_compra: list[str] = field(default_factory=lambda: ["BancoDeVenezuela", "PagoMovil"])
    ruta: str = "BDV"                 # ruta bancaria para calcular el margen del anuncio

    @property
    def en_real(self) -> bool:
        return self.automatico and self.modo == "real"


@dataclass
class Alertas:
    spread_minimo_pct: float = 1.0
    enfriamiento_min: float = 30.0
    resumen_diario_hora: str = "21:00"
    telegram_token: str = ""
    telegram_chat_id: str = ""
    whatsapp_telefono: str = ""      # tu número con código de país, sin "+": 584141234567
    whatsapp_apikey: str = ""        # la que te manda CallMeBot


@dataclass
class Pagos:
    enviar_telegram: bool = True       # manda el bloque "Pegar Datos" por Telegram
    banco_por_defecto: str = "0102"    # 0102 = Banco de Venezuela
    plantilla: str = ""                # vacío = plantilla por defecto (ver pagos.py)
    concepto_por_defecto: str = "Pago USDT orden {orden}"
    automatico: bool = True            # prepara y envía solo el bloque de pago de cada compra nueva
    ventana_orden_min: float = 120.0   # solo órdenes creadas en los últimos N min (evita avisar de las viejas)


@dataclass
class Banco:
    ruta_por_defecto: str = "interbancario"  # cómo pagaste una compra si la orden no lo dice
    limite_bdv_bs: float = 0.0  # 0 = sin límite (entre cuentas BDV)
    limite_interbancario_bs: float = 2_500_000.0
    webhook_token: str = ""
    tolerancia_bs: float = 1.0
    ventana_min: float = 180.0
    patron_entrada: str = ""
    patron_salida: str = ""
    patron_monto: str = ""
    patron_referencia: str = ""

    def limites(self) -> dict[str, float]:
        return {RUTA_BDV: self.limite_bdv_bs, RUTA_INTERBANCARIA: self.limite_interbancario_bs}


@dataclass
class Panel:
    host: str = "127.0.0.1"
    puerto: int = 8000
    usuario: str = "admin"
    clave: str = ""
    simulacion: bool = True       # ofrece el modo Simulación aparte en /sim (datos de ejemplo, sin tocar Binance)


@dataclass
class Config:
    general: General = field(default_factory=General)
    binance: Binance = field(default_factory=Binance)
    mercado: Mercado = field(default_factory=Mercado)
    costos: Costos = field(default_factory=Costos)
    anuncios: Anuncios = field(default_factory=Anuncios)
    pagos: Pagos = field(default_factory=Pagos)
    alertas: Alertas = field(default_factory=Alertas)
    banco: Banco = field(default_factory=Banco)
    panel: Panel = field(default_factory=Panel)
    demo: bool = False
    precios_reales: bool = False   # simulación con el mercado P2P real (consulta pública, sin claves)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.general.zona_horaria)


SECCIONES = ("general", "binance", "mercado", "costos", "anuncios", "pagos", "alertas", "banco", "panel")

# variable de entorno -> (sección, opción). Útil en Docker o en un VPS.
VARIABLES_ENTORNO = {
    "BINANCE_API_KEY": ("binance", "api_key"),
    "BINANCE_API_SECRET": ("binance", "api_secret"),
    "TELEGRAM_TOKEN": ("alertas", "telegram_token"),
    "TELEGRAM_CHAT_ID": ("alertas", "telegram_chat_id"),
    "WHATSAPP_TELEFONO": ("alertas", "whatsapp_telefono"),
    "WHATSAPP_APIKEY": ("alertas", "whatsapp_apikey"),
    "WEBHOOK_TOKEN": ("banco", "webhook_token"),
    "PANEL_HOST": ("panel", "host"),
    "PANEL_PUERTO": ("panel", "puerto"),
    "PANEL_USUARIO": ("panel", "usuario"),
    "PANEL_CLAVE": ("panel", "clave"),
    "ARBITRAJE_DB": ("general", "base_datos"),
}


def _convertir(valor, actual):
    """Ajusta el tipo de un valor leído al tipo de la opción por defecto."""
    if isinstance(actual, bool):
        if isinstance(valor, str):
            return valor.strip().lower() in ("1", "true", "si", "sí", "yes")
        return bool(valor)
    if isinstance(actual, int):
        return int(valor)
    if isinstance(actual, float):
        return float(valor)
    if isinstance(actual, list):
        if isinstance(valor, str):
            return [v.strip() for v in valor.split(",") if v.strip()]
        return [str(v) for v in valor]
    return str(valor)


def cargar(ruta: str | os.PathLike | None = "config.toml", demo: bool = False) -> Config:
    cfg = Config(demo=demo)
    datos: dict = {}
    if ruta and Path(ruta).exists():
        with open(ruta, "rb") as f:
            datos = tomllib.load(f)
    elif ruta:
        log.warning("No encontré %s: uso la configuración por defecto (copia config.example.toml).", ruta)

    for nombre in SECCIONES:
        seccion = getattr(cfg, nombre)
        validas = {f.name for f in fields(seccion)}
        for clave, valor in datos.get(nombre, {}).items():
            if clave not in validas:
                log.warning("Opción desconocida en [%s]: %s (se ignora)", nombre, clave)
                continue
            try:
                setattr(seccion, clave, _convertir(valor, getattr(seccion, clave)))
            except (TypeError, ValueError) as e:
                raise ValueError(f"Valor inválido para [{nombre}] {clave}: {valor!r}") from e

    for variable, (nombre, clave) in VARIABLES_ENTORNO.items():
        if os.environ.get(variable):
            seccion = getattr(cfg, nombre)
            setattr(seccion, clave, _convertir(os.environ[variable], getattr(seccion, clave)))

    if demo:
        # el modo demo nunca toca tu base de datos real ni Binance: solo simula
        cfg.general.base_datos = str(Path(cfg.general.base_datos).with_name("demo.db"))
        cfg.anuncios.automatico = True
        cfg.anuncios.modo = "simular"
    validar(cfg)
    return cfg


def validar(cfg: Config) -> None:
    try:
        ZoneInfo(cfg.general.zona_horaria)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(
            f"Zona horaria inválida: {cfg.general.zona_horaria} (en Windows instala el paquete 'tzdata')"
        ) from e
    if cfg.general.capital_usdt <= 0:
        raise ValueError("general.capital_usdt debe ser mayor que 0")
    if not cfg.mercado.metodos_pago:
        raise ValueError("mercado.metodos_pago no puede estar vacío")
    cfg.banco.ruta_por_defecto = cfg.banco.ruta_por_defecto.strip().upper()
    if cfg.banco.ruta_por_defecto not in RUTAS:
        raise ValueError('banco.ruta_por_defecto debe ser "bdv" o "interbancario"')
    cfg.anuncios.ruta = cfg.anuncios.ruta.strip().upper()
    if cfg.anuncios.ruta not in RUTAS:
        raise ValueError('anuncios.ruta debe ser "bdv" o "interbancario"')
    cfg.anuncios.modo = cfg.anuncios.modo.strip().lower()
    if cfg.anuncios.modo not in ("simular", "manual", "real"):
        raise ValueError('anuncios.modo debe ser "simular", "manual" o "real"')
    cfg.anuncios.lados = [l.strip().upper() for l in cfg.anuncios.lados if l.strip()]
    if any(l not in ("COMPRA", "VENTA") for l in cfg.anuncios.lados):
        raise ValueError('anuncios.lados solo admite "COMPRA" y "VENTA"')
    cfg.anuncios.intervalo_seg = max(cfg.anuncios.intervalo_seg, 15.0)
    cfg.anuncios.desfase_tick = max(cfg.anuncios.desfase_tick, 0)
    if cfg.alertas.resumen_diario_hora and not re.fullmatch(r"\d{1,2}:\d{2}", cfg.alertas.resumen_diario_hora):
        raise ValueError('alertas.resumen_diario_hora debe tener el formato "HH:MM" (o "" para desactivarlo)')
    cfg.alertas.whatsapp_telefono = re.sub(r"\D", "", cfg.alertas.whatsapp_telefono)
    cfg.binance.dias_historial = min(max(cfg.binance.dias_historial, 1), 180)
    # no saturar a Binance: mínimo 15 s entre consultas de mercado y 1 min entre sincronizaciones
    cfg.mercado.intervalo_seg = max(cfg.mercado.intervalo_seg, 15.0)
    cfg.binance.intervalo_sync_min = max(cfg.binance.intervalo_sync_min, 1.0)
