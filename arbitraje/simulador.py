"""Simulador de ciclos: proyecta cuánto rendiría tu capital dando N vueltas de arbitraje.

Una vuelta = comprar USDT barato y venderlo caro (o al revés, según con qué moneda
empieces). Se calcula con los mismos costos de config.toml (comisión de Binance y costo
del banco), así que lo que ves aquí cuadra con lo que mide el panel con órdenes reales.
No toca la base de datos ni Binance: es solo aritmética.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from .config import RUTA_BDV, Costos
from .utilidades import DIA_MS, HORA_MS, MINUTO_MS

ROLES = ("anunciante", "tomador")
MAX_CICLOS = 1000


@dataclass
class Ciclo:
    n: int
    dia: int
    capital_inicio: float
    capital_fin: float
    ganancia: float
    ganancia_pct: float
    acumulada: float


def precios_sugeridos(cotizacion: dict | None, costos: Costos, rol: str) -> tuple[float | None, float | None]:
    """(precio al que compras, precio al que vendes) según el mercado actual y tu rol.

    Anunciante: te pones un tick por delante de cada rival. Tomador: aceptas el mejor anuncio.
    """
    if not cotizacion or not cotizacion.get("precio_compra") or not cotizacion.get("precio_venta"):
        return None, None
    mejor_compra, mejor_venta = cotizacion["precio_compra"], cotizacion["precio_venta"]
    if rol == "anunciante":
        return mejor_venta + costos.tick_bs, mejor_compra - costos.tick_bs
    return mejor_compra, mejor_venta


def _costo_banco(bs: float, costos: Costos, ruta: str) -> float:
    pct = costos.costo_compra_pct(ruta)
    costo = bs * pct / 100
    return max(costo, costos.costo_interbancario_min_bs) if ruta != RUTA_BDV and pct > 0 else costo


def una_vuelta(capital: float, moneda: str, p_compra: float, p_venta: float, costos: Costos, ruta: str, rol: str) -> float:
    """Capital (en la misma moneda inicial) tras comprar y vender una vez."""
    fee = (costos.comision_anunciante_pct if rol == "anunciante" else costos.comision_tomador_pct) / 100
    recibido = 1 - costos.costo_banco_venta_pct / 100
    if moneda == "VES":
        usdt = (capital - _costo_banco(capital, costos, ruta)) / (p_compra * (1 + fee))
        return usdt * p_venta * (1 - fee) * recibido
    bs = capital * p_venta * (1 - fee) * recibido
    return (bs - _costo_banco(bs, costos, ruta)) / (p_compra * (1 + fee))


def simular(
    capital: float, moneda: str, ciclos: int, p_compra: float, p_venta: float, costos: Costos,
    ruta: str = RUTA_BDV, rol: str = "anunciante", reinvertir: bool = True, ciclos_por_dia: float = 1.0,
    limite_interbancario_bs: float = 0.0,
) -> dict:
    """Proyecta `ciclos` vueltas. Con `reinvertir` la ganancia se suma al capital (interés compuesto)."""
    if capital <= 0 or not 1 <= ciclos <= MAX_CICLOS:
        raise ValueError(f"capital debe ser > 0 y ciclos entre 1 y {MAX_CICLOS}")
    if moneda not in ("VES", "USDT") or rol not in ROLES or p_compra <= 0 or p_venta <= 0 or ciclos_por_dia <= 0:
        raise ValueError("parámetros inválidos")

    filas: list[Ciclo] = []
    actual, acumulada = capital, 0.0
    for n in range(1, ciclos + 1):
        base = actual if reinvertir else capital
        fin = una_vuelta(base, moneda, p_compra, p_venta, costos, ruta, rol)
        ganancia = fin - base
        acumulada += ganancia
        if reinvertir:
            actual = fin
        filas.append(Ciclo(
            n=n, dia=int((n - 1) // ciclos_por_dia) + 1, capital_inicio=base, capital_fin=base + ganancia,
            ganancia=ganancia, ganancia_pct=ganancia / base * 100, acumulada=acumulada,
        ))

    capital_final = capital + acumulada
    primero = filas[0]
    usdt_ref = p_venta if moneda == "VES" else 1.0  # para expresar la ganancia también en USDT
    avisos = []
    if primero.ganancia <= 0:
        avisos.append("Con estos precios y costos cada vuelta pierde dinero: no hay arbitraje.")
    if ruta != RUTA_BDV and limite_interbancario_bs > 0:
        bs_por_vuelta = max(f.capital_inicio for f in filas) * (1 if moneda == "VES" else p_venta)
        if bs_por_vuelta * ciclos_por_dia > limite_interbancario_bs:
            avisos.append(
                f"Moverías ~Bs {bs_por_vuelta * ciclos_por_dia:,.0f} al día por interbancario y tu límite diario es "
                f"Bs {limite_interbancario_bs:,.0f}: no podrías completar todas las vueltas."
            )
    return {
        "moneda": moneda,
        "capital_inicial": capital,
        "capital_final": capital_final,
        "ganancia": acumulada,
        "ganancia_usdt": acumulada / usdt_ref,
        "rendimiento_pct": acumulada / capital * 100,
        "ganancia_por_ciclo_pct": primero.ganancia_pct,
        "ciclos": ciclos,
        "dias": filas[-1].dia,
        "precio_compra": p_compra,
        "precio_venta": p_venta,
        "spread_bruto_pct": (p_venta / p_compra - 1) * 100,
        "avisos": avisos,
        "filas": [asdict(f) for f in filas],
    }


def ordenes_de_ciclos(
    filas: Iterable[dict], moneda: str, p_compra: float, p_venta: float, costos: Costos, ruta: str, rol: str,
    ahora_ms: int, ciclos_por_dia: float = 1.0, prefijo: str = "SIM",
) -> list[dict]:
    """Convierte los ciclos simulados en órdenes completadas (compra + venta) para verlas en el panel.

    Se reparten hacia atrás desde ahora a razón de `ciclos_por_dia`, así los gráficos y el ROI reflejan la simulación.
    """
    filas = list(filas)
    fee = (costos.comision_anunciante_pct if rol == "anunciante" else costos.comision_tomador_pct) / 100
    paso = int(DIA_MS / ciclos_por_dia)
    ordenes = []
    for i, f in enumerate(filas):
        t1 = ahora_ms - 2 * HORA_MS - (len(filas) - 1 - i) * paso
        usdt = round(f["capital_inicio"] if moneda == "USDT" else f["capital_inicio"] / p_compra, 2)
        piernas = [("VENTA", p_venta, t1), ("COMPRA", p_compra, t1 + 20 * MINUTO_MS)]
        if moneda == "VES":
            piernas.reverse()
            piernas = [(piernas[0][0], piernas[0][1], t1), (piernas[1][0], piernas[1][1], t1 + 20 * MINUTO_MS)]
        for tipo, precio, ts in piernas:
            ordenes.append({
                "numero": f"{prefijo}{ahora_ms}{f['n']:04d}{tipo[0]}", "origen": "demo", "tipo": tipo,
                "cantidad": usdt, "precio": round(precio, 2), "total_bs": round(usdt * precio, 2),
                "comision": round(usdt * fee, 4), "ruta_pago": ruta if tipo == "COMPRA" else None,
                "metodo_pago": "BancoDeVenezuela" if tipo == "COMPRA" and ruta == RUTA_BDV else None,
                "estado": "COMPLETED", "rol": "MAKER" if rol == "anunciante" else "TAKER",
                "contraparte": "Simulado", "creada_ms": ts,
            })
    return ordenes
