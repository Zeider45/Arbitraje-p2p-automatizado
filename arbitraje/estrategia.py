"""Estrategia de operación: UN solo lugar con lo que decides (bancos, monto, margen) y cómo se aplica a todo el sistema.

Qué eliges:
  - banco_compra / banco_venta: con qué cuenta compras USDT y con cuál los vendes (pueden ser el mismo).
  - monto_bs: cuánto mueves por orden (0 = sin filtro). El mercado se mira solo con anuncios que lo aceptan.
  - margen_minimo_pct: lo mínimo que debe dejarte cada vuelta para publicar.
  - monto_auto: que el sistema escoja solo el mejor monto.

Qué cambia al aplicarla: qué anuncios se buscan, cómo se calculan los costos, qué métodos de pago llevan tus
anuncios, el banco por defecto de los pagos y los límites por orden de tus anuncios.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from .bancos import BANCOS, CODIGOS, ruta_modalidad
from .config import Config
from .utilidades import formato_numero

CLAVE_ESTADO = "estrategia"


@dataclass
class Estrategia:
    banco_compra: str = "BancoDeVenezuela"
    banco_venta: str = "BancoDeVenezuela"
    monto_bs: float = 0.0
    margen_minimo_pct: float = 0.8
    monto_auto: bool = False

    @property
    def ruta(self) -> str:
        return ruta_modalidad(self.banco_compra, self.banco_venta)

    @property
    def mismo_banco(self) -> bool:
        return self.banco_compra == self.banco_venta

    def describir(self) -> str:
        c, v = BANCOS.get(self.banco_compra, self.banco_compra), BANCOS.get(self.banco_venta, self.banco_venta)
        modalidad = f"compras y vendes con {c}" if self.mismo_banco else f"compras con {c} y vendes con {v}"
        monto = f"Bs {formato_numero(self.monto_bs, 0)} por orden" if self.monto_bs else "cualquier monto"
        return f"{modalidad} · {monto} · margen mínimo {self.margen_minimo_pct:g} %"


def inicial(cfg: Config) -> Estrategia:
    """La estrategia que da config.toml (se usa mientras no hayas elegido otra en el panel)."""
    banco = cfg.mercado.mi_banco if cfg.mercado.mi_banco in BANCOS else "BancoDeVenezuela"
    return Estrategia(
        banco_compra=cfg.mercado.banco_compra or banco, banco_venta=cfg.mercado.banco_venta or banco,
        monto_bs=cfg.mercado.monto_bs, margen_minimo_pct=cfg.anuncios.margen_minimo_pct,
    )


def cargar(db, cfg: Config) -> Estrategia:
    """Lee la estrategia guardada; si no hay, usa la del config (y migra las claves sueltas de versiones anteriores)."""
    base = inicial(cfg)
    crudo = db.leer_estado(CLAVE_ESTADO)
    if crudo:
        try:
            datos = json.loads(crudo)
            validos = {k: v for k, v in datos.items() if k in asdict(base)}
            e = Estrategia(**{**asdict(base), **validos})
            if e.banco_compra in BANCOS and e.banco_venta in BANCOS:
                return e
        except (ValueError, TypeError):
            pass
    viejo = db.leer_estado("mi_banco")   # versiones anteriores guardaban cada cosa por separado
    if viejo in BANCOS:
        base.banco_compra = base.banco_venta = viejo
    if db.leer_estado("monto_objetivo_bs"):
        base.monto_bs = float(db.leer_estado("monto_objetivo_bs"))
    base.monto_auto = db.leer_estado("monto_auto") == "1"
    return base


def guardar(db, e: Estrategia) -> None:
    db.escribir_estado(CLAVE_ESTADO, json.dumps(asdict(e)))


def aplicar(cfg: Config, e: Estrategia) -> None:
    """Pone la estrategia a trabajar en todo el sistema."""
    m, a = cfg.mercado, cfg.anuncios
    m.mi_banco = e.banco_compra            # desde donde pagas: así se detecta qué compras fueron "mismo banco"
    m.banco_compra, m.banco_venta = e.banco_compra, e.banco_venta
    m.metodos_pago = [e.banco_compra] if e.mismo_banco else [e.banco_compra, e.banco_venta]
    m.monto_bs = e.monto_bs
    a.metodos_compra, a.metodos_venta = [e.banco_compra], [e.banco_venta]
    a.ruta = cfg.banco.ruta_por_defecto = e.ruta
    a.margen_minimo_pct = e.margen_minimo_pct
    a.min_bs = round(e.monto_bs * 0.5, -3) if e.monto_bs else 0.0
    a.max_bs = e.monto_bs
    cfg.pagos.banco_por_defecto = CODIGOS.get(e.banco_compra, cfg.pagos.banco_por_defecto)
